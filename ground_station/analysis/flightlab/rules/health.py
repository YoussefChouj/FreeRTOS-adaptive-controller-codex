"""Health rules for flight data (spec section 7).

All thresholds and algorithms below are HEURISTIC.
"""
from __future__ import annotations

import fnmatch
from typing import Any

from ..registry import Recommendation, get_path, register_rule


@register_rule(requires=["data_quality.slots"])
def dq_drop(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag slots with excessive packet drop rates (HEURISTIC)."""
    t_cfg = cfg["thresholds"]["DQ-DROP"]
    drop_warn = t_cfg["drop_warn"]
    drop_crit = t_cfg["drop_crit"]

    slots = get_path(metrics, "data_quality.slots")
    if not isinstance(slots, list):
        return []

    recs: list[Recommendation] = []
    for idx, slot in enumerate(slots):
        if not isinstance(slot, dict):
            continue
        drop_pct = slot.get("drop_pct")
        if drop_pct is None:
            continue
        slot_idx = slot.get("index", idx)
        if drop_pct > drop_crit:
            severity = "critical"
        elif drop_pct > drop_warn:
            severity = "warn"
        else:
            continue

        recs.append(
            Recommendation(
                id=f"DQ-DROP-slot{slot_idx}",
                severity=severity,
                category="data",
                target=None,
                action="investigate",
                factor=None,
                evidence={f"data_quality.slots.{idx}.drop_pct": drop_pct},
                rationale=(
                    f"Slot {slot_idx} packet drop rate ({drop_pct:.2f}%) exceeds threshold; "
                    "conclusions weakened; check WiFi."
                ),
                confidence="low",
            )
        )
    return recs


@register_rule(requires=["loops"])
def dq_missing_loops(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag loops missing a subset of core logged variables (HEURISTIC)."""
    loops = metrics.get("loops")
    if not isinstance(loops, dict):
        return []

    fields = cfg["pid_fields"]
    recs: list[Recommendation] = []
    for loop_name, loop_data in loops.items():
        if not isinstance(loop_data, dict):
            continue
        missing = loop_data.get("missing")
        prefix = loop_data.get("prefix")
        if not isinstance(missing, list) or not missing or not prefix:
            continue

        core = [f"{prefix}.{fields[k]}" for k in ("des", "fb", "u")]
        if any(name not in missing for name in core):
            recs.append(
                Recommendation(
                    id=f"DQ-MISSING-{loop_name}",
                    severity="info",
                    category="logging",
                    target=None,
                    action="add_to_preset",
                    factor=None,
                    evidence={f"loops.{loop_name}.missing": missing},
                    rationale=(
                        f"Loop {loop_name} has partial variables streamed; "
                        f"add {', '.join(missing)} to the preset."
                    ),
                    confidence="low",
                )
            )
    return recs


@register_rule(requires=["mrac"])
def dq_missing_axes(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag MRAC axes missing a subset of per-axis fields (HEURISTIC)."""
    mrac = metrics.get("mrac")
    if not isinstance(mrac, dict):
        return []

    num_fields = len(cfg["mrac"]["fields"])

    recs: list[Recommendation] = []
    for axis, axis_data in mrac.items():
        if not isinstance(axis_data, dict):
            continue
        missing = axis_data.get("missing")
        if not isinstance(missing, list):
            continue
        if 0 < len(missing) < num_fields:
            recs.append(
                Recommendation(
                    id=f"DQ-MISSING-{axis}",
                    severity="info",
                    category="logging",
                    target=None,
                    action="add_to_preset",
                    factor=None,
                    evidence={f"mrac.{axis}.missing": missing},
                    rationale=(
                        f"MRAC axis {axis} has partial variables streamed; "
                        f"add {', '.join(missing)} to the preset."
                    ),
                    confidence="low",
                )
            )
    return recs


@register_rule(requires=["data_quality.stuck_vars"])
def dq_stuck(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag variables stuck over airborne not matching expected constants (HEURISTIC)."""
    stuck_vars = get_path(metrics, "data_quality.stuck_vars")
    if not isinstance(stuck_vars, list) or not stuck_vars:
        return []

    t_cfg = cfg["thresholds"]["DQ-STUCK"]
    expected_const = t_cfg["expected_const"]

    unexpected: list[str] = []
    for var in stuck_vars:
        matched = any(fnmatch.fnmatchcase(var, pat) for pat in expected_const)
        if not matched:
            unexpected.append(var)

    if not unexpected:
        return []

    return [
        Recommendation(
            id="DQ-STUCK",
            severity="info",
            category="data",
            target=None,
            action="investigate",
            factor=None,
            evidence={"data_quality.stuck_vars": unexpected},
            rationale=(
                f"{len(unexpected)} variable(s) did not change while airborne and match no "
                "expected_const pattern; check logging or the sensor (names in evidence)."
            ),
            confidence="low",
        )
    ]


@register_rule(requires=["loops"])
def log_gains(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag when any loop gains are not streamed (HEURISTIC)."""
    loops = metrics.get("loops")
    if not isinstance(loops, dict):
        return []

    missing_loops: list[str] = []
    evidence: dict[str, Any] = {}
    for loop_name, loop_data in loops.items():
        if not isinstance(loop_data, dict):
            continue
        gains = loop_data.get("gains")
        if gains is None or gains.get("Kp") is None:
            missing_loops.append(loop_name)
            evidence[f"loops.{loop_name}.gains"] = gains

    if not missing_loops:
        return []

    return [
        Recommendation(
            id="LOG-GAINS",
            severity="info",
            category="logging",
            target=None,
            action="add_to_preset",
            factor=None,
            evidence=evidence,
            rationale=(
                f"PID gains not streamed for loops {', '.join(missing_loops)}; "
                "add a 1 Hz params slot with the PID structs (limits known next flight)."
            ),
            confidence="low",
        )
    ]


@register_rule(requires=["motors.airborne.clamp_hi_frac"])
def mot_clamp(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag motor high-clamping fraction over airborne (HEURISTIC)."""
    clamp_hi = get_path(metrics, "motors.airborne.clamp_hi_frac")
    if clamp_hi is None:
        return []

    t_cfg = cfg["thresholds"]["MOT-CLAMP"]
    clamp_warn = t_cfg["clamp_warn"]
    clamp_crit = t_cfg["clamp_crit"]

    if clamp_hi > clamp_crit:
        severity = "critical"
    elif clamp_hi > clamp_warn:
        severity = "warn"
    else:
        return []

    return [
        Recommendation(
            id="MOT-CLAMP",
            severity=severity,
            category="hardware",
            target=None,
            action="investigate",
            factor=None,
            evidence={"motors.airborne.clamp_hi_frac": clamp_hi},
            rationale=(
                f"Motor clamp high fraction ({clamp_hi:.3f}) exceeds threshold; "
                "no thrust headroom; check hardware or battery."
            ),
            confidence="low",
        )
    ]


@register_rule(requires=["motors.steady.yaw_pair_pct"])
def mot_yawpair(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag motor yaw-pair thrust imbalance over steady flight (HEURISTIC)."""
    yaw_pair_pct = get_path(metrics, "motors.steady.yaw_pair_pct")
    if yaw_pair_pct is None:
        return []

    t_cfg = cfg["thresholds"]["MOT-YAWPAIR"]
    yawpair_warn = t_cfg["yawpair_warn"]

    if abs(yaw_pair_pct) <= yawpair_warn:
        return []

    return [
        Recommendation(
            id="MOT-YAWPAIR",
            severity="warn",
            category="hardware",
            target=None,
            action="investigate",
            factor=None,
            evidence={"motors.steady.yaw_pair_pct": yaw_pair_pct},
            rationale=(
                f"Motor yaw-pair imbalance ({yaw_pair_pct:.2f}%) exceeds threshold ({yawpair_warn}%); "
                "CW/CCW thrust imbalance; check props or motors."
            ),
            confidence="low",
        )
    ]


@register_rule(requires=["battery.v_min_airborne_cell"])
def bat_low(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag low minimum cell voltage during airborne (HEURISTIC)."""
    v_min = get_path(metrics, "battery.v_min_airborne_cell")
    if v_min is None:
        return []

    t_cfg = cfg["thresholds"]["BAT-LOW"]
    cell_warn = t_cfg["cell_warn"]
    cell_crit = t_cfg["cell_crit"]

    if v_min < cell_crit:
        severity, limit = "critical", cell_crit
    elif v_min < cell_warn:
        severity, limit = "warn", cell_warn
    else:
        return []

    return [
        Recommendation(
            id="BAT-LOW",
            severity=severity,
            category="battery",
            target=None,
            action="investigate",
            factor=None,
            evidence={"battery.v_min_airborne_cell": v_min},
            rationale=(
                f"Minimum airborne cell voltage {v_min:.2f} V is below the {severity} "
                f"threshold {limit:.2f} V; land earlier."
            ),
            confidence="low",
        )
    ]


@register_rule(requires=["battery.sag_v_cell"])
def bat_sag(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag high battery voltage sag per cell (HEURISTIC)."""
    sag_v = get_path(metrics, "battery.sag_v_cell")
    if sag_v is None:
        return []

    t_cfg = cfg["thresholds"]["BAT-SAG"]
    sag_warn = t_cfg["sag_warn"]

    if sag_v <= sag_warn:
        return []

    return [
        Recommendation(
            id="BAT-SAG",
            severity="warn",
            category="battery",
            target=None,
            action="investigate",
            factor=None,
            evidence={"battery.sag_v_cell": sag_v},
            rationale=(
                f"Battery voltage sag ({sag_v:.2f} V/cell) exceeds threshold ({sag_warn} V); "
                "check battery health or internal resistance."
            ),
            confidence="low",
        )
    ]


@register_rule(requires=["loops.alt_pos.steady.e_mean"])
def alt_sag(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag steady altitude sag where drone stays below setpoint (HEURISTIC)."""
    e_mean = get_path(metrics, "loops.alt_pos.steady.e_mean")
    if e_mean is None:
        return []

    t_cfg = cfg["thresholds"]["ALT-SAG"]
    alt_sag_m = t_cfg["alt_sag_m"]

    if e_mean <= alt_sag_m:
        return []

    alt_rate_prefix = get_path(metrics, "loops.alt_rate.prefix")
    if alt_rate_prefix:
        target = f"{alt_rate_prefix}.Ki"
        action = "increase"
    else:
        target = None
        action = "investigate"

    return [
        Recommendation(
            id="ALT-SAG",
            severity="warn",
            category="pid",
            target=target,
            action=action,
            factor=None,
            evidence={"loops.alt_pos.steady.e_mean": e_mean},
            rationale=(
                f"Steady altitude error ({e_mean:.3f} m) indicates altitude below setpoint; "
                f"raise hover-thrust feedforward or increase {target or 'integrator gain'}."
            ),
            confidence="low",
        )
    ]
