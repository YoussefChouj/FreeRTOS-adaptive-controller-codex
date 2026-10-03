"""Workflow B scenario step blocks: one scenario is one flight.

Steps run in order:
  takeoff {z}                    set_hover_z + TAKEOFF; the first step, exactly once
  hold    {s}                    stay at the hover point with the heartbeat running
  goto    {x, y, z, dwell_s}     one out-and-back trajectory hover -> (x, y, z) -> dwell -> hover
  path    {shape, params}        trajectory_pipeline.generate around the hover point
  excite  {axis, signal, f0, f1, amp, duration_s}
                                 SysID run (CMD 0x14) on one rate loop; only right after a hold (see below)
  livetune {loop, axes, budget_s, ...}  hold the hover point while ground_station/livetune tunes gains for budget_s
  land    {}                     the last step, exactly once

takeoff may also set mrac_injection (0/1): CMD 0x0F idx 10 sent on the ground before takeoff (autotune flies 0).
An excite start zeroes the optical-flow origin and the loc PID setpoints (TASK/send_data.c:1848-1865), so it must
follow a hold at the hover point; the runner also checks the position before it sends the start.

Firmware facts (docs/workflow-b/interfaces.md CMD 0x1A / 0x1B): one hover point (0, 0, hover_z) per flight,
SET_HOVER_Z only in prim IDLE, and every trajectory starts and ends at the hover point, so a goto cannot end
away from it. Every generated point is checked with trajectory_pipeline.validate against TrajLimits (0.3 m
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

SCENARIO_DIR = Path(__file__).resolve().parents[2] / "docs" / "workflow-b" / "scenarios"

STEP_KINDS = ("takeoff", "hold", "goto", "path", "excite", "livetune", "land")
TRAJ_KINDS = ("goto", "path")

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

_NAME_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
_ARG_RE = re.compile(r"^\$([a-z_][a-z0-9_]*)$")
_TOP_KEYS = {"scenario", "description", "args", "motion", "steps"}
_STEP_KEYS: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # kind: (required keys, optional keys)
    "takeoff": (("z",), ("mrac_injection",)),
    "hold": (("s",), ()),
    "goto": (("x", "y", "z"), ("dwell_s",) + tuple(DEFAULT_MOTION)),
    "path": (("shape", "params"), tuple(DEFAULT_MOTION)),
    "excite": (("axis", "f0", "f1", "amp", "duration_s"), ("signal",)),
    "livetune": (LIVETUNE_REQUIRED, LIVETUNE_OPTIONAL),
    "land": ((), ()),
}


def _compile_excite(body: dict, path: str, problems: list[str]) -> Step | None:
    n_before = len(problems)
    axis, sig = body.get("axis"), body.get("signal", "multisine")
    if axis not in ex.AXES:
        problems.append(f"{path}.axis: must be one of {sorted(ex.AXES)} (got {axis!r})")
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
    amp_max = min(EXCITE_AMP_MAX_DPS, ex.AMP_MAX_DPS.get(axis, 0.0))
    if not 0.0 < amp <= amp_max:
        problems.append(f"{path}.amp: must be in (0, {amp_max}] deg/s (got {amp})")
    if not 1.0 <= dur <= 60.0:
        problems.append(f"{path}.duration_s: must be in [1, 60] s, the firmware range (got {dur})")
    if len(problems) > n_before:
        return None
    args = {"axis": axis, "signal": sig, "f0": f0, "f1": f1, "amp": amp, "duration_s": dur}
    return Step("excite", args, (), ex.step_s(dur))


@dataclass(frozen=True)
class Step:
    """One compiled step. points is the uploaded trajectory for goto/path, empty otherwise."""

    kind: str
    args: Mapping[str, Any]
    points: tuple[TrajPoint, ...] = ()
    duration_s: float = 0.0


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
        """Estimated airborne time: steps + one settle per trajectory + climb/landing overhead."""
        n_traj = sum(1 for s in self.steps if s.kind in TRAJ_KINDS)
        return self.step_time_s + n_traj * WFB_SETTLE_S + WFB_FLIGHT_OVERHEAD_S

    def summary(self) -> list[dict[str, Any]]:
        """Plain per-step table for logs and the launch Q&A."""
        return [
            {"kind": s.kind, "args": dict(s.args), "n_points": len(s.points), "duration_s": round(s.duration_s, 3)}
            for s in self.steps
        ]


class ScenarioError(ValueError):
    """Raised with every problem found in a scenario."""

    def __init__(self, problems: list[str]) -> None:
        self.problems: list[str] = list(problems)
        super().__init__(f"{len(problems)} problem(s) found in scenario:\n" + "\n".join(f"  - {p}" for p in problems))


def _num(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


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
    for k in body:
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


def goto_points(target: tuple[float, float, float], dwell_s: float, profile: Profile) -> list[TrajPoint]:
    """Out-and-back trajectory hover -> target, dwell_s at the target, target -> hover."""
    hover = (0.0, 0.0, profile.hover_z_m)
    out = time_profile(resample([hover, target], profile.ds_m), profile)
    points = list(out)
    t_end = points[-1].t
    if dwell_s > 0.0:
        t_end += dwell_s
        points.append(TrajPoint(target[0], target[1], target[2], profile.yaw_deg, t_end))
    back = time_profile(resample([target, hover], profile.ds_m), profile)
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
        points = goto_points(target, float(dwell), profile)
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
            steps.append(Step("takeoff", dict(body)))
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
        elif kind == "hold":
            s = body.get("s")
            if not _num(s) or not s > 0.0:
                problems.append(f"{path}.hold.s: must be a finite number > 0 (got {s!r})")
            else:
                steps.append(Step("hold", dict(body), (), float(s)))
        elif kind == "livetune":
            if math.isfinite(hover_z):
                lim = TrajLimits()
                _, errs = parse_livetune(body, (0.0, 0.0, hover_z), (lim.x_abs_m, lim.y_abs_m))
                problems.extend(f"{path}.livetune.{e}" for e in errs)
                if not errs:
                    steps.append(Step("livetune", dict(body), (), float(body["budget_s"])))
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
