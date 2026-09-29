"""Flight segmentation by phase and controller state (spec section 5, Contract B).

Identifies armed, airborne, landing, and steady flight intervals.
"""
from __future__ import annotations

import numpy as np

from .model import FlightLog


def mask_intervals(t: np.ndarray, mask: np.ndarray, rate_hz: float) -> list[tuple[float, float]]:
    """Convert boolean mask on a signal's own timebase into [t0, t1) intervals.

    An interval starts at the t of the first True sample of a run and ends at the t
    of the first False sample after it. A run lasting to the last sample ends at
    t_last + 1 / rate_hz.
    """
    if t.size == 0 or mask.size == 0:
        return []
    intervals: list[tuple[float, float]] = []
    in_run = False
    t0 = 0.0
    dt = (1.0 / float(rate_hz)) if rate_hz > 0 else 0.0
    n = len(mask)
    for i in range(n):
        if mask[i] and not in_run:
            in_run = True
            t0 = float(t[i])
        elif not mask[i] and in_run:
            in_run = False
            intervals.append((t0, float(t[i])))
    if in_run:
        intervals.append((t0, float(t[-1] + dt)))
    return intervals


def intersect_intervals(
    int1: list[tuple[float, float]],
    int2: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Compute the intersection of two lists of sorted, disjoint intervals."""
    out: list[tuple[float, float]] = []
    for a0, a1 in int1:
        for b0, b1 in int2:
            start = max(a0, b0)
            end = min(a1, b1)
            if start < end:
                out.append((float(start), float(end)))
    return out


def segment(log: FlightLog, cfg: dict) -> dict:
    """Segment the flight log into armed, airborne, landing, and steady intervals.

    Returns dict with keys: 'armed', 'airborne', 'landing', 'steady'
    (each a list of (t0, t1) python-float tuples, sorted, non-overlapping)
    and 'warnings' (list of str).
    """
    phase_cfg = cfg.get("phase", {})
    arm_var = phase_cfg.get("arm_var", "DroneStatus.ARM_Status")
    phase_var = phase_cfg.get("var", "flight_phase")
    values = phase_cfg.get("values", {})
    flying_val = float(values.get("FLYING", 1))
    landing_val = float(values.get("LANDING", 2))

    has_arm = log.has(arm_var)
    has_phase = log.has(phase_var)
    warnings: list[str] = []

    armed: list[tuple[float, float]] = []
    airborne: list[tuple[float, float]] = []
    landing: list[tuple[float, float]] = []

    if not has_arm:
        warnings.append(f"{arm_var} absent")

    if not has_phase:
        warnings.append("flight_phase absent: airborne = armed")

    if has_arm:
        s_arm = log.get(arm_var)
        arm_mask = np.isclose(s_arm.v, 1.0)
        armed = mask_intervals(s_arm.t, arm_mask, s_arm.rate_hz)

    if has_phase:
        s_phase = log.get(phase_var)
        air_mask = np.isclose(s_phase.v, flying_val)
        airborne = mask_intervals(s_phase.t, air_mask, s_phase.rate_hz)
        land_mask = np.isclose(s_phase.v, landing_val)
        landing = mask_intervals(s_phase.t, land_mask, s_phase.rate_hz)
    else:
        airborne = list(armed)
        landing = []

    seg_cfg = cfg.get("segments", {})
    takeoff_settle_s = float(seg_cfg.get("takeoff_settle_s", 3.0))
    pre_land_s = float(seg_cfg.get("pre_land_s", 1.0))
    hold_window_s = float(seg_cfg.get("hold_window_s", 2.0))
    min_steady_s = float(seg_cfg.get("min_steady_s", 5.0))

    steady_candidates: list[tuple[float, float]] = []
    for t0, t1 in airborne:
        st0 = t0 + takeoff_settle_s
        st1 = t1 - pre_land_s
        if st1 > st0:
            steady_candidates.append((float(st0), float(st1)))

    loops_cfg = cfg.get("loops", {})
    for loop_name, loop_def in loops_cfg.items():
        if not isinstance(loop_def, dict) or "des_hold_tol" not in loop_def:
            continue
        tol = float(loop_def["des_hold_tol"])
        prefix = loop_def.get("prefix", "")
        des_name = f"{prefix}.Des"
        if not log.has(des_name):
            continue

        des_sig = log.get(des_name)
        t_des = des_sig.t
        v_des = des_sig.v
        n_samples = len(t_des)
        if n_samples == 0:
            steady_candidates = []
            break

        i0 = np.searchsorted(t_des, t_des - hold_window_s, side="left")
        held = np.zeros(n_samples, dtype=bool)
        for k in range(n_samples):
            window = v_des[i0[k] : k + 1]
            finite = window[np.isfinite(window)]
            if finite.size > 0:
                if np.ptp(finite, axis=0) < tol:
                    held[k] = True

        des_intervals = mask_intervals(t_des, held, des_sig.rate_hz)
        steady_candidates = intersect_intervals(steady_candidates, des_intervals)

    steady = [(float(a), float(b)) for a, b in steady_candidates if (b - a) >= min_steady_s]

    return {
        "armed": armed,
        "airborne": airborne,
        "landing": landing,
        "steady": steady,
        "warnings": warnings,
    }
