"""Campaign file schema and validation for Workflow B autonomous flights.

Pure logic: reads and validates campaign YAML files before autonomous execution.
Collects every problem into a single CampaignError so the operator can fix all
issues in one pass.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import re
from typing import Any

import yaml

from ground_station.service.abort_monitor import AbortLimits
from ground_station.service.trajectory_pipeline import SHAPES, Profile, TrajLimits

# Numeric limits taken from trajectory_pipeline and interfaces.md:
# docs/workflow-b/interfaces.md section 1 constants table
WFB_TRAJ_V_MAX: float = float(TrajLimits.v_max_mps)  # 1.0 m/s from TrajLimits
WFB_HOVER_Z_MIN: float = 0.3  # interfaces.md section 1: WFB_HOVER_Z_MIN
WFB_HOVER_Z_MAX: float = 1.4  # interfaces.md section 1: WFB_HOVER_Z_MAX

_CAMPAIGN_NAME_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
_CONTROLLER_NAME_RE = re.compile(r"^[a-z0-9_]+$")

@dataclass(frozen=True)
class EnvelopeLimit:
    """Per-parameter gain envelope limits (Workflow B Q10)."""

    min: float
    max: float
    max_step: float


@dataclass(frozen=True)
class Experiment:
    """Single flight experiment definition within a campaign queue."""

    name: str
    shape: str
    params: dict[str, Any]
    profile: Profile
    capture: str
    repeats: int


@dataclass(frozen=True)
class Campaign:
    """Frozen campaign descriptor containing all experiments and safety bounds."""

    campaign: str
    objective: str
    controller: str
    packs: tuple[str, ...]
    max_flights: int
    envelope: dict[str, EnvelopeLimit]
    experiments: tuple[Experiment, ...]
    abort: dict[str, Any] = field(default_factory=dict)

    @property
    def abort_limits(self) -> AbortLimits:
        """Resolve AbortLimits instance with campaign overrides applied."""
        return AbortLimits(**self.abort)

    def to_dict(self) -> dict[str, Any]:
        """Convert campaign back to a plain dictionary matching the YAML schema."""
        return {
            "campaign": self.campaign,
            "objective": self.objective,
            "controller": self.controller,
            "packs": list(self.packs),
            "max_flights": self.max_flights,
            "envelope": {
                k: {"min": v.min, "max": v.max, "max_step": v.max_step}
                for k, v in self.envelope.items()
            },
            "experiments": [
                {
                    "name": exp.name,
                    "shape": exp.shape,
                    "params": dict(exp.params),
                    "profile": {
                        "v_cruise_mps": exp.profile.v_cruise_mps,
                        "a_max_mps2": exp.profile.a_max_mps2,
                        "ds_m": exp.profile.ds_m,
                        "hover_z_m": exp.profile.hover_z_m,
                        "yaw_deg": exp.profile.yaw_deg,
                    },
                    "capture": exp.capture,
                    "repeats": exp.repeats,
                }
                for exp in self.experiments
            ],
            "abort": dict(self.abort),
        }


class CampaignError(ValueError):
    """Raised when one or more problems are found during campaign validation."""

    def __init__(self, problems: list[str]) -> None:
        self.problems: list[str] = list(problems)
        msg = f"{len(problems)} problem(s) found in campaign:\n" + "\n".join(
            f"  - {p}" for p in problems
        )
        super().__init__(msg)


def parse_campaign(data: dict) -> Campaign:
    """Validate and parse in-memory campaign data into a Campaign instance.

    Collects all validation problems into a single CampaignError.
    """
    if not isinstance(data, dict):
        raise CampaignError(["root: must be a YAML mapping"])

    problems: list[str] = []

    allowed_top_keys = {
        "campaign",
        "objective",
        "controller",
        "packs",
        "max_flights",
        "envelope",
        "experiments",
        "abort",
    }
    required_top_keys = [
        "campaign",
        "objective",
        "controller",
        "packs",
        "max_flights",
        "envelope",
        "experiments",
    ]

    # 1. Top-level unknown keys
    for k in data:
        if k not in allowed_top_keys:
            problems.append(f"{k}: unknown key")

    # 2. Top-level required keys
    for k in required_top_keys:
        if k not in data:
            problems.append(f"{k}: missing required key")

    # 3. Field: campaign
    if "campaign" in data:
        c_val = data["campaign"]
        if isinstance(c_val, bool) or not isinstance(c_val, str):
            problems.append("campaign: must be a string")
        elif not _CAMPAIGN_NAME_RE.match(c_val):
            problems.append(
                f"campaign: must match pattern '^[a-z0-9_-]{{1,40}}$' (got '{c_val}')"
            )

    # 4. Field: objective
    if "objective" in data:
        obj_val = data["objective"]
        if isinstance(obj_val, bool) or not isinstance(obj_val, str):
            problems.append("objective: must be a string")
        elif len(obj_val.strip()) == 0:
            problems.append("objective: must be a non-empty string")

    # 5. Field: controller
    if "controller" in data:
        ctrl_val = data["controller"]
        if isinstance(ctrl_val, bool) or not isinstance(ctrl_val, str):
            problems.append("controller: must be a string")
        elif not _CONTROLLER_NAME_RE.match(ctrl_val):
            problems.append(
                f"controller: must match pattern '^[a-z0-9_]+$' (got '{ctrl_val}')"
            )

    # 6. Field: packs
    if "packs" in data:
        packs_val = data["packs"]
        if not isinstance(packs_val, list):
            problems.append("packs: must be a list")
        elif len(packs_val) == 0:
            problems.append("packs: must not be empty")
        else:
            seen_packs: set[str] = set()
            for idx, p in enumerate(packs_val):
                p_path = f"packs.{idx}"
                if isinstance(p, bool) or not isinstance(p, str):
                    problems.append(f"{p_path}: must be a string")
                elif len(p.strip()) == 0:
                    problems.append(f"{p_path}: must be a non-empty string")
                else:
                    if p in seen_packs:
                        problems.append(f"packs: duplicate pack id '{p}'")
                    seen_packs.add(p)

    # 7. Field: max_flights
    if "max_flights" in data:
        mf_val = data["max_flights"]
        if isinstance(mf_val, bool) or not isinstance(mf_val, int):
            problems.append("max_flights: must be an integer")
        elif mf_val < 1:
            problems.append(f"max_flights: must be >= 1 (got {mf_val})")

    # 8. Field: envelope
    if "envelope" in data:
        env_val = data["envelope"]
        if not isinstance(env_val, dict):
            problems.append("envelope: must be a mapping")
        else:
            allowed_env_keys = {"min", "max", "max_step"}
            required_env_keys = ("min", "max", "max_step")
            for sym, row in env_val.items():
                sym_path = f"envelope.{sym}"
                if not isinstance(row, dict):
                    problems.append(f"{sym_path}: must be a mapping")
                    continue
                for rk in row:
                    if rk not in allowed_env_keys:
                        problems.append(f"{sym_path}.{rk}: unknown key")
                for rk in required_env_keys:
                    if rk not in row:
                        problems.append(f"{sym_path}.{rk}: missing required key")

                numeric_ok = True
                for rk in required_env_keys:
                    if rk in row:
                        val = row[rk]
                        if isinstance(val, bool) or not isinstance(val, (int, float)):
                            problems.append(f"{sym_path}.{rk}: must be numeric")
                            numeric_ok = False
                        elif not math.isfinite(val):
                            problems.append(f"{sym_path}.{rk}: must be finite")
                            numeric_ok = False

                if numeric_ok and all(rk in row for rk in required_env_keys):
                    min_val = float(row["min"])
                    max_val = float(row["max"])
                    step_val = float(row["max_step"])
                    if not (min_val < max_val):
                        problems.append(
                            f"{sym_path}: min ({min_val}) must be < max ({max_val})"
                        )
                    if step_val <= 0:
                        problems.append(f"{sym_path}.max_step: must be > 0 (got {step_val})")
                    elif min_val < max_val and step_val > (max_val - min_val):
                        problems.append(
                            f"{sym_path}.max_step: must be <= max - min ({max_val - min_val}) (got {step_val})"
                        )

    # 9. Field: experiments
    if "experiments" in data:
        exp_val = data["experiments"]
        if not isinstance(exp_val, list):
            problems.append("experiments: must be a list")
        elif len(exp_val) == 0:
            problems.append("experiments: must have at least 1 experiment")
        else:
            allowed_exp_keys = {"name", "shape", "params", "profile", "capture", "repeats"}
            required_exp_keys = ("name", "shape", "params", "profile", "capture", "repeats")
            seen_exp_names: set[str] = set()

            for idx, exp in enumerate(exp_val):
                exp_path = f"experiments.{idx}"
                if not isinstance(exp, dict):
                    problems.append(f"{exp_path}: must be a mapping")
                    continue

                for ek in exp:
                    if ek not in allowed_exp_keys:
                        problems.append(f"{exp_path}.{ek}: unknown key")
                for req in required_exp_keys:
                    if req not in exp:
                        problems.append(f"{exp_path}.{req}: missing required key")

                # Name
                if "name" in exp:
                    ename = exp["name"]
                    if isinstance(ename, bool) or not isinstance(ename, str):
                        problems.append(f"{exp_path}.name: must be a string")
                    elif len(ename.strip()) == 0:
                        problems.append(f"{exp_path}.name: must be a non-empty string")
                    else:
                        if ename in seen_exp_names:
                            problems.append(
                                f"{exp_path}.name: duplicate experiment name '{ename}'"
                            )
                        seen_exp_names.add(ename)

                # Shape
                if "shape" in exp:
                    eshape = exp["shape"]
                    if isinstance(eshape, bool) or not isinstance(eshape, str):
                        problems.append(f"{exp_path}.shape: must be a string")
                    elif eshape not in SHAPES:
                        problems.append(
                            f"{exp_path}.shape: unknown shape '{eshape}', must be one of {sorted(SHAPES.keys())}"
                        )

                # Params
                if "params" in exp:
                    eparams = exp["params"]
                    if not isinstance(eparams, dict):
                        problems.append(f"{exp_path}.params: must be a mapping")

                # Profile
                if "profile" in exp:
                    eprofile = exp["profile"]
                    prof_path = f"{exp_path}.profile"
                    if not isinstance(eprofile, dict):
                        problems.append(f"{prof_path}: must be a mapping")
                    else:
                        allowed_prof_keys = {
                            "v_cruise_mps",
                            "a_max_mps2",
                            "ds_m",
                            "hover_z_m",
                            "yaw_deg",
                        }
                        required_prof_keys = (
                            "v_cruise_mps",
                            "a_max_mps2",
                            "ds_m",
                            "hover_z_m",
                            "yaw_deg",
                        )
                        for pk in eprofile:
                            if pk not in allowed_prof_keys:
                                problems.append(f"{prof_path}.{pk}: unknown key")
                        for req_pk in required_prof_keys:
                            if req_pk not in eprofile:
                                problems.append(f"{prof_path}.{req_pk}: missing required key")

                        # v_cruise_mps in (0, WFB_TRAJ_V_MAX]
                        if "v_cruise_mps" in eprofile:
                            vc = eprofile["v_cruise_mps"]
                            if isinstance(vc, bool) or not isinstance(vc, (int, float)):
                                problems.append(f"{prof_path}.v_cruise_mps: must be numeric")
                            elif not math.isfinite(vc):
                                problems.append(f"{prof_path}.v_cruise_mps: must be finite")
                            elif not (0 < vc <= WFB_TRAJ_V_MAX):
                                problems.append(
                                    f"{prof_path}.v_cruise_mps: must be in range (0, {WFB_TRAJ_V_MAX}] (got {vc})"
                                )

                        # a_max_mps2 > 0
                        if "a_max_mps2" in eprofile:
                            am = eprofile["a_max_mps2"]
                            if isinstance(am, bool) or not isinstance(am, (int, float)):
                                problems.append(f"{prof_path}.a_max_mps2: must be numeric")
                            elif not math.isfinite(am):
                                problems.append(f"{prof_path}.a_max_mps2: must be finite")
                            elif am <= 0:
                                problems.append(
                                    f"{prof_path}.a_max_mps2: must be > 0 (got {am})"
                                )

                        # ds_m > 0
                        if "ds_m" in eprofile:
                            ds = eprofile["ds_m"]
                            if isinstance(ds, bool) or not isinstance(ds, (int, float)):
                                problems.append(f"{prof_path}.ds_m: must be numeric")
                            elif not math.isfinite(ds):
                                problems.append(f"{prof_path}.ds_m: must be finite")
                            elif ds <= 0:
                                problems.append(f"{prof_path}.ds_m: must be > 0 (got {ds})")

                        # hover_z_m in [WFB_HOVER_Z_MIN, WFB_HOVER_Z_MAX]
                        if "hover_z_m" in eprofile:
                            hz = eprofile["hover_z_m"]
                            if isinstance(hz, bool) or not isinstance(hz, (int, float)):
                                problems.append(f"{prof_path}.hover_z_m: must be numeric")
                            elif not math.isfinite(hz):
                                problems.append(f"{prof_path}.hover_z_m: must be finite")
                            elif not (WFB_HOVER_Z_MIN <= hz <= WFB_HOVER_Z_MAX):
                                problems.append(
                                    f"{prof_path}.hover_z_m: must be in range [{WFB_HOVER_Z_MIN}, {WFB_HOVER_Z_MAX}] (got {hz})"
                                )

                        # yaw_deg in [-180, 180]
                        if "yaw_deg" in eprofile:
                            yd = eprofile["yaw_deg"]
                            if isinstance(yd, bool) or not isinstance(yd, (int, float)):
                                problems.append(f"{prof_path}.yaw_deg: must be numeric")
                            elif not math.isfinite(yd):
                                problems.append(f"{prof_path}.yaw_deg: must be finite")
                            elif not (-180.0 <= yd <= 180.0):
                                problems.append(
                                    f"{prof_path}.yaw_deg: must be in range [-180, 180] (got {yd})"
                                )

                # Capture
                if "capture" in exp:
                    ecap = exp["capture"]
                    if isinstance(ecap, bool) or not isinstance(ecap, str):
                        problems.append(f"{exp_path}.capture: must be a string")
                    elif len(ecap.strip()) == 0:
                        problems.append(f"{exp_path}.capture: must be a non-empty string")

                # Repeats
                if "repeats" in exp:
                    erep = exp["repeats"]
                    if isinstance(erep, bool) or not isinstance(erep, int):
                        problems.append(f"{exp_path}.repeats: must be an integer")
                    elif erep < 1:
                        problems.append(f"{exp_path}.repeats: must be >= 1 (got {erep})")

    # 10. Field: abort (optional)
    if "abort" in data:
        abort_val = data["abort"]
        if not isinstance(abort_val, dict):
            problems.append("abort: must be a mapping")
        else:
            allowed_abort_keys = {f.name for f in dataclasses.fields(AbortLimits)}
            for ak, av in abort_val.items():
                abort_path = f"abort.{ak}"
                if ak not in allowed_abort_keys:
                    problems.append(f"{abort_path}: unknown key")
                else:
                    if isinstance(av, bool) or not isinstance(av, (int, float)):
                        problems.append(f"{abort_path}: must be numeric")
                    elif not math.isfinite(av):
                        problems.append(f"{abort_path}: must be finite")

    if problems:
        raise CampaignError(problems)

    # Construction of frozen dataclasses
    envelope_map: dict[str, EnvelopeLimit] = {
        sym: EnvelopeLimit(
            min=float(row["min"]),
            max=float(row["max"]),
            max_step=float(row["max_step"]),
        )
        for sym, row in data["envelope"].items()
    }

    experiments_list: list[Experiment] = []
    for exp in data["experiments"]:
        prof_dict = exp["profile"]
        profile_obj = Profile(
            v_cruise_mps=float(prof_dict["v_cruise_mps"]),
            a_max_mps2=float(prof_dict["a_max_mps2"]),
            ds_m=float(prof_dict["ds_m"]),
            hover_z_m=float(prof_dict["hover_z_m"]),
            yaw_deg=float(prof_dict.get("yaw_deg", 0.0)),
        )
        experiments_list.append(
            Experiment(
                name=str(exp["name"]),
                shape=str(exp["shape"]),
                params=dict(exp["params"]),
                profile=profile_obj,
                capture=str(exp["capture"]),
                repeats=int(exp["repeats"]),
            )
        )

    abort_map: dict[str, Any] = dict(data.get("abort", {}))

    return Campaign(
        campaign=str(data["campaign"]),
        objective=str(data["objective"]),
        controller=str(data["controller"]),
        packs=tuple(str(p) for p in data["packs"]),
        max_flights=int(data["max_flights"]),
        envelope=envelope_map,
        experiments=tuple(experiments_list),
        abort=abort_map,
    )


def load_campaign(path: str | os.PathLike) -> Campaign:
    """Load a campaign YAML file from disk and return the validated Campaign."""
    resolved_path = Path(path)
    try:
        with open(resolved_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise CampaignError([f"yaml: syntax error: {exc}"]) from exc
    except OSError as exc:
        raise CampaignError([f"file: cannot read '{resolved_path}': {exc}"]) from exc

    return parse_campaign(data)
