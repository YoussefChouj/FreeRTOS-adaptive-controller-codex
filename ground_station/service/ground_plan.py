"""Ground writes a `.fly` script asks for before takeoff: the layer target, the x/y reference, the MRAC prior.

The `.fly` verbs (``layers``, ``xy_layers``, ``mrac_ref``, ``mrac_prior``) compile to a list of ground writes that
the campaign runner sends on the ground, before IDLE, in order. Every write is idempotent and absolute, so a lost
reply can be resent (the lossy-link rule): the runner retries three times, then aborts the flight on the ground.

Each write is one of:

  {"kind": "variant", "axis": 0..5, "field": <CMD 0x1D field number>, "value": <float>}
      one CMD 0x1D MRAC_VARIANT write (the firmware refuses it while airborne).
  {"kind": "prior", "axis": <axis name>, "idx": <int>, "value": <float>}
      one CMD 0x2F..0x34 MRAC_PRIOR write (disarmed only).
  {"kind": "refuse", "reason": <str>, "field": <field name>}
      nothing is sent: the firmware lacks the field, so the flight is refused on the ground at run time, naming
      the field. It passes the parse, so the rest of the deck still flies (never drop a flight).

Where the field NUMBERS come from (never typed here):

  - the reference fields (ref_type, ref_model_bw, ref_delay_s, ref_model_zeta) and the x/y layer field
    (xy_layers) are in ``ground_station/analysis/mrac_variants.py`` (VARIANT_FIELDS), the host mirror of the
    firmware table API/mrac.c mrac_var_field;
  - the layer fields of the all-loop build (att_layers, yaw_layers, z_layers) come from the firmware contract
    (``ground_station/platform/firmware_contract.py``), which the firmware agent owns. A group whose field the
    contract does not carry yet is refused with "needs the all-loop flash" and nothing is sent.

Every field is looked up BY NAME: the contract is asked first, the host mirror answers for the fields the
contract does not name yet. The contract is read, never written: the firmware agent owns it.

The contract may name its layer fields in any of these shapes, all of which are read:

  - a module-level dict ``MRAC_LAYER_FIELDS`` or ``LAYER_FIELDS``: {"att": 33, "yaw": 34, "z": 35};
  - a mapping attribute on the contract object: ``mrac_layer_fields`` or ``layer_fields``;
  - a parameter of the 0x1D MRAC_VARIANT command spec named ``<group>_layers`` whose index is the field number.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

# Where the fitted reference models live (written by ground_station.analysis.mrac_ref_fit).
REF_FIT_DIR = Path("logs") / "ref_fit"

# The layer groups the `layers` verb accepts. `xy_layers` is the historical alias of `xy`.
LAYER_GROUPS = ("xy", "att", "yaw", "z")
LAYER_ALIASES = {"xy_layers": "xy"}

# How many of the last Theta samples the prior is the median of (mrac_prior.prior_from_columns).
PRIOR_LAST_N = 500

# The layer target values, as the firmware stores them (API/mrac.h MRAC_XY_LAYER_*): bit 1 inner, bit 2 outer.
LAYER_TARGETS: Mapping[str, int] = {"off": 0, "none": 0, "inner": 1, "outer": 2, "both": 3}

# The CMD 0x1D field numbers of the reference model, by name (the host mirror of the firmware table).
REF_FIELDS = ("ref_type", "ref_model_bw", "ref_delay_s", "ref_model_zeta")

# The names the reference fields may carry in the firmware, by the fit key. The firmware agent names the
# 2nd-order damping ``ref_zeta`` (the member it writes is ref_model_zeta), so both are looked up.
REF_FIELD_ALIASES: Mapping[str, tuple[str, ...]] = {
    "ref_type": ("ref_type",),
    "ref_model_bw": ("ref_model_bw",),
    "ref_delay_s": ("ref_delay_s",),
    "ref_model_zeta": ("ref_zeta", "ref_model_zeta"),
}

# The note a missing firmware field carries. The operator's rule (2026-10-24): a flight is never dropped
# because the firmware is behind, so this note names the firmware item that adds the field and the flight
# stays in the deck. The verb still fails loudly at run time: the flight does not take off with a field the
# firmware does not have.
NEEDS_ALL_LOOP = "needs mrac-all-loops item 0"


class GroundWriteError(ValueError):
    """A ground write the firmware would refuse, or one that cannot be built. Nothing was sent."""


# --------------------------------------------------------------------------- the layer fields


def _contract_module() -> Any:
    """The firmware contract MODULE (ground_station/platform/firmware_contract.py).

    ``ground_station.platform.firmware_contract`` is the contract INSTANCE (the package re-exports it as
    ``current``), so the module is fetched through importlib. The contract is read, never written.
    """
    import importlib

    return importlib.import_module("ground_station.platform.firmware_contract")


def contract_fields() -> dict[str, int]:
    """Every CMD 0x1D field the firmware contract names, as {field name: field number}.

    The contract is the single source of truth for the field numbers (the firmware agent owns it and adds the
    fields with the all-loop build). Every shape the contract may carry them in is read, so a contract that
    gains them in any of those shapes works without a host change.

    The names read are the field names themselves (``ref_type``, ``ref_model_bw``, ``xy_layers``,
    ``att_layers``), and the layer groups without the ``_layers`` suffix (``att``, ``yaw``, ``z``).
    """
    fc = _contract_module()

    out: dict[str, int] = {}
    for source in (getattr(fc, "MRAC_LAYER_FIELDS", None), getattr(fc, "LAYER_FIELDS", None)):
        if isinstance(source, Mapping):
            out.update({str(k): int(v) for k, v in source.items()})
    contract = getattr(fc, "current", None)
    for attr in ("mrac_layer_fields", "layer_fields", "mrac_variant_fields", "variant_fields"):
        source = getattr(contract, attr, None)
        if isinstance(source, Mapping):
            out.update({str(k): int(v) for k, v in source.items()})
    spec = contract.command_spec(0x1D) if contract is not None else None
    for param in (spec.params if spec is not None else ()):
        name = str(param.name)
        if name.endswith("_layers") and name[:-len("_layers")] in LAYER_GROUPS:
            out.setdefault(name, int(param.index))
            out.setdefault(name[:-len("_layers")], int(param.index))
    return out


def field_number(name: str) -> int | None:
    """The CMD 0x1D field number of a field NAME, or None when no source knows it.

    The contract is asked first (the firmware agent owns it), the host mirror of the firmware table
    (mrac_variants.VARIANT_FIELDS) answers for the fields the contract does not name yet. The number is never
    typed in this file.

    A name the reference uses may carry a different name in the firmware (the 2nd-order damping is
    ``ref_model_zeta`` in the fit file and ``ref_zeta`` in the firmware table): REF_FIELD_ALIASES lists the
    names that are tried, in order.
    """
    from ground_station.analysis import mrac_variants as mv

    named = contract_fields()
    for candidate in REF_FIELD_ALIASES.get(name, (name,)):
        if candidate in named:
            return int(named[candidate])
    fields = [n for n, _lo, _hi in mv.VARIANT_FIELDS]
    for candidate in REF_FIELD_ALIASES.get(name, (name,)):
        if candidate in fields:
            return fields.index(candidate)
    return None


def contract_layer_fields() -> dict[str, int]:
    """The layer fields the firmware contract carries, as {group: field number}.

    The contract is the single source of truth for the layer field numbers (the firmware agent adds them with
    the all-loop build). Every shape the contract may carry them in is read, so a contract that gains them in
    any of those shapes works without a host change.
    """
    out: dict[str, int] = {}
    for name, number in contract_fields().items():
        if name.endswith("_layers"):
            group = name[: -len("_layers")]
        else:
            group = name
        if group in LAYER_GROUPS:
            out.setdefault(group, int(number))
    return out


def layer_fields() -> dict[str, int]:
    """Every layer group's CMD 0x1D field number: the host mirror's ``<group>_layers`` rows, then the contract's.

    The host mirror (VARIANT_FIELDS) travels with the firmware table it mirrors (API/mrac.c mrac_var_field):
    x/y is field 31, and the all-loop build adds att / yaw / z as 33 / 34 / 35. A field the contract names
    overrides the mirror's number.
    """
    from ground_station.analysis import mrac_variants as mv

    names = [name for name, _lo, _hi in mv.VARIANT_FIELDS]
    out = {g: names.index(f"{g}_layers") for g in LAYER_GROUPS if f"{g}_layers" in names}
    out.update(contract_layer_fields())
    return out


def layer_field(group: str) -> int | None:
    """The CMD 0x1D field number of a layer group, or None when the firmware contract does not carry it.

    None is NOT an error: the all-loop build adds the att / yaw / z fields, and until the contract carries
    them those groups are refused on the GROUND (layer_write), naming the field, so the flight stays in the
    deck. An unknown group name is a typo in the .fly file and still raises.
    """
    g = LAYER_ALIASES.get(group, group)
    if g not in LAYER_GROUPS:
        raise GroundWriteError(f"unknown layer group {group!r} (known: {', '.join(LAYER_GROUPS)})")
    return layer_fields().get(g)


def layer_write(group: str, target: str) -> dict[str, Any]:
    """The ground write that sets one layer group to inner/outer/both/off.

    A group whose field the firmware contract does not carry yet (the all-loop build adds att / yaw / z)
    gives the refusing write: the flight stays in the deck and is refused on the ground, naming the field.
    """
    t = str(target).strip().lower()
    if t not in LAYER_TARGETS:
        raise GroundWriteError(f"layers {group}: target must be one of {', '.join(sorted(set(LAYER_TARGETS)))}, "
                               f"got {target!r}")
    g = LAYER_ALIASES.get(group, group)
    field = layer_field(group)
    if field is None:
        return refusing_write(
            f"layers {g}: field {g}_layers is missing from the firmware contract ({NEEDS_ALL_LOOP})",
            f"{g}_layers")
    # The layer fields are global (any axis sets them). Axes 0-3 pack the field in 5 bits of the idx byte, so
    # field 32+ goes on axis x: idx 0x80 | (field << 1), e.g. att_layers 33 -> 0xC2 (API/mrac.c MRAC_VariantIdxSet).
    axis = 4 if field >= 32 else 0
    return {"kind": "variant", "axis": axis, "field": field, "value": float(LAYER_TARGETS[t])}


def refusing_write(reason: str, field: str) -> dict[str, Any]:
    """A ground write the host refuses on the ground at run time: the flight stays in the deck, nothing is sent.

    It passes the parse (check_write), so one missing firmware field never stops the whole deck. When the runner
    reaches it, send_write returns False and the flight is refused before arming, with the field in the label.
    """
    return {"kind": "refuse", "reason": str(reason), "field": str(field)}


# --------------------------------------------------------------------------- the fitted reference


def ref_fit_path(kind: str, root: str | Path = ".") -> Path:
    """Where the fitted reference of a controller kind lives (logs/ref_fit/<kind>.json)."""
    return Path(root) / REF_FIT_DIR / f"{kind}.json"


def load_ref_fit(kind: str, root: str | Path = ".") -> dict[str, Any]:
    """The fitted reference of a controller kind.

    Raises GroundWriteError when the file is missing: a flight must never fly an unmeasured reference, so the
    caller refuses the flight instead of sending a default.
    """
    path = ref_fit_path(kind, root)
    if not path.is_file():
        raise GroundWriteError(
            f"mrac_ref from={kind}: no fitted reference at {path} (run: python -m "
            f"ground_station.analysis.mrac_ref_fit <session> --kind {kind})")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise GroundWriteError(f"mrac_ref from={kind}: cannot read {path}: {e}") from None
    if not isinstance(data, dict):
        raise GroundWriteError(f"mrac_ref from={kind}: {path} is not a reference file")
    data["__path__"] = str(path)
    return data


def _num(data: Mapping[str, Any], key: str, kind: str) -> float:
    if key not in data:
        raise GroundWriteError(f"mrac_ref from={kind}: {path_name(data)} has no {key}")
    try:
        v = float(data[key])
    except (TypeError, ValueError):
        raise GroundWriteError(f"mrac_ref from={kind}: {key} is not a number ({data[key]!r})") from None
    if not math.isfinite(v):
        raise GroundWriteError(f"mrac_ref from={kind}: {key} is not finite ({data[key]!r})")
    return v


def path_name(data: Mapping[str, Any]) -> str:
    """The file the reference came from, for the error message."""
    return str(data.get("__path__", "the reference file"))


def ref_writes(kind: str, root: str | Path = ".") -> list[dict[str, Any]]:
    """The ground writes that set the x/y reference model from a fitted reference.

    One write per axis (x = 4, y = 5) and per field: ref_type, ref_model_bw, ref_delay_s, and ref_model_zeta when
    the fitted reference is second order. The fitted file names the values per axis (``ref_model_bw_x`` /
    ``ref_model_bw_y``); a key without an axis suffix is the value for both axes.

    The field NUMBERS are looked up BY NAME (field_number): the firmware contract first, then the host mirror
    of API/mrac.c MRAC_VAR_FIELD. They are never typed in this file. A field the firmware does not have is
    refused loudly, naming the field, with the note "needs mrac-all-loops item 0": the flight stays in the
    deck and does not take off until the reviewed build is flashed.

    A value outside the firmware's range is refused too, so a bad fit cannot reach the drone.
    """
    data = load_ref_fit(kind, root)
    from ground_station.analysis import mrac_variants as mv

    order = data.get("order")
    if order not in ("1st", "2nd"):
        raise GroundWriteError(f"mrac_ref from={kind}: order must be '1st' or '2nd' (got {order!r})")
    # ref_type: 1 = first order (the firmware's MRAC_REF_TYPE_FIRST), 2 = second order.
    ref_type = 1.0 if order == "1st" else 2.0
    wanted: list[tuple[str, float | None]] = [("ref_type", ref_type), ("ref_model_bw", None), ("ref_delay_s", None)]
    if order == "2nd":
        wanted.append(("ref_model_zeta", None))
    # Every field the reference needs, by name. A missing one is a loud refusal, never a skip.
    numbers: dict[str, int] = {}
    bounds: dict[str, tuple[float, float]] = {}
    for field, _fixed in wanted:
        n = field_number(field)
        if n is None:
            raise GroundWriteError(
                f"mrac_ref from={kind}: field {field} is missing from the firmware contract "
                f"({NEEDS_ALL_LOOP})")
        numbers[field] = int(n)
        bounds[field] = _field_bounds(int(n)) or (-math.inf, math.inf)
    out: list[dict[str, Any]] = []
    for axis, suffix in ((4, "x"), (5, "y")):
        for field, fixed in wanted:
            value = fixed if fixed is not None else _axis_num(data, field, suffix, kind)
            lo, hi = bounds[field]
            if not lo <= value <= hi:
                raise GroundWriteError(f"mrac_ref from={kind}: {field} {suffix} = {value} is outside the firmware "
                                       f"range [{lo}, {hi}]")
            out.append({"kind": "variant", "axis": axis, "field": numbers[field], "value": value})
    return out


def _axis_num(data: Mapping[str, Any], field: str, suffix: str, kind: str) -> float:
    """One fitted value for one axis: ``<field>_<axis>`` when present, else ``<field>`` for both axes."""
    for key in (f"{field}_{suffix}", field):
        if key in data:
            return _num(data, key, kind)
    raise GroundWriteError(f"mrac_ref from={kind}: the fitted reference has no {field} for axis {suffix}")


# --------------------------------------------------------------------------- the prior


def prior_writes(session: str, root: str | Path = ".") -> list[dict[str, Any]]:
    """The ground writes that load the MRAC prior (Theta) of a previous session.

    The Theta comes from the session's ``mrac_state.<axis>.Theta[i]`` columns (the same source the firmware's own
    prior path reads), through mrac_prior.prior_from_columns. A session without those columns is refused: no
    invented weights.

    The warm bit comes LAST (mrac_prior.plan writes the bits for every axis, warm last), so the firmware
    re-seeds at the next disarmed tick with the whole prior in place.
    """
    from ground_station.analysis import log_corpus as lc
    from ground_station.platform import mrac_prior as mp

    path = _session_path(session, root)
    try:
        ser = lc.load(lc.LogRef(path.name, path, "session"))
    except (OSError, ValueError) as e:
        raise GroundWriteError(f"mrac_prior from={session}: cannot read {path}: {e}") from None
    if not ser:
        raise GroundWriteError(f"mrac_prior from={session}: {path} has no telemetry rows")
    cols = {k: v[1] for k, v in ser.items()}
    prior = mp.prior_from_columns(cols, last_n=PRIOR_LAST_N)
    if not prior:
        raise GroundWriteError(
            f"mrac_prior from={session}: {path} has no mrac_state.<axis>.Theta[i] columns (the session must log "
            f"the MRAC state to seed a prior)")
    steps = mp.plan(prior, warm=list(prior))
    return [{"kind": "prior", "axis": mp.AXES[cmd - mp.CMD_BASE], "idx": idx, "value": value}
            for cmd, idx, value in steps]


def _session_path(session: str, root: str | Path = ".") -> Path:
    """The session dir a session id names: logs/sessions/<id>, logs/campaigns/<id>, or the path as given."""
    s = Path(session)
    if s.is_dir() and (s / "telemetry.csv").is_file():
        return s
    for base in (Path(root) / "logs" / "sessions", Path(root) / "logs" / "campaigns"):
        hit = base / session
        if (hit / "telemetry.csv").is_file():
            return hit
    raise GroundWriteError(
        f"mrac_prior from={session}: no session dir with telemetry.csv (looked in "
        f"{Path(root) / 'logs' / 'sessions'} and {Path(root) / 'logs' / 'campaigns'})")


# --------------------------------------------------------------------------- checking and sending


def _field_bounds(field: int) -> tuple[float, float] | None:
    """The firmware's accept range of a CMD 0x1D field, or None when the firmware has no such field.

    The reference and knob fields come from the host mirror of the firmware table; a layer field of the
    all-loop build is not in that table yet, so its range is the firmware's layer bits (0..3).
    """
    from ground_station.analysis import mrac_variants as mv

    if 0 <= field < len(mv.VARIANT_FIELDS):
        return mv.VARIANT_FIELDS[field][1:]
    if field in layer_fields().values():
        return (float(min(LAYER_TARGETS.values())), float(max(LAYER_TARGETS.values())))
    return None
def field_name(field: int) -> str:
    """The name of a CMD 0x1D field, for the flight log.

    The name is looked up in the same sources the number came from: the firmware contract first, then the
    host mirror. So a field the firmware agent adds shows up by name, never as "field 32".
    """
    from ground_station.analysis import mrac_variants as mv

    for name, number in contract_fields().items():
        if number == field:
            return name
    if 0 <= field < len(mv.VARIANT_FIELDS):
        return mv.VARIANT_FIELDS[field][0]
    for group, number in layer_fields().items():
        if number == field:
            return f"{group}_layers"
    return f"field {field}"


def check_write(write: Mapping[str, Any]) -> None:
    """Raise GroundWriteError when the write is not one this host may send."""
    kind = write.get("kind")
    if kind == "variant":
        from ground_station.analysis import mrac_variants as mv

        axis, field, value = write.get("axis"), write.get("field"), write.get("value")
        if not isinstance(axis, int) or isinstance(axis, bool) or not 0 <= axis < len(mv.AXES):
            raise GroundWriteError(f"variant: axis {axis!r} is not one of 0..{len(mv.AXES) - 1}")
        if not isinstance(field, int) or isinstance(field, bool) or field < 0:
            raise GroundWriteError(f"variant: field {field!r} is not a field number")
        bounds = _field_bounds(field)
        if bounds is None:
            raise GroundWriteError(f"variant: field {field} is not one of the firmware's CMD 0x1D fields")
        lo, hi = bounds
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
            raise GroundWriteError(f"variant: value {value!r} is not a finite number")
        if not lo <= float(value) <= hi:
            raise GroundWriteError(f"variant: {field_name(field)} = {value} is outside [{lo}, {hi}]")
    elif kind == "prior":
        from ground_station.platform import mrac_prior as mp

        mp.check(str(write.get("axis")), int(write.get("idx", -1)), float(write.get("value", 0.0)))
    elif kind == "refuse":
        # Valid at parse time on purpose (refusing_write): it is refused on the ground when the flight runs.
        if not str(write.get("field") or "") or not str(write.get("reason") or ""):
            raise GroundWriteError("refuse: needs a field and a reason")
    else:
        raise GroundWriteError(f"unknown ground write {kind!r}")


def write_label(write: Mapping[str, Any]) -> str:
    """One line naming the write, for the flight log."""
    from ground_station.analysis import mrac_variants as mv

    if write.get("kind") == "variant":
        axis, field = int(write["axis"]), int(write["field"])
        return f"variant {mv.AXES[axis]} {field_name(field)} = {write['value']:g}"
    if write.get("kind") == "prior":
        return f"prior {write.get('axis')} [{write.get('idx')}] = {write.get('value')}"
    if write.get("kind") == "refuse":
        return f"REFUSED {write.get('field')}: {write.get('reason')}"
    return str(write)


def send_write(client: Any, write: Mapping[str, Any], tries: int = 3) -> bool:
    """Send one ground write, retrying a lost reply (the write is idempotent and absolute).

    True when the firmware APPLIED it. A refused write is not retried: the firmware would refuse it again.
    """
    from ground_station.analysis import mrac_variants as mv

    kind = write.get("kind")
    if kind == "refuse":
        # Nothing is sent: the runner aborts this flight on the ground, and its label (write_label) names the field.
        return False
    for _ in range(max(1, tries)):
        if kind == "variant":
            axis, field = int(write["axis"]), int(write["field"])
            if 0 <= field < len(mv.VARIANT_FIELDS):
                ok = client.mrac_variant(axis, field, float(write["value"]))
            else:
                # A field of the all-loop build: the host mirror does not know it yet, so the idx byte is
                # built here (the firmware's own packing, API/mrac.c MRAC_VariantIdxSet). A field number that
                # needs 5 bits has no axes 0-3 encoding, so it goes on the x / y axes only.
                if axis < 4 and field >= 32:
                    raise GroundWriteError(
                        f"variant: field {field} ({field_name(field)}) has no encoding on axes 0-3 (the field "
                        f"number needs 5 bits there); it is reachable on the x / y axes only ({NEEDS_ALL_LOOP})")
                idx = (field << 2) | axis if axis < 4 else 0x80 | (field << 1) | (axis - 4)
                ok = client._send_cmd(0x1D, idx, float(write["value"]))
        elif kind == "prior":
            ok = client.mrac_prior(str(write["axis"]), int(write["idx"]), float(write["value"]))
        else:
            raise GroundWriteError(f"unknown ground write {kind!r}")
        if ok:
            return True
        if "no result in" not in str(getattr(client, "last_error", "") or ""):
            return False
    return False
