"""Workflow B scenario step blocks: one scenario is one flight.

Steps run in order:
  takeoff {z}                    set_hover_z + TAKEOFF; the first step, exactly once
  hold    {s}                    stay at the hover point with the heartbeat running
  goto    {x, y, z, dwell_s}     one out-and-back trajectory hover -> (x, y, z) -> dwell -> hover
  path    {shape, params}        trajectory_pipeline.generate around the hover point
  program {ops, profile, v, a, j, yaw_rate, yaw_acc, yaw_jerk}
                                 onboard preset program (CMD 0x1C): ground_station.platform.wfb_presets ops, closed
                                 with home; only the parameters fly, the firmware generates the setpoints
  excite  {axis, signal, f0, f1, amp, duration_s}
  pattern {layer, speed, axes, periods}  firmware pattern library (API/excite.c, CMD 0x2E): outer|inner, slow|fast
                                 SysID run (CMD 0x14) on one rate loop; only right after a hold (see below)
  step    {axis, delta, hold}     a setpoint step through the onboard program (CMD 0x1C): axis z (m) or yaw (deg),
                                 delta the size of the step, hold the dwell at the new setpoint
  livetune {loop, axes, budget_s, ...}  hold the hover point while ground_station/livetune tunes gains for budget_s
  preset  {name, <traj_p field>: value, ...}
                                 Keil trajectory preset (TASK/AutoflyTask.c traj_id/traj_go), started over VAR_WRITE
                                 (CMD 0x2D) from the hover: name step|zigzag|circle|figure8; fields left out fly at the
                                 firmware boot defaults (wfb_var.traj_defaults; start_fixed 1 flies to its start point
                                 first). The hover yields while it runs and holds the hover point again after (WP-45)
  set     {<variable>: value, ...}
                                 VAR_WRITE (CMD 0x2D) each variable in order from the hover (kp_id, kp_go, vp_user_go,
                                 g_traj_ff_preset); the traj_* rows belong to the preset step (SET_FORBIDDEN)
  land    {}                     the last step, exactly once

takeoff may also set mrac_injection (0/1): CMD 0x0F idx 10 sent on the ground before takeoff (autotune flies 0),
and idle_s (0..IDLE_S_MAX): seconds the props spin at idle on the ground between IDLE and TAKEOFF.
and set {<variable>: value}: VAR_WRITEs sent on the ground before IDLE, checked like a set step (g_xy_ctrl_kind
latches only on the ground, StabilizerTask.c XyCtrl latch).
An excite start zeroes the optical-flow origin and the loc PID setpoints (TASK/send_data.c:1848-1865), so it must
follow a hold at the hover point; the runner also checks the position before it sends the start.

Firmware facts (docs/workflow-b/interfaces.md CMD 0x1A / 0x1B): one hover point (0, 0, hover_z) per flight,
SET_HOVER_Z only in prim IDLE, and every trajectory starts and ends at the hover point, so a goto cannot end
away from it. A program's duration and COMMIT verdict (fence, speeds, endpoint) come from the firmware evaluator
itself (wfb_program.preview, needs gcc). Every generated point is checked with trajectory_pipeline.validate against TrajLimits (0.3 m
inside the fence and ceiling) and the summed step time against the firmware airborne cap.

A scenario file may declare `args` defaults; a value written as the string "$name" in `steps` or `motion`
is replaced by that arg (campaign experiments override them with `scenario_args`).
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from ground_station.analysis import excite_patterns as ep
from ground_station.autotune import excitation as ex
from ground_station.service.trajectory_pipeline import (
    SHAPES,
    Profile,
    TrajLimits,
    TrajPoint,
    generate,
    resample,
    time_profile,
    validate,
)
from ground_station.livetune.loop import STEP_OPTIONAL as LIVETUNE_OPTIONAL
from ground_station.livetune.loop import STEP_REQUIRED as LIVETUNE_REQUIRED
from ground_station.livetune.loop import parse_step as parse_livetune
from ground_station.platform import wfb_presets, wfb_var
from ground_station.platform.wfb_presets import MOTION_KEYS
from ground_station.platform.wfb_program import Segment, preview

SCENARIO_DIR = Path(__file__).resolve().parents[2] / "docs" / "workflow-b" / "scenarios"

STEP_KINDS = ("takeoff", "hold", "goto", "path", "program", "excite", "pattern", "step", "livetune", "preset", "set", "land")
TRAJ_KINDS = ("goto", "path")
PROG_KINDS = ("program",)

# excite limits, PROPOSED: band below the 100 Hz core-stream Nyquist; amp at most the WP-25 60 deg/s cap; hover at
# least 0.1 m above the sysid altitude floor (ex.ALT_BAND_M) so the run is not aborted by altitude.
EXCITE_F_MAX_HZ = 40.0
EXCITE_AMP_MAX_DPS = 60.0
EXCITE_Z_MIN_M = ex.ALT_BAND_M[0] + 0.1

# Airborne budget. Cap = airborne_cap_s in API/wfb_safety.c WFB_SAFETY_LIMITS_ROW; settle = interfaces.md
# "Settle ... 1.0 s" before each trajectory START; overhead covers climb, landing return and descent.
WFB_AIRBORNE_CAP_S: float = 120.0  # PROPOSED (firmware table)
WFB_SETTLE_S: float = 1.0  # PROPOSED (interfaces.md)
WFB_FLIGHT_OVERHEAD_S: float = 30.0  # PROPOSED, not measured

# Motion defaults for goto/path when neither the step nor the scenario `motion` sets them.
DEFAULT_MOTION: Mapping[str, float] = {
    "v_cruise_mps": 0.3,  # PROPOSED
    "a_max_mps2": 0.5,  # PROPOSED
    "ds_m": 0.05,  # PROPOSED
    "yaw_deg": 0.0,
}

IDLE_S_MAX = 30.0  # s on the ground at idle before TAKEOFF, PROPOSED cap
_NAME_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
_ARG_RE = re.compile(r"^\$([a-z_][a-z0-9_]*)$")
_TOP_KEYS = {"scenario", "description", "args", "motion", "steps"}
_STEP_KEYS: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # kind: (required keys, optional keys)
    "takeoff": (("z",), ("mrac_injection", "idle_s", "set", "ground")),
    "hold": (("s",), ()),
    "goto": (("x", "y", "z"), ("dwell_s", "min_jerk", "arrive_r_cm", "arrival_radius_cm", "slow_clock") + tuple(DEFAULT_MOTION)),
    "path": (("shape", "params"), ("min_jerk", "arrive_r_cm", "arrival_radius_cm", "slow_clock") + tuple(DEFAULT_MOTION)),
    "program": (("ops",), tuple(sorted(MOTION_KEYS))),
    "excite": (("axis", "f0", "f1", "amp", "duration_s"), ("signal", "target")),
    "pattern": (("layer", "speed", "axes"), ("periods",)),
    "step": (("axis", "delta", "hold"), ()),
    "livetune": (LIVETUNE_REQUIRED, LIVETUNE_OPTIONAL),
    "preset": (("name",), tuple(k.split(".", 1)[1] for k in wfb_var.traj_defaults()) + (
        "min_jerk", "slow_clock", "slow_on", "e_lo", "e_hi", "arrive_r_cm", "arrival_radius_cm",
    )),
    "set": ((), ()),  # keys are VAR_WRITE rows, checked by _compile_set
    "land": ((), ()),
}


def _compile_excite(body: dict, path: str, problems: list[str]) -> Step | None:
    n_before = len(problems)
    axis, sig, target = body.get("axis"), body.get("signal", "multisine"), body.get("target")
    if target is not None and target not in ex.TARGETS:
        problems.append(f"{path}.target: must be one of {sorted(ex.TARGETS)} (got {target!r})")
    valid_axes = ex.TARGET_AXES.get(target, ex.AXES) if target else ex.AXES
    if axis not in valid_axes:
        problems.append(f"{path}.axis: must be one of {sorted(valid_axes)} (got {axis!r})")
    if sig not in ex.SIGNALS:
        problems.append(f"{path}.signal: must be one of {sorted(ex.SIGNALS)} (got {sig!r})")
    for k in ("f0", "f1", "amp", "duration_s"):
        if not _num(body.get(k)):
            problems.append(f"{path}.{k}: must be a finite number")
    if len(problems) > n_before:
        return None
    f0, f1, amp, dur = (float(body[k]) for k in ("f0", "f1", "amp", "duration_s"))
    if not 0.1 <= f0 < f1 <= EXCITE_F_MAX_HZ:
        problems.append(f"{path}: need 0.1 <= f0 < f1 <= {EXCITE_F_MAX_HZ} Hz (got f0 {f0}, f1 {f1})")
    if target and target in ex.TARGET_AMP_MAX:
        amp_max = float(ex.TARGET_AMP_MAX[target].get(axis, 0.0))
    else:
        amp_max = min(EXCITE_AMP_MAX_DPS, ex.AMP_MAX_DPS.get(axis, 0.0))
    if not 0.0 < amp <= amp_max:
        problems.append(f"{path}.amp: must be in (0, {amp_max}] deg/s (got {amp})")
    if not 1.0 <= dur <= 60.0:
        problems.append(f"{path}.duration_s: must be in [1, 60] s, the firmware range (got {dur})")
    if len(problems) > n_before:
        return None
    args = {"axis": axis, "signal": sig, "f0": f0, "f1": f1, "amp": amp, "duration_s": dur}
    if target is not None:
        args["target"] = target
    return Step("excite", args, (), ex.step_s(dur))


PATTERN_SETTLE_S = 2.0   # after the run, before the next step (PROPOSED)

# The `step` verb: a setpoint step through the onboard program (CMD 0x1C). z is a height in m, yaw an angle in
# deg. The firmware's own program evaluator checks the path, the speeds and the fence (wfb_program.preview runs
# API/wfb_prog.c), so the limits below only keep the step inside what the program can express at all.
STEP_AXES = ("z", "yaw")
# The z step stays inside the fence the firmware allows for a program point (TrajLimits.z_min_m..z_max_m); the yaw
# step stays inside the heading limit. Both are the firmware's own numbers (trajectory_pipeline.TrajLimits).
STEP_DELTA_LIMITS: Mapping[str, tuple[float, float]] = {
    "z": (-0.3, 0.3),      # m, the soft-fence z band around the hover point
    "yaw": (-45.0, 45.0),  # deg
}
STEP_HOLD_MAX_S = 30.0   # s, the dwell at the new setpoint


def _compile_step(body: dict, hover_z: float, path: str, problems: list[str]) -> Step | None:
    """One setpoint step, as an onboard program: out to the new setpoint, dwell, back to the hover point."""
    axis = body.get("axis")
    delta = body.get("delta")
    hold = body.get("hold")
    n_before = len(problems)
    if axis not in STEP_AXES:
        problems.append(f"{path}.step.axis: must be one of {', '.join(STEP_AXES)} (got {axis!r})")
    if not _num(delta):
        problems.append(f"{path}.step.delta: must be a finite number")
    if not _num(hold) or not hold > 0.0:
        problems.append(f"{path}.step.hold: must be a finite number > 0 (got {hold!r})")
    if len(problems) > n_before:
        return None
    lo, hi = STEP_DELTA_LIMITS[axis]
    if not lo <= float(delta) <= hi:
        problems.append(f"{path}.step.delta: must be in [{lo}, {hi}] (got {delta})")
        return None
    if float(hold) > STEP_HOLD_MAX_S:
        problems.append(f"{path}.step.hold: must be <= {STEP_HOLD_MAX_S} s (got {hold})")
        return None
    # z is measured from the hover point; yaw turns about the current heading (the program's own origin).
    if axis == "z":
        target_z = hover_z + float(delta)
        if not math.isfinite(hover_z) or not TrajLimits().z_min_m <= target_z <= TrajLimits().z_max_m:
            problems.append(f"{path}.step: the z step lands at {target_z} m, outside the firmware z band "
                            f"[{TrajLimits().z_min_m}, {TrajLimits().z_max_m}] m")
            return None
        # The firmware's program evaluator wants the program to END where it started (API/wfb_prog.c WFB_ERR_ENDPOINT),
    # so the step goes out, dwells, and comes back: `close=True` appends the home op.
    ops = [{"move": {"dz": float(delta)}}, {"hold": {"s": float(hold)}}] if axis == "z" else \
        [{"turn": {"by": float(delta)}}, {"hold": {"s": float(hold)}}]
    try:
        motion = wfb_presets.Motion()
        segs = wfb_presets.build(ops, hover_z, motion, close=True)
    except (TypeError, ValueError) as e:
        problems.append(f"{path}.step: {e}")
        return None
    try:
        pv = preview(segs, hover_z)
    except RuntimeError as e:
        problems.append(f"{path}.step: preview unavailable, cannot check the step: {e}")
        return None
    if not pv.ok:
        problems.append(f"{path}.step: the firmware would reject it at COMMIT: {pv.error}")
        return None
    args = {"axis": axis, "delta": float(delta), "hold": float(hold)}
    return Step("step", args, (), pv.t_total_s, tuple(segs))


def _compile_pattern(body: dict, path: str, problems: list[str], hover_z: float) -> Step | None:
    n_before = len(problems)
    layer, speed, axes, periods = body.get("layer"), body.get("speed"), body.get("axes"), body.get("periods", 1)
    if layer not in ep.LAYERS:
        problems.append(f"{path}.layer: must be one of {list(ep.LAYERS)} (got {layer!r})")
    if speed not in ep.SPEEDS:
        problems.append(f"{path}.speed: must be one of {list(ep.SPEEDS)} (got {speed!r})")
    if (not isinstance(axes, list) or not axes or len(set(axes)) != len(axes)
            or any(a not in ep.AXES for a in axes)):
        problems.append(f"{path}.axes: must be a non-empty list of distinct axes from {list(ep.AXES)} (got {axes!r})")
    if isinstance(periods, bool) or not isinstance(periods, int) or not 1 <= periods <= ep.MAX_PERIODS:
        problems.append(f"{path}.periods: must be an integer in [1, {ep.MAX_PERIODS}] (got {periods!r})")
    if len(problems) > n_before:
        return None
    li, si = ep.LAYERS.index(layer), ep.SPEEDS.index(speed)
    if layer == "outer" and "z" in axes and math.isfinite(hover_z):
        clamp = ep.find(ep.AXES.index("z"), li, si).clamp
        lo, hi = ep.ALT_BAND_M
        if not (lo + 0.1 <= hover_z - clamp and hover_z + clamp <= hi - 0.1):
            problems.append(f"{path}: outer z pattern swings {hover_z} +- {clamp} m, outside the excite altitude "
                            f"band [{lo}, {hi}] m less 0.1 m")
            return None
    args = {"layer": layer, "speed": speed, "axes": list(axes), "periods": periods,
            "mask": ep.mask_of(axes)}
    return Step("pattern", args, (), ep.active_s(li, si, periods) + PATTERN_SETTLE_S)


@dataclass(frozen=True)
class Step:
    """One compiled step. points is the uploaded trajectory for goto/path, program the segments of a program step."""

    kind: str
    args: Mapping[str, Any]
    points: tuple[TrajPoint, ...] = ()
    duration_s: float = 0.0
    program: tuple[Segment, ...] = ()


@dataclass(frozen=True)
class Scenario:
    """A validated flight: hover point (0, 0, hover_z_m) and its steps."""

    name: str
    hover_z_m: float
    steps: tuple[Step, ...]

    @property
    def step_time_s(self) -> float:
        return sum(s.duration_s for s in self.steps)

    @property
    def budget_s(self) -> float:
        """Estimated airborne time: steps + one settle per trajectory, program or preset + climb/landing overhead."""
        n_traj = sum(1 for s in self.steps if s.kind in TRAJ_KINDS + PROG_KINDS + ("preset", "step"))
        return self.step_time_s + n_traj * WFB_SETTLE_S + WFB_FLIGHT_OVERHEAD_S

    def summary(self) -> list[dict[str, Any]]:
        """Plain per-step table for logs and the launch Q&A."""
        return [
            {"kind": s.kind, "args": dict(s.args), "n_points": len(s.points), "n_segments": len(s.program),
             "duration_s": round(s.duration_s, 3)}
            for s in self.steps
        ]


class ScenarioError(ValueError):
    """Raised with every problem found in a scenario."""

    def __init__(self, problems: list[str]) -> None:
        self.problems: list[str] = list(problems)
        super().__init__(f"{len(problems)} problem(s) found in scenario:\n" + "\n".join(f"  - {p}" for p in problems))


def _num(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _compile_preset(body: dict, hover_z: float, path: str, problems: list[str]) -> Step | None:
    """traj_p = the firmware boot defaults overlaid with the step's fields, each checked against its VAR_WRITE row,
    and move_s + ret_s + hold_s against TRAJ_TOTAL_MAX_S (traj_go refuses it as 0xEE in the air otherwise).

    Friction B3 fix: writes traj_p.start_z_m to match scenario hover_z (unless explicitly overridden by the preset body)
    so preset maneuvers do not jump to firmware boot default 0.85 m.
    """
    name = body.get("name")
    if name not in wfb_var.PRESETS:
        problems.append(f"{path}.name: must be one of {', '.join(wfb_var.PRESETS)} (got {name!r})")
        return None
    params, n0 = wfb_var.traj_defaults(), len(problems)
    extra_writes: dict[str, float] = {}
    if math.isfinite(hover_z):
        try:
            wfb_var.check("traj_p.start_z_m", hover_z)
            params["traj_p.start_z_m"] = float(hover_z)
        except wfb_var.VarWriteError as e:
            problems.append(f"{path}.start_z_m: hover_z {hover_z} violates start_z_m: {e}")
    for k, v in body.items():
        if k == "min_jerk":
            if v in (0, 1, True, False):
                if int(v) == 1:
                    params["traj_p.profile"] = 2.0
            else:
                problems.append(f"{path}.min_jerk: must be 0 or 1 (got {v!r})")
            continue
        if k == "profile" and v in ("min_jerk", "quintic"):
            params["traj_p.profile"] = 2.0
            continue
        if k == "slow_clock":
            if isinstance(v, dict):
                sc_map = {"on": "g_traj_slow_on", "e_lo": "g_traj_slow_elo_cm", "e_hi": "g_traj_slow_ehi_cm"}
                for sk, sv in v.items():
                    if sk not in sc_map or not _num(sv if not isinstance(sv, bool) else int(sv)):
                        problems.append(f"{path}.slow_clock.{sk}: invalid slow_clock field")
                        continue
                    fval = float(int(sv) if isinstance(sv, bool) else sv)
                    try:
                        wfb_var.check(sc_map[sk], fval)
                        extra_writes[sc_map[sk]] = fval
                    except wfb_var.VarWriteError as e:
                        problems.append(f"{path}.slow_clock.{sk}: {e}")
            elif v in (0, 1, True, False):
                extra_writes["g_traj_slow_on"] = float(int(v))
            else:
                problems.append(f"{path}.slow_clock: must be 0/1 or mapping {{on, e_lo, e_hi}} (got {v!r})")
            continue
        if k in ("slow_on", "e_lo", "e_hi", "arrive_r_cm", "arrival_radius_cm"):
            var_map = {
                "slow_on": "g_traj_slow_on",
                "e_lo": "g_traj_slow_elo_cm",
                "e_hi": "g_traj_slow_ehi_cm",
                "arrive_r_cm": "g_traj_arrive_r_cm",
                "arrival_radius_cm": "g_traj_arrive_r_cm",
            }
            fval = float(int(v)) if isinstance(v, bool) and k == "slow_on" else v
            if not _num(fval):
                problems.append(f"{path}.{k}: must be a finite number (got {v!r})")
                continue
            try:
                wfb_var.check(var_map[k], float(fval))
                extra_writes[var_map[k]] = float(fval)
            except wfb_var.VarWriteError as e:
                problems.append(f"{path}.{k}: {e}")
            continue
        if k == "name":
            continue
        if not _num(v):
            problems.append(f"{path}.{k}: must be a finite number (got {v!r})")
            continue
        try:
            wfb_var.check(f"traj_p.{k}", v)
        except wfb_var.VarWriteError as e:
            problems.append(f"{path}.{k}: {e}")
            continue
        params[f"traj_p.{k}"] = float(v)
    total, cap = (params["traj_p.move_s"] + params["traj_p.ret_s"] + params["traj_p.hold_s"],
                  wfb_var.traj_total_max_s())
    if total > cap:
        problems.append(f"{path}: move_s + ret_s + hold_s = {total:g} s exceeds TRAJ_TOTAL_MAX_S {cap:g} s")
    if ("g_traj_slow_elo_cm" in extra_writes and "g_traj_slow_ehi_cm" in extra_writes
            and extra_writes["g_traj_slow_elo_cm"] >= extra_writes["g_traj_slow_ehi_cm"]):
        problems.append(f"{path}.slow_clock: e_lo must be < e_hi")
    if len(problems) > n0:
        return None
    step_args: dict[str, Any] = {"name": name, "id": wfb_var.PRESETS[name], "params": params}
    if extra_writes:
        step_args["var_writes"] = extra_writes
    return Step("preset", step_args, (),
                wfb_var.preset_duration_s(params))


# A set step may not write these: traj_go/traj_stop/traj_id and traj_p.* are the preset step's (it writes all of
# traj_p before every traj_go, so a set would be overwritten or, for a ground-loaded preset, fly instead of the
# step's own fields); traj_fence_off is an operator-only Keil switch, never a script's.
SET_FORBIDDEN = ("traj_go", "traj_stop", "traj_id", "traj_fence_off", "traj_p.")


def _compile_set(body: dict, path: str, problems: list[str]) -> Step | None:
    """One VAR_WRITE per key, in the file's order, each value checked against its row."""
    if not body:
        problems.append(f"{path}: needs at least one variable: value")
        return None
    writes, n0 = [], len(problems)
    for k, v in body.items():
        if not isinstance(k, str) or k.startswith(SET_FORBIDDEN):
            problems.append(f"{path}.{k}: not writable by a set step (traj_* rows belong to the preset step)")
            continue
        if not _num(v):
            problems.append(f"{path}.{k}: must be a finite number (got {v!r})")
            continue
        try:
            wfb_var.check(k, v)
        except wfb_var.VarWriteError as e:
            problems.append(f"{path}.{k}: {e}")
            continue
        writes.append([k, float(v)])
    if len(problems) > n0:
        return None
    return Step("set", {"writes": writes}, (), 0.0)


def _compile_ground(body: Any, path: str, problems: list[str]) -> list[dict[str, Any]]:
    """The ground writes of the .fly verbs (layers / mrac_ref / mrac_prior), each checked against the firmware.

    The writes are absolute and idempotent, so the runner may resend one whose reply was lost.
    """
    from ground_station.service import ground_plan

    if not isinstance(body, list) or not body:
        problems.append(f"{path}: must be a non-empty list of ground writes")
        return []
    out: list[dict[str, Any]] = []
    for i, w in enumerate(body):
        if not isinstance(w, dict):
            problems.append(f"{path}[{i}]: must be a mapping")
            continue
        try:
            ground_plan.check_write(w)
        except ground_plan.GroundWriteError as e:
            problems.append(f"{path}[{i}]: {e}")
            continue
        out.append(dict(w))
    return out if len(out) == len(body) else []


def _subst(value: Any, args: Mapping[str, Any], path: str, problems: list[str]) -> Any:
    if isinstance(value, dict):
        return {k: _subst(v, args, f"{path}.{k}", problems) for k, v in value.items()}
    if isinstance(value, list):
        return [_subst(v, args, f"{path}.{i}", problems) for i, v in enumerate(value)]
    if isinstance(value, str):
        m = _ARG_RE.match(value)
        if m:
            if m.group(1) not in args:
                problems.append(f"{path}: unknown arg '{value}'")
                return value
            return args[m.group(1)]
    return value


def _split_step(raw: Any, path: str, problems: list[str]) -> tuple[str, dict] | None:
    if isinstance(raw, str) and raw == "land":
        return "land", {}
    if not isinstance(raw, dict) or len(raw) != 1:
        problems.append(f"{path}: must be a mapping with exactly one step kind {list(STEP_KINDS)}")
        return None
    kind, body = next(iter(raw.items()))
    if kind not in STEP_KINDS:
        problems.append(f"{path}: unknown step kind '{kind}', must be one of {list(STEP_KINDS)}")
        return None
    if body is None:
        body = {}
    if not isinstance(body, dict):
        problems.append(f"{path}.{kind}: must be a mapping")
        return None
    required, optional = _STEP_KEYS[kind]
    for k in body if kind != "set" else ():
        if k not in required and k not in optional:
            problems.append(f"{path}.{kind}.{k}: unknown key")
    for k in required:
        if k not in body:
            problems.append(f"{path}.{kind}.{k}: missing required key")
    return kind, body


def _motion(body: Mapping[str, Any], base: Mapping[str, Any], hover_z: float, path: str,
            problems: list[str]) -> Profile | None:
    m = dict(base)
    m.update({k: body[k] for k in DEFAULT_MOTION if k in body})
    ok = True
    for k, v in m.items():
        if not _num(v):
            problems.append(f"{path}.{k}: must be a finite number")
            ok = False
    if not ok:
        return None
    lim = TrajLimits()
    if not (0.0 < m["v_cruise_mps"] <= lim.v_max_mps):
        problems.append(f"{path}.v_cruise_mps: must be in (0, {lim.v_max_mps}] (got {m['v_cruise_mps']})")
        ok = False
    for k in ("a_max_mps2", "ds_m"):
        if not m[k] > 0.0:
            problems.append(f"{path}.{k}: must be > 0 (got {m[k]})")
            ok = False
    if not abs(m["yaw_deg"]) <= lim.yaw_abs_deg:
        problems.append(f"{path}.yaw_deg: must be in [-{lim.yaw_abs_deg}, {lim.yaw_abs_deg}] (got {m['yaw_deg']})")
        ok = False
    if not ok:
        return None
    return Profile(float(m["v_cruise_mps"]), float(m["a_max_mps2"]), float(m["ds_m"]), hover_z,
                   float(m["yaw_deg"]))


def goto_points(target: tuple[float, float, float], dwell_s: float, profile: Profile,
                ds_m: float | None = None) -> list[TrajPoint]:
    """Out-and-back trajectory hover -> target, dwell_s at the target, target -> hover.

    When ds_m is None (default), compiles to minimal 4 corner waypoints:
    [hover (t=0), target (t_reach), target (t_dwell), hover (t_return)].
    Firmware linearly interpolates between segment endpoints.
    If ds_m is explicitly provided and > 0, resamples along the line.
    """
    hover = (0.0, 0.0, profile.hover_z_m)
    out_pts = [hover, target]
    if ds_m is not None and ds_m > 0:
        out_pts = resample(out_pts, ds_m)
    out = time_profile(out_pts, profile)
    points = list(out)
    t_end = points[-1].t
    if dwell_s > 0.0:
        t_end += dwell_s
        points.append(TrajPoint(target[0], target[1], target[2], profile.yaw_deg, t_end))
    back_pts = [target, hover]
    if ds_m is not None and ds_m > 0:
        back_pts = resample(back_pts, ds_m)
    back = time_profile(back_pts, profile)
    points.extend(TrajPoint(p.x, p.y, p.z, p.yaw_deg, t_end + p.t) for p in back[1:])
    return points


def _compile_traj(kind: str, body: dict, base_motion: Mapping[str, Any], hover_z: float, path: str,
                  problems: list[str]) -> Step | None:
    n_before = len(problems)
    profile = _motion(body, base_motion, hover_z, f"{path}.{kind}", problems)
    if kind == "goto":
        for k in ("x", "y", "z", "dwell_s"):
            if k in body and not _num(body[k]):
                problems.append(f"{path}.goto.{k}: must be a finite number")
        dwell = body.get("dwell_s", 0.0)
        if _num(dwell) and dwell < 0.0:
            problems.append(f"{path}.goto.dwell_s: must be >= 0 (got {dwell})")
        if len(problems) > n_before or profile is None:
            return None
        target = (float(body["x"]), float(body["y"]), float(body["z"]))
        if math.dist(target, (0.0, 0.0, hover_z)) <= TrajLimits().endpoint_tol_m:
            problems.append(f"{path}.goto: target is the hover point, use hold")
            return None
        ds_m_val = float(body["ds_m"]) if "ds_m" in body and _num(body["ds_m"]) else None
        points = goto_points(target, float(dwell), profile, ds_m=ds_m_val)
        errs = validate(points, TrajLimits(), hover_z)
    else:
        shape, params = body["shape"], body["params"]
        if not isinstance(shape, str) or shape not in SHAPES:
            problems.append(f"{path}.path.shape: must be one of {sorted(SHAPES)} (got {shape!r})")
        if not isinstance(params, dict):
            problems.append(f"{path}.path.params: must be a mapping")
        if len(problems) > n_before or profile is None:
            return None
        try:
            points = generate(shape, params, profile, TrajLimits())
            errs = []
        except ValueError as exc:
            points, errs = [], [str(exc)]
    if errs:
        problems.append(f"{path}.{kind}: trajectory rejected by soft-boundary checks: {'; '.join(errs)[:400]}")
        return None
    args = {k: v for k, v in body.items()}
    return Step(kind, args, tuple(points), points[-1].t)


def _compile_program(body: dict, hover_z: float, path: str, problems: list[str]) -> Step | None:
    ops = body.get("ops")
    if not isinstance(ops, list) or not ops:
        problems.append(f"{path}.program.ops: must be a non-empty list of preset ops")
        return None
    try:
        motion = wfb_presets.Motion().with_(**{k: body[k] for k in MOTION_KEYS if k in body})
        segs = wfb_presets.build(ops, hover_z, motion)
    except (TypeError, ValueError) as e:
        problems.append(f"{path}.program: {e}")
        return None
    try:
        pv = preview(segs, hover_z)
    except RuntimeError as e:
        problems.append(f"{path}.program: preview unavailable, cannot check the program: {e}")
        return None
    if not pv.ok:
        problems.append(f"{path}.program: the firmware would reject it at COMMIT: {pv.error}")
        return None
    return Step("program", dict(body), (), pv.t_total_s, tuple(segs))


def parse_scenario(data: Any, args: Mapping[str, Any] | None = None) -> Scenario:
    """Validate scenario data (file contents or inline mapping) and compile it into a Scenario."""
    if not isinstance(data, dict):
        raise ScenarioError(["root: must be a mapping"])
    problems: list[str] = []
    for k in data:
        if k not in _TOP_KEYS:
            problems.append(f"{k}: unknown key")
    name = data.get("scenario")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        problems.append(f"scenario: must match '^[a-z0-9_-]{{1,40}}$' (got {name!r})")

    file_args = data.get("args", {}) or {}
    if not isinstance(file_args, dict):
        problems.append("args: must be a mapping")
        file_args = {}
    merged = dict(file_args)
    for k, v in (args or {}).items():
        if k not in file_args:
            problems.append(f"scenario_args.{k}: not declared in the scenario args {sorted(file_args)}")
        merged[k] = v

    motion = _subst(data.get("motion", {}) or {}, merged, "motion", problems)
    if not isinstance(motion, dict):
        problems.append("motion: must be a mapping")
        motion = {}
    for k in motion:
        if k not in DEFAULT_MOTION:
            problems.append(f"motion.{k}: unknown key")
    base_motion = dict(DEFAULT_MOTION)
    base_motion.update({k: v for k, v in motion.items() if k in DEFAULT_MOTION})

    raw_steps = _subst(data.get("steps"), merged, "steps", problems)
    if not isinstance(raw_steps, list) or len(raw_steps) < 3:
        problems.append("steps: must be a list of at least takeoff, one step, land")
        raise ScenarioError(problems)

    split = [_split_step(raw, f"steps.{i}", problems) for i, raw in enumerate(raw_steps)]
    kinds = [s[0] if s else None for s in split]
    if kinds[0] != "takeoff" or kinds.count("takeoff") != 1:
        problems.append("steps: takeoff must be the first step and appear exactly once")
    if kinds[-1] != "land" or kinds.count("land") != 1:
        problems.append("steps: land must be the last step and appear exactly once")

    hover_z = math.nan
    if split[0] and split[0][0] == "takeoff" and "z" in split[0][1]:
        z = split[0][1]["z"]
        lim = TrajLimits()
        if not _num(z) or not (lim.z_min_m <= z <= lim.z_max_m):
            problems.append(f"steps.0.takeoff.z: must be in [{lim.z_min_m}, {lim.z_max_m}] (got {z!r})")
        else:
            hover_z = float(z)

    steps: list[Step] = []
    for i, item in enumerate(split):
        if item is None:
            continue
        kind, body = item
        path = f"steps.{i}"
        if kind == "takeoff":
            if body.get("mrac_injection", 0) not in (0, 1) or isinstance(body.get("mrac_injection"), bool):
                problems.append(f"{path}.takeoff.mrac_injection: must be 0 or 1 (got {body['mrac_injection']!r})")
            idle_s = body.get("idle_s", 0)
            if isinstance(idle_s, bool) or not isinstance(idle_s, (int, float)) or not 0 <= idle_s <= IDLE_S_MAX:
                problems.append(f"{path}.takeoff.idle_s: must be in [0, {IDLE_S_MAX}] s (got {idle_s!r})")
            args = dict(body)
            if "set" in args:
                ground = _compile_set(args["set"] if isinstance(args["set"], dict) else {}, f"{path}.takeoff.set", problems)
                args["set"] = ground.args["writes"] if ground is not None else []
                if not args["set"]:
                    del args["set"]
            if "ground" in args:
                writes = _compile_ground(args["ground"], f"{path}.takeoff.ground", problems)
                if writes:
                    args["ground"] = writes
                else:
                    del args["ground"]
            steps.append(Step("takeoff", args))
        elif kind == "land":
            steps.append(Step("land", {}))
        elif kind == "excite":
            if kinds[i - 1] != "hold":
                problems.append(f"{path}.excite: must follow a hold step (the start re-zeroes the position origin, "
                                "so the drone must be holding at the hover point)")
            if math.isfinite(hover_z) and hover_z < EXCITE_Z_MIN_M:
                problems.append(f"{path}.excite: takeoff z {hover_z} is below {EXCITE_Z_MIN_M:.2f} m, too close to "
                                f"the sysid altitude floor {ex.ALT_BAND_M[0]} m")
            step = _compile_excite(body, f"{path}.excite", problems)
            if step is not None:
                steps.append(step)
        elif kind == "pattern":
            if kinds[i - 1] != "hold":
                problems.append(f"{path}.pattern: must follow a hold step (the start latches the xy box centre "
                                "at the current position, so the drone must be holding at the hover point)")
            if math.isfinite(hover_z) and hover_z < EXCITE_Z_MIN_M:
                problems.append(f"{path}.pattern: takeoff z {hover_z} is below {EXCITE_Z_MIN_M:.2f} m, too close to "
                                f"the excite altitude floor {ep.ALT_BAND_M[0]} m")
            step = _compile_pattern(body, f"{path}.pattern", problems, hover_z)
            if step is not None:
                steps.append(step)
        elif kind == "hold":
            s = body.get("s")
            if not _num(s) or not s > 0.0:
                problems.append(f"{path}.hold.s: must be a finite number > 0 (got {s!r})")
            else:
                steps.append(Step("hold", dict(body), (), float(s)))
        elif kind == "step":
            step = _compile_step(body, hover_z, f"{path}.step", problems)
            if step is not None:
                steps.append(step)
        elif kind == "livetune":
            if math.isfinite(hover_z):
                lim = TrajLimits()
                _, errs = parse_livetune(body, (0.0, 0.0, hover_z), (lim.x_abs_m, lim.y_abs_m))
                problems.extend(f"{path}.livetune.{e}" for e in errs)
                if not errs:
                    steps.append(Step("livetune", dict(body), (), float(body["budget_s"])))
        elif kind == "preset":
            step = _compile_preset(body, hover_z, f"{path}.preset", problems)
            if step is not None:
                steps.append(step)
        elif kind == "set":
            step = _compile_set(body, f"{path}.set", problems)
            if step is not None:
                steps.append(step)
        elif kind == "program":
            if math.isfinite(hover_z):
                step = _compile_program(body, hover_z, path, problems)
                if step is not None:
                    steps.append(step)
        elif math.isfinite(hover_z):
            step = _compile_traj(kind, body, base_motion, hover_z, path, problems)
            if step is not None:
                steps.append(step)

    if problems:
        raise ScenarioError(problems)
    scenario = Scenario(name, hover_z, tuple(steps))
    if not scenario.budget_s <= WFB_AIRBORNE_CAP_S:
        raise ScenarioError([
            f"steps: estimated airborne time {scenario.budget_s:.1f} s (steps {scenario.step_time_s:.1f} s "
            f"+ settle + {WFB_FLIGHT_OVERHEAD_S:.0f} s overhead) exceeds the {WFB_AIRBORNE_CAP_S:.0f} s airborne cap"
        ])
    return scenario


def scenario_from_shape(name: str, shape: str, params: Mapping[str, Any], profile: Profile) -> Scenario:
    """Legacy shape-only experiment: [takeoff profile.hover_z_m, path, land]."""
    motion = {"v_cruise_mps": profile.v_cruise_mps, "a_max_mps2": profile.a_max_mps2, "ds_m": profile.ds_m,
              "yaw_deg": profile.yaw_deg}
    return parse_scenario({
        "scenario": name,
        "motion": motion,
        "steps": [{"takeoff": {"z": profile.hover_z_m}}, {"path": {"shape": shape, "params": dict(params)}}, "land"],
    })


def load_scenario(ref: str | os.PathLike, args: Mapping[str, Any] | None = None,
                  base_dir: str | os.PathLike = SCENARIO_DIR) -> Scenario:
    """Load a scenario file; a bare name resolves to <base_dir>/<name>.yaml."""
    p = Path(ref)
    if p.suffix not in (".yaml", ".yml"):
        p = Path(base_dir) / f"{p}.yaml"
    elif not p.is_absolute() and not p.exists():
        p = Path(base_dir) / p
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ScenarioError([f"yaml: syntax error in '{p}': {exc}"]) from exc
    except OSError as exc:
        raise ScenarioError([f"file: cannot read '{p}': {exc}"]) from exc
    return parse_scenario(data, args)
