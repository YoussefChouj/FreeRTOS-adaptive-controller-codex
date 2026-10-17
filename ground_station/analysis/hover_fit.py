"""Fit hover throttle sag against battery voltage across all flights.

Fits u_hov(V) = c0 + c1*(V0 - V) from steady hover segments (position hold).
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ground_station.analysis import log_corpus as lc
from ground_station.analysis.battery_model import PackRegistry
from ground_station.autotune.frf import load_series

"""Fit hover throttle sag against battery voltage across all flights.

Fits u_hov(V) = c0 + c1*(V0 - V) from steady hover segments (position hold).
"""




# Constants / Thresholds

try:
    _reg = PackRegistry.load()
    V0 = _reg.ocv_table[0][0] * 4.0  # Assuming 4S
except Exception: # noqa: BLE001
    V0 = 16.7
PROPOSED_HOVER_THR_FREE = 3050.0       # baseline hover throttle free flight (from StabilizerTask.c)
PROPOSED_VZ_SMALL_MPS = 0.2            # threshold for "vz small"
PROPOSED_VXY_SMALL_MPS = 0.4           # threshold for "vxy small"
PROPOSED_HEIGHT_ERR_CM = 20.0          # threshold for "height near target"
PROPOSED_GE_HEIGHT_M = 0.40            # threshold for ground effect
PROPOSED_DT = 0.01                     # standard log dt (100 Hz core stream)


@dataclass
class HoverPoint:
    flight: str
    t: float
    vbat: float
    throttle: float
    tilt_correction: float
    ge: bool
    weight: float = 1.0


def extract_hover_points(series: lc.Series, flight_name: str, skip_s: float = 2.0, window_s: float = 1.0) -> list[HoverPoint]:
    """Find steady hover holds in a flight and return one data point per ~1s window."""
    # Handle telemetry aliases
    vkey = "status.vbat" if "status.vbat" in series else "real_voltage"
    rol_key = "status.roll_deg" if "status.roll_deg" in series else "imu_data.rol"
    pit_key = "status.pitch_deg" if "status.pitch_deg" in series else "imu_data.pit"

    keys = ["Ctrler.Z_ratePID.U", vkey, pit_key, rol_key,
            "Ctrler.Z_posPID.FB", "Ctrler.Z_posPID.Des", "Ctrler.locxsPID.FB", "Ctrler.locysPID.FB",
            "Ctrler.Z_ratePID.FB", "flight_phase", "id.sysid_state"]
    if not all(k in series for k in keys):
        # We also want to support missing id.sysid_state if no sysid happened
        keys.remove("id.sysid_state")
        if not all(k in series for k in keys):
            return []

    t, g = lc.grid(series, keys, PROPOSED_DT)
    if len(t) == 0:
        return []

    air = lc.airborne(series, t)

    # SysID check: either id.sysid_state doesn't exist, or it is 0 (IDLE)
    if "id.sysid_state" in series:
        # id.sysid_state might be sparsely logged or on a different timebase.
        # lc.grid holds values, so it's fine.
        sysid = g["id.sysid_state"] > 0
    else:
        sysid = np.zeros(len(t), bool)

    # Calculate |vz| and |vxy|

    vz = np.abs(g["Ctrler.Z_ratePID.FB"])
    vxy = np.sqrt(g["Ctrler.locxsPID.FB"]**2 + g["Ctrler.locysPID.FB"]**2) / 100.0 # locxsPID.FB in cm/s (TASK/StabilizerTask.c:1110)

    z_err = np.abs(g["Ctrler.Z_posPID.FB"] - g["Ctrler.Z_posPID.Des"]) * 100.0 # assume m, convert to cm

    # Steady hold mask
    steady = (
        air &
        (~sysid) &
        (vz < PROPOSED_VZ_SMALL_MPS) &
        (vxy < PROPOSED_VXY_SMALL_MPS) &
        (z_err < PROPOSED_HEIGHT_ERR_CM)
    )

    points = []
    # Find contiguous steady segments
    for a_t, b_t in lc.spans(steady, t, min_s=skip_s + window_s):
        # Chop into window_s windows, discarding the first skip_s
        n_windows = int((b_t - a_t - skip_s) // window_s)
        for i in range(n_windows):
            w_start = a_t + skip_s + i * window_s
            w_end = w_start + window_s
            mask = (t >= w_start) & (t < w_end)
            if not np.any(mask):
                continue

            # Extract means
            u_z = np.mean(g["Ctrler.Z_ratePID.U"][mask])
            vbat = np.mean(g[vkey][mask])
            height = np.mean(g["Ctrler.Z_posPID.FB"][mask])
            ge = height < PROPOSED_GE_HEIGHT_M

            # Tilt correction
            pit = np.radians(np.mean(g[pit_key][mask]))
            rol = np.radians(np.mean(g[rol_key][mask]))
            tilt = max(0.1, np.cos(pit) * np.cos(rol))

            points.append(HoverPoint(
                flight=flight_name,
                t=w_start,
                vbat=vbat,
                throttle=u_z + PROPOSED_HOVER_THR_FREE,
                tilt_correction=tilt,
                ge=ge
            ))

    return points

def base_flight_name(name):
    return re.sub(r'_\d+$', '', name)

def fit_model(points: list[HoverPoint], use_quad: bool = False) -> tuple[dict, list[float]]:
    """Fits u_hov*cosR*cosP = c0 + c1*(V0 - V) [ + c2*(V0 - V)^2 ] via weighted LS."""
    if len(points) < 2:
        return {}, []

    y = np.array([p.throttle * p.tilt_correction for p in points])
    v_delta = np.array([V0 - p.vbat for p in points])
    w = np.array([p.weight for p in points])

    if use_quad:
        X = np.column_stack([np.ones_like(v_delta), v_delta, v_delta**2])
    else:
        X = np.column_stack([np.ones_like(v_delta), v_delta])

    W = np.diag(w)
    try:
        theta, _residuals_sum, _rank, _s = np.linalg.lstsq(X.T @ W @ X, X.T @ W @ y, rcond=None)
        # Covariance matrix
        from scipy import stats
        preds = X @ theta
        w_residuals = np.sqrt(w) * (y - preds)
        df = len(y) - len(theta)
        cov = np.linalg.inv(X.T @ W @ X) * (np.sum(w_residuals**2) / df)
        t_val = stats.t.ppf(0.975, df) if df > 0 else 1.96
        ci = t_val * np.sqrt(np.diag(cov)) # 95% CI
    except np.linalg.LinAlgError:
        return {}, []

    rms = np.sqrt(np.average((y - preds)**2, weights=w))

    res = {
        "c0": float(theta[0]),
        "c0_95": float(ci[0]),
        "c1": float(theta[1]),
        "c1_95": float(ci[1]),
        "rms": float(rms)
    }
    if use_quad:
        res["c2"] = float(theta[2])
        res["c2_95"] = float(ci[2])

    return res, preds

def loocv(points: list[HoverPoint], use_quad: bool = False) -> tuple[float, dict[str, float]]:
    """Leave-one-flight-out cross validation RMS error.
    Returns (summary_rms, per_flight_rms)."""
        # Group by pack/session for hover_empty runs


    flights = list({base_flight_name(p.flight) for p in points})
    if len(flights) < 2:
        return float('nan'), {}

    errors = []
    weights = []
    per_flight = {}

    for f in flights:
        train = [p for p in points if base_flight_name(p.flight) != f]
        test = [p for p in points if base_flight_name(p.flight) == f]
        if len(train) < 2 or not test:
            continue

        fit, _ = fit_model(train, use_quad)
        if not fit:
            continue

        f_err = []
        f_w = []
        for p in test:
            vd = V0 - p.vbat
            pred = fit["c0"] + fit["c1"] * vd
            if use_quad:
                pred += fit["c2"] * vd**2
            y = p.throttle * p.tilt_correction
            f_err.append((y - pred)**2)
            f_w.append(p.weight)

        if f_err:
            per_flight[f] = float(np.sqrt(np.average(f_err, weights=f_w)))
            errors.extend(f_err)
            weights.extend(f_w)

    if not errors:
        return float('nan'), {}
    return float(np.sqrt(np.average(errors, weights=weights))), per_flight

def analyze(series_map: dict[str, lc.Series]) -> dict:
    points = []
    for name, series in series_map.items():
        points.extend(extract_hover_points(series, name))

    if not points:
        return {"error": "no steady hover points found"}

    # Downweight ground effect points (optional, let's say weight 0.5)
    for p in points:
        if p.ge:
            p.weight = 0.5

    linear, _ = fit_model(points, use_quad=False)
    if not linear:
        return {"error": "fit failed"}

    loocv_lin_rms, loocv_lin_per = loocv(points, use_quad=False)

    quad, _ = fit_model(points, use_quad=True)
    loocv_quad_rms, loocv_quad_per = loocv(points, use_quad=True)

    res = {
        "V0": V0,
        "n_points": len(points),
        "n_flights": len({base_flight_name(p.flight) for p in points}),
        "linear": linear,
        "linear_loocv_rms": loocv_lin_rms,
        "linear_loocv_per_flight": loocv_lin_per,
        "quadratic": quad,
        "quadratic_loocv_rms": loocv_quad_rms,
        "quadratic_loocv_per_flight": loocv_quad_per,
    }

    if np.isfinite(loocv_quad_rms) and loocv_quad_rms < 0.8 * loocv_lin_rms:
        res["best_model"] = "quadratic"
    else:
        res["best_model"] = "linear"

    return res

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("logs", nargs="+", help="paths to flight logs")
    ap.add_argument("--out", default=None, help="output path")
    args = ap.parse_args(argv)

    series_map = {}
    for p in args.logs:
        path = Path(p)
        if path.is_dir() or path.name.endswith(".csv"):
            try:
                # autotune.frf.load_series can load either
                series = load_series(p)
                series_map[path.name] = series
            except Exception as e: # noqa: BLE001
                print(f"Skipping {p}: {e}")

    if not series_map:
        print("No valid logs loaded.")
        return 1

    res = analyze(series_map)
    if args.out:
        Path(args.out).write_text(json.dumps(res, indent=2))
    else:
        print(json.dumps(res, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
