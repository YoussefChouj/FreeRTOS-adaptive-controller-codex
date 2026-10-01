"""Controller descriptors for Workflow B tuning campaigns.

A descriptor YAML (``controllers/<name>.yaml``) lists the knobs of one controller that a campaign may
tune: the firmware variable each knob writes, the uplink command that writes it (``cmd_id`` plus the
packed ``idx`` byte), the firmware default and the search range ``[lo, hi]``.

``load`` checks a descriptor against the firmware contract and against the firmware's own decode of
the idx byte, so a knob whose idx would write a different variable fails at load time instead of
silently tuning the wrong gain. Every problem is collected and raised in one ``DescriptorError``.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ground_station.platform.firmware_contract import COMMAND_TABLE, CommandParam

CONTROLLERS_DIR = Path(__file__).with_name("controllers")
MAX_KNOBS = 14  # sim/bench search limit (docs/workflow-b/facts-gs.md:111)
SCALES = ("log", "lin")

# Firmware variables behind each tuning command, in the order the firmware indexes them
# (TASK/send_data.c:1474-1519). The contract's axis/elem ranges stay the authority for which idx
# values are legal; a test asserts these tuples cover exactly those ranges.
PID_GAIN_CMD = 0x01  # idx = axis * 3 + gain
PID_AXES = ("pitchPID", "rollPID", "yawPID", "gyroxPID", "gyroyPID", "gyrozPID", "Z_ratePID")
PID_GAINS = ("Kp", "Ki", "Kd")
MRAC_ARRAY_CMDS = {0x02: "gamma", 0x05: "What_limit", 0x08: "What_tol"}  # idx = axis << 4 | elem
MRAC_AXES = ("mrac_config_pitch", "mrac_config_roll", "mrac_config_yaw", "mrac_config_z")
TUNING_CMDS = (PID_GAIN_CMD, *MRAC_ARRAY_CMDS)

_DESCRIPTOR_KEYS = ("name", "knobs", "shadow_outputs")
_KNOB_KEYS = ("symbol", "cmd_id", "idx", "default", "lo", "hi", "scale")
_KIND_TEXT = {"str": "a non-empty string", "int": "an integer", "number": "a finite number"}


@dataclass(frozen=True)
class Knob:
    """One tunable firmware value and the command that writes it."""

    symbol: str   # firmware variable written, e.g. "gyroxPID.Kd" or "mrac_config_roll.gamma[0]"
    cmd_id: int   # uplink command id (one of TUNING_CMDS)
    idx: int      # packed idx byte, see wire_target()
    default: float
    lo: float
    hi: float
    scale: str    # "log" or "lin": how the tuner spaces [lo, hi]


@dataclass(frozen=True)
class Descriptor:
    """The knobs of one controller, plus the shadow outputs it can stream."""

    name: str
    knobs: tuple[Knob, ...]
    shadow_outputs: tuple[str, ...]


class DescriptorError(ValueError):
    """Invalid descriptor; ``problems`` lists every issue found, one entry each."""

    def __init__(self, problems: list[str], source: str = "descriptor") -> None:
        super().__init__(f"invalid {source}:\n" + "\n".join(f"  - {p}" for p in problems))
        self.problems = problems


def wire_target(cmd_id: int, idx: int) -> str | None:
    """Return the firmware variable that command ``cmd_id`` writes for idx byte ``idx``.

    Decodes idx as the firmware does (TASK/send_data.c:1474-1519): 0x01 packs ``axis * 3 + gain``;
    0x02/0x05/0x08 pack ``axis << 4 | elem``. Returns None for a non-tuning command or an idx whose
    axis or element lies outside the contract's range.
    """
    if cmd_id == PID_GAIN_CMD:
        axis, sub = divmod(idx, len(PID_GAINS))
    elif cmd_id in MRAC_ARRAY_CMDS:
        axis, sub = idx >> 4, idx & 0x0F
    else:
        return None
    axis_param, sub_param = COMMAND_TABLE[cmd_id].params[:2]
    if not (_in_param_range(axis, axis_param) and _in_param_range(sub, sub_param)):
        return None
    if cmd_id == PID_GAIN_CMD:
        return f"{PID_AXES[axis]}.{PID_GAINS[sub]}"
    return f"{MRAC_AXES[axis]}.{MRAC_ARRAY_CMDS[cmd_id]}[{sub}]"


def value_param(cmd_id: int) -> CommandParam:
    """The contract parameter that bounds the value a tuning command carries."""
    return COMMAND_TABLE[cmd_id].params[2]


def load(path: str | os.PathLike) -> Descriptor:
    """Load and check a descriptor YAML; raise DescriptorError listing every problem found."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise DescriptorError([f"unreadable file or invalid YAML: {e}"], str(path)) from e
    problems: list[str] = []
    descriptor = _parse_descriptor(raw, problems)
    if problems:
        raise DescriptorError(problems, str(path))
    assert descriptor is not None  # every None return above records a problem
    return descriptor


# ---------------------------------------------------------------------------
# Parsing. Each helper appends to ``problems`` and carries on, so a single load reports every
# mistake in the file. A check that needs a field which failed to parse is skipped, so one
# mistake is reported once.
# ---------------------------------------------------------------------------

def _parse_descriptor(raw: Any, problems: list[str]) -> Descriptor | None:
    if not isinstance(raw, dict):
        problems.append("root must be a mapping with keys: " + ", ".join(_DESCRIPTOR_KEYS))
        return None
    _check_unknown_keys(raw, _DESCRIPTOR_KEYS, "descriptor", problems)
    name = _field(raw, "name", "str", "descriptor", problems)
    shadow_outputs = _parse_shadow_outputs(raw, problems)
    knobs = _parse_knobs(raw, problems)
    if name is None or shadow_outputs is None or knobs is None:
        return None
    return Descriptor(name, knobs, shadow_outputs)


def _parse_shadow_outputs(raw: dict, problems: list[str]) -> tuple[str, ...] | None:
    if "shadow_outputs" not in raw:
        problems.append("descriptor: missing key 'shadow_outputs' (use [] when there are none)")
        return None
    outputs = raw["shadow_outputs"]
    if not isinstance(outputs, list) or not all(isinstance(s, str) and s for s in outputs):
        problems.append(f"descriptor: 'shadow_outputs' must be a list of symbol names, got {outputs!r}")
        return None
    if len(set(outputs)) != len(outputs):
        problems.append("descriptor: 'shadow_outputs' lists a symbol twice")
        return None
    return tuple(outputs)


def _parse_knobs(raw: dict, problems: list[str]) -> tuple[Knob, ...] | None:
    if "knobs" not in raw:
        problems.append("descriptor: missing key 'knobs'")
        return None
    entries = raw["knobs"]
    if not isinstance(entries, list):
        problems.append(f"descriptor: 'knobs' must be a list, got {entries!r}")
        return None
    if not 1 <= len(entries) <= MAX_KNOBS:
        problems.append(f"descriptor: {len(entries)} knobs, need 1..{MAX_KNOBS}")
    knobs = [_parse_knob(i, entry, problems) for i, entry in enumerate(entries)]
    parsed = [k for k in knobs if k is not None]
    _check_duplicates(parsed, problems)
    return tuple(parsed) if len(parsed) == len(entries) else None


def _parse_knob(i: int, raw: Any, problems: list[str]) -> Knob | None:
    where = f"knob #{i}"
    if not isinstance(raw, dict):
        problems.append(f"{where}: must be a mapping with keys: " + ", ".join(_KNOB_KEYS))
        return None
    n_before = len(problems)
    symbol = _field(raw, "symbol", "str", where, problems)
    if symbol is not None:
        where = f"knob #{i} ({symbol})"
    _check_unknown_keys(raw, _KNOB_KEYS, where, problems)
    cmd_id = _field(raw, "cmd_id", "int", where, problems)
    idx = _field(raw, "idx", "int", where, problems)
    default = _field(raw, "default", "number", where, problems)
    lo = _field(raw, "lo", "number", where, problems)
    hi = _field(raw, "hi", "number", where, problems)
    scale = _field(raw, "scale", "str", where, problems)

    if cmd_id is not None and idx is not None:
        _check_wire(where, symbol, cmd_id, idx, lo, hi, problems)
    if lo is not None and hi is not None and not lo < hi:
        problems.append(f"{where}: lo {lo} must be below hi {hi}")
    elif None not in (lo, default, hi) and not lo <= default <= hi:
        problems.append(f"{where}: default {default} lies outside [lo, hi] = [{lo}, {hi}]")
    if scale is not None and scale not in SCALES:
        problems.append(f"{where}: scale must be one of {', '.join(SCALES)}, got {scale!r}")
    if scale == "log" and lo is not None and lo <= 0:
        problems.append(f"{where}: a log scale needs lo > 0, got {lo}")

    if len(problems) > n_before:
        return None
    return Knob(symbol, cmd_id, idx, float(default), float(lo), float(hi), scale)


def _check_wire(where: str, symbol: str | None, cmd_id: int, idx: int,
                lo: float | None, hi: float | None, problems: list[str]) -> None:
    """The command must be a tuning command, idx must write ``symbol``, and [lo, hi] must fit
    the contract's value range for that command."""
    if not (0 <= cmd_id <= 255 and 0 <= idx <= 255):
        problems.append(f"{where}: cmd_id {cmd_id} and idx {idx} must both be bytes (0..255)")
        return
    if cmd_id not in TUNING_CMDS:
        allowed = ", ".join(f"0x{c:02X}" for c in TUNING_CMDS)
        problems.append(f"{where}: cmd_id 0x{cmd_id:02X} is not a tuning command ({allowed})")
        return
    target = wire_target(cmd_id, idx)
    if target is None:
        problems.append(f"{where}: idx {idx} decodes outside the contract's range for cmd 0x{cmd_id:02X}")
    elif symbol is not None and symbol != target:
        problems.append(f"{where}: cmd 0x{cmd_id:02X} idx {idx} writes {target}, not {symbol}")
    param = value_param(cmd_id)
    if lo is not None and hi is not None and not (
            _in_param_range(lo, param) and _in_param_range(hi, param)):
        problems.append(f"{where}: [lo, hi] = [{lo}, {hi}] leaves the contract range of "
                        f"cmd 0x{cmd_id:02X} {param.name}: [{param.min_val}, {param.max_val}]")


def _check_duplicates(knobs: list[Knob], problems: list[str]) -> None:
    """Report knobs that write the same variable. A checked knob's symbol is fixed by its
    (cmd_id, idx), so one check covers both a repeated symbol and a repeated command slot."""
    first_seen: dict[str, int] = {}
    for i, knob in enumerate(knobs):
        if knob.symbol in first_seen:
            problems.append(f"knobs #{first_seen[knob.symbol]} and #{i} both write {knob.symbol} "
                            f"(cmd 0x{knob.cmd_id:02X} idx {knob.idx})")
        else:
            first_seen[knob.symbol] = i


def _check_unknown_keys(raw: dict, allowed: tuple[str, ...], where: str, problems: list[str]) -> None:
    for key in raw:
        if key not in allowed:
            problems.append(f"{where}: unknown key {key!r}")


def _field(raw: dict, key: str, kind: str, where: str, problems: list[str]) -> Any:
    """Return ``raw[key]`` if present and of ``kind`` ('str' | 'int' | 'number'); otherwise record
    the problem and return None. YAML booleans are rejected as numbers."""
    if key not in raw:
        problems.append(f"{where}: missing key '{key}'")
        return None
    value = raw[key]
    if kind == "str":
        ok = isinstance(value, str) and value != ""
    elif kind == "int":
        ok = type(value) is int
    else:
        ok = type(value) in (int, float) and math.isfinite(value)
    if not ok:
        problems.append(f"{where}: '{key}' must be {_KIND_TEXT[kind]}, got {value!r}")
        return None
    return value


def _in_param_range(value: float, param: CommandParam) -> bool:
    """True when ``value`` lies inside the contract parameter's bounds (None = unbounded)."""
    return ((param.min_val is None or value >= param.min_val)
            and (param.max_val is None or value <= param.max_val))
