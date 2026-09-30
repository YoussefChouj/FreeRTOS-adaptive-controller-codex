"""MRAC rules (spec section 7).

All thresholds and algorithms below are HEURISTIC.
"""
from __future__ import annotations

import math
from typing import Any

from ..registry import Recommendation, get_path, register_rule


def _weight_status(w_data: dict, cw: float, min_change: float) -> str:
    """Classifies weight status: 'drifting', 'converged', or 'unknown' (HEURISTIC).

    - DRIFTS iff converged is False and slope_last30 is not None and abs(slope_last30) * cw >= min_change.
    - EFFECTIVELY CONVERGED iff converged is True, or (converged is False and slope_last30 is not None
      and abs(slope_last30) * cw < min_change).
    - converged None -> neither (unknown).
    """
    if not isinstance(w_data, dict):
        return "unknown"
    conv = w_data.get("converged")
    if conv is None:
        return "unknown"
    if conv is True:
        return "converged"
    slope = w_data.get("slope_last30")
    if slope is None or not isinstance(slope, (int, float)) or not math.isfinite(slope):
        return "unknown"
    if abs(slope) * cw >= min_change:
        return "drifting"
    return "converged"


@register_rule(requires=["mrac"])
def mrac_ready(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag when MRAC axis is ready for active injection (HEURISTIC)."""
    mode = get_path(metrics, "controller.mrac_mode")
    if mode != "shadow":
        return []

    mrac = metrics.get("mrac")
    if not isinstance(mrac, dict):
        return []

    t_cfg = cfg["thresholds"]["MRAC-READY"]
    ready_lo = t_cfg["ready_lo"]
    ready_hi = t_cfg["ready_hi"]
    hf_max = t_cfg["hf_max"]

    w_cfg = cfg["thresholds"]["MRAC-DRIFT"]
    min_change = w_cfg["min_change"]
    cw = cfg["params"]["mrac"]["conv_window_s"]

    recs: list[Recommendation] = []
    for axis, axis_data in mrac.items():
        if not isinstance(axis_data, dict):
            continue
        steady = axis_data.get("steady")
        if not isinstance(steady, dict):
            continue

        auth = steady.get("authority_ratio")
        hf_frac = steady.get("u_ad_hf_frac")
        if auth is None or hf_frac is None:
            continue

        if not (ready_lo <= auth <= ready_hi):
            continue
        if not (hf_frac < hf_max):
            continue

        weights = axis_data.get("weights")
        if not isinstance(weights, dict) or not weights:
            continue

        all_converged = True
        for _, w_data in weights.items():
            st = _weight_status(w_data, cw, min_change)
            if st != "converged":
                all_converged = False
                break

        if all_converged:
            target = "mrac_flags.output_injection_on"
            recs.append(
                Recommendation(
                    id=f"MRAC-READY-{axis}",
                    severity="info",
                    category="mrac",
                    target=target,
                    action="enable",
                    factor=None,
                    evidence={
                        f"mrac.{axis}.steady.authority_ratio": auth,
                        f"mrac.{axis}.steady.u_ad_hf_frac": hf_frac,
                    },
                    rationale=(
                        f"MRAC axis {axis} is ready for active mode (authority ratio {auth:.2f}, "
                        f"hf fraction {hf_frac:.2f}, all weights converged); enable {target}."
                    ),
                    confidence="low",
                )
            )
    return recs


@register_rule(requires=["mrac"])
def mrac_auth(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag excessive MRAC authority ratio over steady flight (HEURISTIC)."""
    mode = get_path(metrics, "controller.mrac_mode")
    if mode not in ("shadow", "active"):
        return []

    mrac = metrics.get("mrac")
    if not isinstance(mrac, dict):
        return []

    t_cfg = cfg["thresholds"]["MRAC-AUTH"]
    auth_max = t_cfg["auth_max"]

    recs: list[Recommendation] = []
    for axis, axis_data in mrac.items():
        if not isinstance(axis_data, dict):
            continue
        steady = axis_data.get("steady")
        if not isinstance(steady, dict):
            continue

        auth = steady.get("authority_ratio")
        if auth is None:
            continue

        if auth > auth_max:
            recs.append(
                Recommendation(
                    id=f"MRAC-AUTH-{axis}",
                    severity="warn",
                    category="mrac",
                    target=None,
                    action="investigate",
                    factor=None,
                    evidence={f"mrac.{axis}.steady.authority_ratio": auth},
                    rationale=(
                        f"MRAC axis {axis} steady authority ratio ({auth:.2f}) exceeds threshold ({auth_max}); "
                        "adaptive term would dominate the nominal PID; check gamma / regressor scaling "
                        "before enabling injection."
                    ),
                    confidence="medium",
                )
            )
    return recs


@register_rule(requires=["mrac"])
def mrac_drift(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag drifting adaptive weights over airborne (HEURISTIC)."""
    mrac = metrics.get("mrac")
    if not isinstance(mrac, dict):
        return []

    w_cfg = cfg["thresholds"]["MRAC-DRIFT"]
    min_change = w_cfg["min_change"]
    cw = cfg["params"]["mrac"]["conv_window_s"]

    recs: list[Recommendation] = []
    for axis, axis_data in mrac.items():
        if not isinstance(axis_data, dict):
            continue
        weights = axis_data.get("weights")
        if not isinstance(weights, dict) or not weights:
            continue

        drifting: list[str] = []
        evidence: dict[str, Any] = {}
        for w_name, w_data in sorted(weights.items()):
            if _weight_status(w_data, cw, min_change) == "drifting":
                drifting.append(w_name)
                evidence[f"mrac.{axis}.weights.{w_name}.slope_last30"] = w_data.get("slope_last30")
                evidence[f"mrac.{axis}.weights.{w_name}.final"] = w_data.get("final")

        if drifting:
            recs.append(
                Recommendation(
                    id=f"MRAC-DRIFT-{axis}",
                    severity="warn",
                    category="mrac",
                    target=None,
                    action="investigate",
                    factor=None,
                    evidence=evidence,
                    rationale=(
                        f"MRAC axis {axis} has drifting weights ({', '.join(drifting)}); "
                        "reduce gamma or enable projection before active."
                    ),
                    confidence="low",
                )
            )
    return recs


@register_rule(requires=["mrac"])
def mrac_chatter(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag high-frequency chatter in MRAC control signal (HEURISTIC)."""
    mrac = metrics.get("mrac")
    if not isinstance(mrac, dict):
        return []

    t_cfg = cfg["thresholds"]["MRAC-CHATTER"]
    hf_max = t_cfg["hf_max"]

    recs: list[Recommendation] = []
    for axis, axis_data in mrac.items():
        if not isinstance(axis_data, dict):
            continue
        steady = axis_data.get("steady")
        if not isinstance(steady, dict):
            continue

        hf_frac = steady.get("u_ad_hf_frac")
        if hf_frac is None:
            continue

        if hf_frac > hf_max:
            recs.append(
                Recommendation(
                    id=f"MRAC-CHATTER-{axis}",
                    severity="warn",
                    category="mrac",
                    target=None,
                    action="investigate",
                    factor=None,
                    evidence={f"mrac.{axis}.steady.u_ad_hf_frac": hf_frac},
                    rationale=(
                        f"MRAC axis {axis} steady high-frequency fraction ({hf_frac:.3f}) exceeds threshold ({hf_max}); "
                        "low-pass u_ad or reduce gamma."
                    ),
                    confidence="low",
                )
            )
    return recs


@register_rule(requires=["mrac"])
def mrac_worse(metrics: dict, cfg: dict, ctx: dict) -> list[Recommendation]:
    """Flag axes where active MRAC performs worse than non-active baseline (HEURISTIC)."""
    mode = get_path(metrics, "controller.mrac_mode")
    if mode != "active":
        return []

    flight_preset = get_path(metrics, "flight.preset")
    flight_name = get_path(metrics, "flight.name")
    flight_started = get_path(metrics, "flight.started_at")
    if not (isinstance(flight_started, str) and flight_started.strip()):
        return []
    if not flight_name or not flight_preset:
        return []

    ledger_rows = ctx.get("ledger_rows") if isinstance(ctx, dict) else None
    if not isinstance(ledger_rows, list) or not ledger_rows:
        return []

    candidates = []
    for row in ledger_rows:
        if not isinstance(row, dict):
            continue
        if row.get("preset") != flight_preset:
            continue
        if row.get("mrac_mode") == "active":
            continue
        if row.get("flight") == flight_name:
            continue
        row_started = row.get("started_at")
        if not (isinstance(row_started, str) and row_started.strip()):
            continue
        if row_started >= flight_started:
            continue
        candidates.append(row)

    if not candidates:
        return []

    baseline_row = max(candidates, key=lambda r: r["started_at"])
    baseline_flight = baseline_row.get("flight")

    t_cfg = cfg["thresholds"]["MRAC-WORSE"]
    worse_pct = t_cfg["worse_pct"]

    axes_cfg = cfg["mrac"]["axes"]
    mrac_metrics = metrics.get("mrac", {})
    if not isinstance(mrac_metrics, dict):
        return []

    recs: list[Recommendation] = []
    for axis, axis_info in axes_cfg.items():
        if axis not in mrac_metrics:
            continue
        loop = axis_info.get("loop")
        if not loop:
            continue

        current = get_path(metrics, f"loops.{loop}.steady.e_rms")
        if current is None or not isinstance(current, (int, float)) or not math.isfinite(current):
            continue

        base_col = f"e_rms_steady_{loop}"
        if base_col not in baseline_row:
            continue
        try:
            base = float(baseline_row[base_col])
        except (ValueError, TypeError):
            continue
        if not math.isfinite(base) or base <= 0:
            continue

        if current > base * (1.0 + worse_pct / 100.0):
            target = "mrac_flags.output_injection_on"
            recs.append(
                Recommendation(
                    id=f"MRAC-WORSE-{axis}",
                    severity="warn",
                    category="mrac",
                    target=target,
                    action="disable",
                    factor=None,
                    evidence={
                        f"loops.{loop}.steady.e_rms": current,
                        f"ledger.{baseline_flight}.e_rms_steady_{loop}": base,
                    },
                    rationale=(
                        f"MRAC axis {axis} steady tracking error RMS ({current:.4f}) is worse than "
                        f"baseline {baseline_flight} ({base:.4f}) by > {worse_pct:.1f}%; "
                        f"disable injection via global flag {target}."
                    ),
                    confidence="low",
                )
            )
    return recs
