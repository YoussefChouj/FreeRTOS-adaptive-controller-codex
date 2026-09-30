"""PID loop rules (spec section 7).

All thresholds and algorithms below are HEURISTIC.
"""
from __future__ import annotations

from ..registry import Recommendation, get_path, register_rule


@register_rule(requires=["loops"])
def pid_osc(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag oscillation in rate or attitude loops (HEURISTIC)."""
    loops = metrics.get("loops")
    if not isinstance(loops, dict):
        return []

    t_cfg = cfg["thresholds"]["PID-OSC"]
    osc_ratio_thresh = t_cfg["osc_ratio"]
    osc_fmin = t_cfg["osc_fmin"]
    d_band_hz = t_cfg["d_band_hz"]
    factor = t_cfg["factor"]

    recs: list[Recommendation] = []
    for loop_name, loop_data in loops.items():
        if not isinstance(loop_data, dict):
            continue
        level = loop_data.get("level")
        if level not in ("rate", "attitude"):
            continue

        steady = loop_data.get("steady")
        if not isinstance(steady, dict):
            continue

        ratio = steady.get("osc_peak_ratio")
        f_hz = steady.get("osc_peak_hz")
        if ratio is None or f_hz is None:
            continue

        if ratio > osc_ratio_thresh and f_hz > osc_fmin:
            prefix = loop_data.get("prefix")
            if not prefix:
                continue
            if f_hz > d_band_hz:
                target = f"{prefix}.Kd"
            else:
                target = f"{prefix}.Kp"

            recs.append(
                Recommendation(
                    id=f"PID-OSC-{loop_name}",
                    severity="warn",
                    category="pid",
                    target=target,
                    action="decrease",
                    factor=factor,
                    evidence={
                        f"loops.{loop_name}.steady.osc_peak_ratio": ratio,
                        f"loops.{loop_name}.steady.osc_peak_hz": f_hz,
                    },
                    rationale=(
                        f"Loop {loop_name} oscillation detected at {f_hz:.1f} Hz (ratio {ratio:.1f}); "
                        f"decrease {target} by factor {factor}."
                    ),
                    confidence="low",
                )
            )
    return recs


@register_rule(requires=["loops"])
def pid_bias(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag steady-state bias exceeding noise floor and absolute threshold (HEURISTIC)."""
    loops = metrics.get("loops")
    if not isinstance(loops, dict):
        return []

    t_cfg = cfg["thresholds"]["PID-BIAS"]
    bias_k = t_cfg["bias_k"]
    bias_abs_dict = t_cfg["bias_abs"]

    recs: list[Recommendation] = []
    for loop_name, loop_data in loops.items():
        if not isinstance(loop_data, dict):
            continue
        level = loop_data.get("level")
        if level not in bias_abs_dict:
            continue
        bias_abs_thresh = float(bias_abs_dict[level])

        steady = loop_data.get("steady")
        if not isinstance(steady, dict):
            continue

        e_mean = steady.get("e_mean")
        e_std = steady.get("e_std")
        if e_mean is None or e_std is None:
            continue

        if abs(e_mean) > bias_k * e_std and abs(e_mean) > bias_abs_thresh:
            prefix = loop_data.get("prefix")
            if not prefix:
                continue
            target = f"{prefix}.Ki"

            recs.append(
                Recommendation(
                    id=f"PID-BIAS-{loop_name}",
                    severity="warn",
                    category="pid",
                    target=target,
                    action="increase",
                    factor=None,
                    evidence={
                        f"loops.{loop_name}.steady.e_mean": e_mean,
                        f"loops.{loop_name}.steady.e_std": e_std,
                    },
                    rationale=(
                        f"Loop {loop_name} steady bias (|mean| {abs(e_mean):.3f} > {bias_abs_thresh}); "
                        f"increase {target} or check integrator gating."
                    ),
                    confidence="low",
                )
            )
    return recs


@register_rule(requires=["loops"])
def pid_iwindup(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag integrator saturation over airborne (HEURISTIC)."""
    loops = metrics.get("loops")
    if not isinstance(loops, dict):
        return []

    t_cfg = cfg["thresholds"]["PID-IWINDUP"]
    sat_warn = t_cfg["sat_warn"]

    recs: list[Recommendation] = []
    for loop_name, loop_data in loops.items():
        if not isinstance(loop_data, dict):
            continue
        airborne = loop_data.get("airborne")
        if not isinstance(airborne, dict):
            continue

        sume_sat_frac = airborne.get("sume_sat_frac")
        if sume_sat_frac is None:
            continue

        if sume_sat_frac > sat_warn:
            prefix = loop_data.get("prefix")
            if not prefix:
                continue
            target = f"{prefix}.SumEMax"

            recs.append(
                Recommendation(
                    id=f"PID-IWINDUP-{loop_name}",
                    severity="warn",
                    category="pid",
                    target=target,
                    action="increase",
                    factor=None,
                    evidence={f"loops.{loop_name}.airborne.sume_sat_frac": sume_sat_frac},
                    rationale=(
                        f"Loop {loop_name} integrator saturation fraction ({sume_sat_frac:.3f}) exceeds threshold ({sat_warn}); "
                        f"raise {target}."
                    ),
                    confidence="low",
                )
            )
    return recs


@register_rule(requires=["loops"])
def pid_sat(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag actuator/control saturation over airborne (HEURISTIC)."""
    loops = metrics.get("loops")
    if not isinstance(loops, dict):
        return []

    t_cfg = cfg["thresholds"]["PID-SAT"]
    sat_warn = t_cfg["sat_warn"]

    recs: list[Recommendation] = []
    for loop_name, loop_data in loops.items():
        if not isinstance(loop_data, dict):
            continue
        airborne = loop_data.get("airborne")
        if not isinstance(airborne, dict):
            continue

        u_sat_frac = airborne.get("u_sat_frac")
        if u_sat_frac is None:
            continue

        if u_sat_frac > sat_warn:
            recs.append(
                Recommendation(
                    id=f"PID-SAT-{loop_name}",
                    severity="warn",
                    category="pid",
                    target=None,
                    action="investigate",
                    factor=None,
                    evidence={f"loops.{loop_name}.airborne.u_sat_frac": u_sat_frac},
                    rationale=(
                        f"Loop {loop_name} output saturation fraction ({u_sat_frac:.3f}) exceeds threshold ({sat_warn}); "
                        "gain changes will not help; check trim, hardware, or limits."
                    ),
                    confidence="low",
                )
            )
    return recs


@register_rule(requires=["loops"])
def pid_lag(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag tracking lag under significant setpoint excitation (HEURISTIC)."""
    loops = metrics.get("loops")
    if not isinstance(loops, dict):
        return []

    t_cfg = cfg["thresholds"]["PID-LAG"]
    excite_min = t_cfg["excite_min"]
    lag_deg = t_cfg["lag_deg"]
    gain_min = t_cfg["gain_min"]
    factor = t_cfg["factor"]

    recs: list[Recommendation] = []
    for loop_name, loop_data in loops.items():
        if not isinstance(loop_data, dict):
            continue
        airborne = loop_data.get("airborne")
        if not isinstance(airborne, dict):
            continue

        des_std = airborne.get("des_std")
        phase_deg = airborne.get("track_phase_deg")
        gain = airborne.get("track_gain")
        if des_std is None or phase_deg is None or gain is None:
            continue

        if des_std > excite_min and (phase_deg < -lag_deg or gain < gain_min):
            prefix = loop_data.get("prefix")
            if not prefix:
                continue
            target = f"{prefix}.Kp"

            recs.append(
                Recommendation(
                    id=f"PID-LAG-{loop_name}",
                    severity="warn",
                    category="pid",
                    target=target,
                    action="increase",
                    factor=factor,
                    evidence={
                        f"loops.{loop_name}.airborne.des_std": des_std,
                        f"loops.{loop_name}.airborne.track_phase_deg": phase_deg,
                        f"loops.{loop_name}.airborne.track_gain": gain,
                    },
                    rationale=(
                        f"Loop {loop_name} tracking lag detected (phase {phase_deg:.1f} deg, gain {gain:.2f}); "
                        f"increase {target} by factor {factor}."
                    ),
                    confidence="low",
                )
            )
    return recs
