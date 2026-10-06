"""Workflow B capture plan: what a tuning campaign records, at which rate, and the session manifest.

- ``CAMPAIGN_SET`` lists the recorded variables. ``needed`` covers scoring (position, motors), the abort
  monitor and clock sync; ``optional`` holds the MRAC shadow signals in priority order. This module owns
  every symbol name: the scoring adapter (ground_station/analysis/workflow_b_adapter.py) imports
  ``POSITION_AXES`` and ``MOTORS`` from here, so it cannot disagree with what was recorded.
- ``slots_for(rate_hz)`` packs the set into stream slots under the slot limits and the planning budget.
  Needed variables must all fit; optional ones are dropped from the end of the list.
- ``probe_max_rate(stream)`` finds the highest rate the live link carries without loss.
- ``write_manifest`` / ``read_manifest`` store and check a session's ``manifest.json``.

Every CAMPAIGN_SET entry is one scalar, so it takes one stream range (manifests.yaml:135 lists array
elements one by one). The planner prices each at DEFAULT_VALUE_BYTES, an upper bound: motors are 2 B and
flight_phase is 1 B.
"""
from __future__ import annotations

import json
import math
import os
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple

from ground_station.livewatch.manifest import DEFAULT_VALUE_BYTES, REQUIRED_SYNC_VARS
from ground_station.livewatch.stream import (
    FRAME_OVERHEAD,
    MAX_SLOTS,
    MAX_STREAM_RANGES,
    SEND_TASK_HZ,
    STREAM_MAX_BYTES,
)

# --- limits and budget -------------------------------------------------------------------------------

VARS_PER_SLOT = min(MAX_STREAM_RANGES, STREAM_MAX_BYTES // DEFAULT_VALUE_BYTES)  # 62 ranges bind first
# The firmware budget guard prices a slot as if Send_Task ran at 200 Hz (API/subscribe.h:271,
# subscribe.c:592), although it really runs at SEND_TASK_HZ = 100. A plan must pass the guard.
GUARD_SEND_HZ = 200
RADIO_BUDGET_BPS = 87552  # USART3 radio share: 95 % of 92160 B/s (livewatch/log_frames.md:73)
PLANNING_BUDGET_BPS = 0.8 * RADIO_BUDGET_BPS  # 20 % headroom for retries and the command channel
PROBE_RATES_HZ = (200, 100, 50, 25)

MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA = "wfb_session_v1"

# --- the campaign set ---------------------------------------------------------------------------------


class Axis(NamedTuple):
    """One tracked position axis: feedback and reference symbols, and the factor to metres."""

    feedback: str
    reference: str
    to_m: float


# The loc loops run in cm, Z_posPID in m (TASK/StabilizerTask.c:242-244).
POSITION_AXES: tuple[Axis, ...] = (
    Axis("Ctrler.locxPID.FB", "Ctrler.locxPID.Des", 0.01),
    Axis("Ctrler.locyPID.FB", "Ctrler.locyPID.Des", 0.01),
    Axis("Ctrler.Z_posPID.FB", "Ctrler.Z_posPID.Des", 1.0),
)
MOTORS: tuple[str, ...] = ("mymotor.motor1", "mymotor.motor2", "mymotor.motor3", "mymotor.motor4")
ATTITUDE: tuple[str, ...] = ("imu_data.rol", "imu_data.pit", "imu_data.yaw")
RATE_LOOPS: tuple[str, ...] = (
    "Ctrler.gyroxPID.FB", "Ctrler.gyroxPID.Des",
    "Ctrler.gyroyPID.FB", "Ctrler.gyroyPID.Des",
    "Ctrler.gyrozPID.FB", "Ctrler.gyrozPID.Des",
    "Ctrler.Z_ratePID.FB", "Ctrler.Z_ratePID.Des",
)
# Abort monitor and clock sync, plus the airborne flag (FLYING|LANDING, TASK/StabilizerTask.c:252).
STATUS: tuple[str, ...] = REQUIRED_SYNC_VARS + ("flight_phase",)
# Workflow B primitive and safety status (API/wfb_glue.h wfb_status_t, all float) and the OF EKF health
# flag (TASK/StabilizerTask.c, u8: 1 healthy, 0 diverged). The live runner and the auto-next check read them.
WFB_STATUS_FIELDS: tuple[str, ...] = (
    "prim_state", "traj_state", "traj_n", "traj_rx", "traj_crc_hi", "traj_crc_lo", "traj_t", "last_err",
    "safety_trip", "hb_age", "gs_flight_active", "hover_z", "airborne_t", "fence_push",
)
WFB_STATUS: tuple[str, ...] = tuple(f"g_wfb_status.{f}" for f in WFB_STATUS_FIELDS)
KF_HEALTH = "g_ekf_of_health"

MRAC_AXES = ("pitch", "roll", "yaw", "z_rate")
MRAC_N_FEATURES = 6  # API/mrac_variant.h:12
# Priority order (dropping takes from the end): the control split first, then the tracking error, then
# the weights Theta and the filtered features Whatf. Arrays go index-major, so every axis keeps its
# leading features when the tail is dropped.
MRAC_SHADOW: tuple[str, ...] = (
    tuple(f"mrac_state.{axis}.{m}" for m in ("u_ad", "u_nom", "u_def", "e", "e_dot") for axis in MRAC_AXES)
    + tuple(
        f"mrac_state.{axis}.{array}[{i}]"
        for array in ("Theta", "Whatf")
        for i in range(MRAC_N_FEATURES)
        for axis in MRAC_AXES
    )
)

CAMPAIGN_SET: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "needed": (
        STATUS
        + tuple(name for axis in POSITION_AXES for name in (axis.feedback, axis.reference))
        + ATTITUDE
        + RATE_LOOPS
        + MOTORS
        + WFB_STATUS
        + (KF_HEALTH,)
    ),
    "optional": MRAC_SHADOW,
})

# Agent-picked log groups (decision 8): an experiment's log_plan names groups recorded on top of the
# always-on needed set. Inside a group the order is priority: the link budget trims from the end.
VELOCITY_LOOPS: tuple[str, ...] = (
    "Ctrler.locxsPID.FB", "Ctrler.locxsPID.Des", "Ctrler.locysPID.FB", "Ctrler.locysPID.Des",
    "g_of_hold_active",   # RC ch6: 0 = angle mode, the loops above never reach tilt (10-06 f01)
    "Ctrler.locxPID.U", "Ctrler.locyPID.U", "Ctrler.pitchPID.Des", "Ctrler.rollPID.Des",
)
OPTICAL_FLOW: tuple[str, ...] = ("ano_of.of2_dx_fix", "ano_of.of2_dy_fix")
LOG_GROUPS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "mrac_shadow": MRAC_SHADOW,
    "velocity_loops": VELOCITY_LOOPS,
    "optical_flow": OPTICAL_FLOW,
})
LOG_PLAN_KEYS = ("rate_hz", "groups")
# PROPOSED default: 50 Hz is the rate the 10 s stream_log check carried with 0 dropped after the last flash.
DEFAULT_LOG_PLAN: Mapping[str, Any] = MappingProxyType({"rate_hz": 50, "groups": ()})


class CaptureError(RuntimeError):
    """The campaign set cannot be captured: no loss-free rate, or the needed variables do not fit."""


class ManifestError(ValueError):
    """A session manifest is malformed; the message names the first problem found."""


# --- slot plan and rate probe -------------------------------------------------------------------------


def slots_for(
    rate_hz: float,
    *,
    budget_bps: float = PLANNING_BUDGET_BPS,
    optional: Sequence[str] | None = None,
) -> tuple[list[dict], list[str]]:
    """Pack CAMPAIGN_SET into stream slots for ``rate_hz``; returns ``(slots, dropped)``.

    ``optional`` replaces CAMPAIGN_SET["optional"] (plan_capture passes the log_plan groups).

    Each slot is ``{"slot": id, "hz": rate the drone emits, "vars": [...]}``. Variables fill slot 0 up
    to VARS_PER_SLOT, then slot 1, and so on: needed first, then optional in CAMPAIGN_SET order until
    the next one would exceed MAX_SLOTS or ``budget_bps``. That one and every later one are ``dropped``.
    Raises CaptureError if ``rate_hz`` is not positive or the needed variables alone do not fit.
    """
    if not (isinstance(rate_hz, (int, float)) and math.isfinite(rate_hz) and rate_hz > 0):
        raise CaptureError(f"rate must be a positive number of Hz, got {rate_hz!r}")
    divider = max(1, int(SEND_TASK_HZ / rate_hz))  # as the live host does (capture_preset.py:329)

    def fits(n_vars: int) -> bool:
        return math.ceil(n_vars / VARS_PER_SLOT) <= MAX_SLOTS and _guard_bps(n_vars, divider) <= budget_bps

    needed = CAMPAIGN_SET["needed"]
    optional = CAMPAIGN_SET["optional"] if optional is None else tuple(optional)
    if not fits(len(needed)):
        raise CaptureError(
            f"the {len(needed)} needed variables need {_guard_bps(len(needed), divider):.0f} B/s at "
            f"{rate_hz} Hz, over the {budget_bps:.0f} B/s budget"
        )
    kept = len(optional)
    while not fits(len(needed) + kept):
        kept -= 1
    recorded = needed + optional[:kept]
    slots = [
        {"slot": i, "hz": SEND_TASK_HZ / divider, "vars": list(recorded[start:start + VARS_PER_SLOT])}
        for i, start in enumerate(range(0, len(recorded), VARS_PER_SLOT))
    ]
    return slots, list(optional[kept:])


def _guard_bps(n_vars: int, divider: int) -> float:
    """What the firmware guard charges for ``n_vars`` packed into full slots (API/subscribe.c:592)."""
    n_slots = math.ceil(n_vars / VARS_PER_SLOT)
    frame_bytes = n_slots * FRAME_OVERHEAD + n_vars * DEFAULT_VALUE_BYTES
    return frame_bytes * GUARD_SEND_HZ / divider


def probe_max_rate(
    stream: Callable[[float, float], float],
    rates: Sequence[float] = PROBE_RATES_HZ,
    secs: float = 3.0,
) -> float:
    """The highest rate in ``rates`` that the link carries without losing a frame.

    ``stream(rate_hz, secs)`` streams the campaign set at ``rate_hz`` for ``secs`` seconds and returns
    the fraction of frames lost. Rates are tried from high to low; the first with zero loss is returned.
    Raises CaptureError if every rate loses frames.
    """
    losses: dict[float, float] = {}
    for rate in sorted(rates, reverse=True):
        losses[rate] = stream(rate, secs)
        if losses[rate] == 0.0:
            return float(rate)
    raise CaptureError(f"every probed rate lost frames (rate Hz: loss fraction): {losses}")


# --- per-experiment log plan (decision 8) -------------------------------------------------------------


def check_log_plan(log_plan: Any) -> list[str]:
    """Problems with an experiment's ``log_plan`` (``{rate_hz, groups}``, both optional); empty if valid."""
    if not isinstance(log_plan, Mapping):
        return ["must be a mapping"]
    problems = [f"unknown key {k!r} (allowed: {', '.join(LOG_PLAN_KEYS)})" for k in log_plan if k not in LOG_PLAN_KEYS]
    rate = log_plan.get("rate_hz", DEFAULT_LOG_PLAN["rate_hz"])
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 < rate <= SEND_TASK_HZ:
        problems.append(f"rate_hz: must be a number in (0, {SEND_TASK_HZ}] (got {rate!r})")
    groups = log_plan.get("groups", ())
    if not isinstance(groups, (list, tuple)) or not all(isinstance(g, str) for g in groups):
        problems.append("groups: must be a list of group names")
    else:
        problems += [f"groups: unknown group {g!r} (known: {', '.join(LOG_GROUPS)})"
                     for g in groups if g not in LOG_GROUPS]
        if len(set(groups)) != len(groups):
            problems.append("groups: a group is listed twice")
    return problems


def plan_capture(
    log_plan: Mapping[str, Any] | None = None,
    *,
    max_rate_hz: float | None = None,
    budget_bps: float = PLANNING_BUDGET_BPS,
) -> dict[str, Any]:
    """One experiment's capture: the always-on needed set, then the log_plan groups in their order.

    The rate is the log_plan ``rate_hz`` capped at ``max_rate_hz`` (probe_max_rate on the live link).
    Returns ``{"rate_hz", "requested_hz", "groups", "slots", "dropped"}``; ``rate_hz`` is what the
    drone emits after the Send_Task divider. Raises CaptureError on an invalid log_plan.
    """
    plan = {**DEFAULT_LOG_PLAN, **(log_plan or {})}
    problems = check_log_plan(plan)
    if problems:
        raise CaptureError("log_plan: " + "; ".join(problems))
    requested = float(plan["rate_hz"])
    rate = min(requested, float(max_rate_hz)) if max_rate_hz else requested
    groups = list(plan["groups"])
    slots, dropped = slots_for(rate, budget_bps=budget_bps, optional=[v for g in groups for v in LOG_GROUPS[g]])
    return {"rate_hz": slots[0]["hz"], "requested_hz": requested, "groups": groups, "slots": slots,
            "dropped": dropped}


def log_plan_table(plan: Mapping[str, Any]) -> str:
    """Markdown table of a plan_capture() plan for the operator's launch approval (launch Q3)."""
    recorded = {v for s in plan["slots"] for v in s["vars"]}
    hz = plan["rate_hz"]
    rows = [("core (always on)", CAMPAIGN_SET["needed"])] + [(g, LOG_GROUPS[g]) for g in plan["groups"]]
    lines = ["| group | vars recorded | rate Hz | note |", "|---|---|---|---|"]
    for name, group_vars in rows:
        kept = sum(v in recorded for v in group_vars)
        note = f"{len(group_vars) - kept} dropped (link budget)" if kept < len(group_vars) else ""
        lines.append(f"| {name} | {kept}/{len(group_vars)} | {hz:g} | {note} |")
    capped = f", requested {plan['requested_hz']:g} Hz" if plan["requested_hz"] != hz else ""
    bps = _guard_bps(len(recorded), max(1, round(SEND_TASK_HZ / hz)))
    lines.append(f"{len(plan['slots'])} slot(s){capped}, {bps:.0f} of {PLANNING_BUDGET_BPS:.0f} B/s planning budget")
    return "\n".join(lines)


def subscribe_steps(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Agent plan steps (action ``subscribe``, service/agent.py) that put ``plan`` on the stream slots."""
    return [
        {"action": "subscribe",
         "args": {"slot": s["slot"], "divider": max(1, round(SEND_TASK_HZ / s["hz"])), "ranges": list(s["vars"])}}
        for s in plan["slots"]
    ]


# --- session manifest ---------------------------------------------------------------------------------

_MANIFEST_KEYS = ("schema", "pack_id", "rate_hz", "slots", "dropped", "segments")
_SLOT_KEYS = ("slot", "hz", "csv", "vars")
_SEGMENT_KEYS = ("name", "t0_host_s", "t1_host_s")


def write_manifest(
    session_dir: str | os.PathLike[str],
    *,
    pack_id: str,
    rate_hz: float,
    slots: Sequence[Mapping[str, Any]],
    dropped: Sequence[str],
    segments: Sequence[Mapping[str, Any]] = (),
) -> Path:
    """Validate and write ``<session_dir>/manifest.json``; returns its path.

    ``slots`` are slots_for() slots, each with its ``csv`` file name added. ``segments`` are
    ``{"name", "t0_host_s", "t1_host_s"}`` (host clock, s) and are written sorted by start time.
    The inputs are not modified. Raises ManifestError (and writes nothing) if the manifest is invalid.
    """
    manifest = _checked({
        "schema": MANIFEST_SCHEMA,
        "pack_id": pack_id,
        "rate_hz": rate_hz,
        "slots": slots,
        "dropped": dropped,
        "segments": segments,
    })
    path = Path(session_dir) / MANIFEST_NAME
    tmp = path.with_name(MANIFEST_NAME + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)  # a crash mid-write leaves the old manifest or none, never half a file
    return path


def read_manifest(session_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Read and validate ``<session_dir>/manifest.json`` (segments come back sorted by start time).

    Raises ManifestError if it is not JSON or not a valid wfb_session_v1 manifest.
    """
    path = Path(session_dir) / MANIFEST_NAME
    try:
        return _checked(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{path}: not valid JSON ({exc})") from exc
    except ManifestError as exc:
        raise ManifestError(f"{path}: {exc}") from None


def _checked(manifest: Any) -> dict[str, Any]:
    """A validated copy of ``manifest`` with plain lists and floats; raises ManifestError."""
    _require_keys(manifest, _MANIFEST_KEYS, "manifest")
    if manifest["schema"] != MANIFEST_SCHEMA:
        raise ManifestError(f"schema is {manifest['schema']!r}, expected {MANIFEST_SCHEMA!r}")
    pack_id = manifest["pack_id"]
    if not isinstance(pack_id, str) or not pack_id:
        raise ManifestError(f"pack_id must be a non-empty string, got {pack_id!r}")
    slots = _checked_slots(manifest["slots"])
    dropped = _names(manifest["dropped"], "dropped")
    both = set(dropped).intersection(v for s in slots for v in s["vars"])
    if both:
        raise ManifestError(f"{sorted(both)} are both recorded and dropped")
    return {
        "schema": MANIFEST_SCHEMA,
        "pack_id": pack_id,
        "rate_hz": _positive(manifest["rate_hz"], "rate_hz"),
        "slots": slots,
        "dropped": dropped,
        "segments": _checked_segments(manifest["segments"]),
    }


def _checked_slots(raw: Any) -> list[dict[str, Any]]:
    slots = [_checked_slot(item) for item in _sequence(raw, "slots")]
    if not 1 <= len(slots) <= MAX_SLOTS:
        raise ManifestError(f"{len(slots)} slots; a session has 1 to {MAX_SLOTS}")
    for key in ("slot", "csv"):
        _no_repeats([s[key] for s in slots], f"slot {key}")
    _no_repeats([v for s in slots for v in s["vars"]], "recorded variable")
    return slots


def _checked_slot(item: Any) -> dict[str, Any]:
    _require_keys(item, _SLOT_KEYS, "slot")
    slot = item["slot"]
    if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < MAX_SLOTS:
        raise ManifestError(f"slot id must be an int in 0..{MAX_SLOTS - 1}, got {slot!r}")
    csv_name = item["csv"]
    if not isinstance(csv_name, str) or csv_name in ("", ".", "..") or "/" in csv_name or "\\" in csv_name:
        raise ManifestError(f"slot {slot} csv must be a file name in the session directory, got {csv_name!r}")
    names = _names(item["vars"], f"slot {slot} vars")
    if not names:
        raise ManifestError(f"slot {slot} records no variables")
    return {"slot": slot, "hz": _positive(item["hz"], f"slot {slot} hz"), "csv": csv_name, "vars": names}


def _checked_segments(raw: Any) -> list[dict[str, Any]]:
    segments = sorted((_checked_segment(item) for item in _sequence(raw, "segments")),
                      key=lambda s: s["t0_host_s"])
    _no_repeats([s["name"] for s in segments], "segment name")
    for prev, cur in zip(segments, segments[1:]):
        if cur["t0_host_s"] < prev["t1_host_s"]:
            raise ManifestError(f"segments {prev['name']!r} and {cur['name']!r} overlap")
    return segments


def _checked_segment(item: Any) -> dict[str, Any]:
    _require_keys(item, _SEGMENT_KEYS, "segment")
    name = item["name"]
    if not isinstance(name, str) or not name:
        raise ManifestError(f"segment name must be a non-empty string, got {name!r}")
    t0, t1 = (_finite(item[key], f"segment {name!r} {key}") for key in ("t0_host_s", "t1_host_s"))
    if not t0 < t1:
        raise ManifestError(f"segment {name!r} must start before it ends (t0 {t0}, t1 {t1})")
    return {"name": name, "t0_host_s": t0, "t1_host_s": t1}


def _require_keys(obj: Any, keys: Sequence[str], what: str) -> None:
    if not isinstance(obj, Mapping):
        raise ManifestError(f"{what} must be a JSON object, got {type(obj).__name__}")
    missing = [k for k in keys if k not in obj]
    extra = sorted(set(obj) - set(keys))
    if missing or extra:
        raise ManifestError(f"{what} keys: missing {missing}, unexpected {extra}")


def _sequence(value: Any, what: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise ManifestError(f"{what} must be a list, got {type(value).__name__}")
    return value


def _names(value: Any, what: str) -> list[str]:
    names = list(_sequence(value, what))
    if not all(isinstance(n, str) and n for n in names):
        raise ManifestError(f"{what} must hold non-empty strings")
    return names


def _no_repeats(values: Sequence[Any], what: str) -> None:
    repeated = sorted(str(v) for v, count in Counter(values).items() if count > 1)
    if repeated:
        raise ManifestError(f"repeated {what}: {repeated}")


def _finite(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ManifestError(f"{what} must be a finite number, got {value!r}")
    return float(value)


def _positive(value: Any, what: str) -> float:
    number = _finite(value, what)
    if number <= 0:
        raise ManifestError(f"{what} must be positive, got {value!r}")
    return number
