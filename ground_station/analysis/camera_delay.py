"""In-flight camera delay from the xy delay flight (lab_x_delay): how far the camera's xy lags the drone's own xy.

The camera fix of a frame read at epoch t shows where the drone was at t - L. The drone's estimator xy
(Ctrler.loc{x,y}PID.FB, telemetry) moves with the same steps, so L is the shift that best lines up the two
velocities (video_truth.sync_xcorr on 50 Hz grids). Velocity, not position: the camera offset (mode 2) and any
constant frame misalignment drop out.

Clocks: fixes.csv t_epoch is the read time (time.time() at send), telemetry received_ns is the receive time. The
telemetry itself arrives one WiFi hop late, so L here = photon-to-read delay minus that one-way link latency (size
UNVERIFIED). The tracker's own setting (--delay-ms) is t_send - t_cap in fixes.csv; the report prints both.

    python -m ground_station.analysis.camera_delay <session dir> [--fixes <fixes.csv>] [--flight <name>]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from ground_station.analysis.lab_flights import _find_fixes_csv
from ground_station.analysis.video_truth import sync_xcorr

DT = 0.02


def velocity(t, x, dt: float = DT, smooth_s: float = 0.2):
    """Velocity on a uniform grid: interpolate, moving-average over smooth_s, central difference."""
    g = np.arange(t[0], t[-1], dt)
    y = np.interp(g, t, x)
    k = max(1, int(round(smooth_s / dt)))
    y = np.convolve(y, np.ones(k) / k, mode="same")
    return g[1:-1], (y[2:] - y[:-2]) / (2 * dt)


def load_fixes(path: Path):
    f = pd.read_csv(path)
    f = f[np.isfinite(f.earth_x_cm) & np.isfinite(f.earth_y_cm)]
    setting = float(np.median(f.t_send - f.t_cap))
    return f.t_epoch.to_numpy(float), f.earth_x_cm.to_numpy(float) / 100, f.earth_y_cm.to_numpy(float) / 100, setting


def load_est(session: Path):
    df = pd.read_csv(session / "telemetry.csv", usecols=["received_ns", "key", "value"])
    out = {}
    for ax in "xy":
        s = df[df.key == f"Ctrler.loc{ax}PID.FB"]
        out[ax] = (s.received_ns.to_numpy(float) / 1e9, pd.to_numeric(s.value, errors="coerce").to_numpy(float) / 100)
    return out


def estimate(t_cam, cam, t_est, est, max_lag: float = 1.0, centre: float = 0.3, min_speed: float = 0.05):
    """(lag s, r, runner-up r, moving s) for one axis; lag None when the axis barely moved (peak speed < min_speed m/s)."""
    m = np.isfinite(est)
    tv_e, v_e = velocity(t_est[m], est[m])
    tv_c, v_c = velocity(t_cam, cam)
    moving = float(np.sum(np.abs(v_e) > min_speed) * DT)
    if np.nanmax(np.abs(v_e)) < min_speed:
        return None, float("nan"), float("nan"), moving
    lag, r, r2 = sync_xcorr(tv_e, v_e, tv_c, v_c, max_lag=max_lag, dt=DT, min_overlap=0.3, centre=centre)
    return lag, r, r2, moving


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("session", type=Path)
    ap.add_argument("--fixes", type=Path)
    ap.add_argument("--flight", help="flight name to find fixes.csv under logs/video_truth")
    ap.add_argument("--tol-ms", type=float, default=20.0, help="PROPOSED: |lag - setting| above this says re-set --delay-ms")
    a = ap.parse_args(argv)
    fx = a.fixes or _find_fixes_csv(a.session, a.flight)
    if fx is None:
        raise SystemExit("no fixes.csv found: pass --fixes")
    t_cam, cx, cy, setting = load_fixes(fx)
    est = load_est(a.session)
    print(f"fixes {fx}\ntracker --delay-ms setting {setting * 1000:.0f} ms")
    lags = []
    for ax, cam in (("x", cx), ("y", cy)):
        lag, r, r2, mv = estimate(t_cam, cam, *est[ax])
        if lag is None:
            print(f"{ax}: too little motion ({mv:.1f} s above 5 cm/s), no lag")
            continue
        sharp = "sharp" if r - r2 > 0.2 else "FLAT (runner-up close: do not trust)"
        print(f"{ax}: camera lags the estimate by {lag * 1000:.0f} ms (r {r:.2f}, runner-up {r2:.2f}, {sharp}; {mv:.1f} s moving)")
        if r - r2 > 0.2:
            lags.append(lag)
    if not lags:
        print("verdict: no usable lag")
        return 1
    L = float(np.mean(lags))
    d = (L - setting) * 1000
    print(f"lag {L * 1000:.0f} ms (excludes the telemetry link's one-way latency) vs setting {setting * 1000:.0f} ms: "
          f"{'re-set --delay-ms' if abs(d) > a.tol_ms else 'keep the setting'} ({d:+.0f} ms, tolerance {a.tol_ms:.0f} ms PROPOSED)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
