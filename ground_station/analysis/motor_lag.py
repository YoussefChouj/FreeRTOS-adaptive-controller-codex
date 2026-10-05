"""Motor lag (first-order tau + dead time) from flight logs: motor command -> measured RPM.

The sim bench models each motor as a pure delay plus a first-order lag (sim/bench/plant.py: TAU_M = 1/19.8 s,
DELAY_TICKS = 3 at 5 ms).  Neither number has a recorded measurement.  This fits the same structure to slot-2 VOFA
logs, which carry the commanded motor value (``mymotor.motor1..4``) and the per-revolution RPM period
(``rpm_dbg_period_cyc[i]``) in the same frame.

Model per motor, sampled at Ts (about 20 ms in slot 2):  rpm[k] = a rpm[k-1] + b cmd[k-d] + c,  tau = -Ts / ln a.
The RPM channel is noisy (missed marks, extra edges), and that noise sits on the regressor rpm[k-1], which biases
ordinary least squares toward a = 0 (tau too small).  So the fit is an instrumental-variable one: rpm[k-1] is
instrumented by cmd[k-d-1] and cmd[k-d-2].  The controller feeds back gyro and attitude, never RPM, so RPM sensor
noise is independent of the command and the IV estimate stays consistent in closed loop.

Limits: Ts = 20 ms cannot resolve a dead time below one sample, and the RPM period is measured over the last
revolution (about 12 ms at 5000 RPM), which adds roughly half a revolution of apparent delay.

Result on the logs in the repo (2026-10-05, doc sec K.6): NOT identifiable.  The 10-03 logs have all four RPM
channels frozen together for most of the flight (rpm_dbg_edges stops counting while mymotor still updates), and on the
logs with live RPM the fits scatter (tau 47-760 ms over motor, dead time and estimator), likely because the 50 Hz slot
aliases the 200 Hz command.  Keep this for a log with live RPM at >= 100 Hz; check 'valid' (fresh-edge fraction) first.

    python -m ground_station.analysis.motor_lag logs/vofa/flight_test_drift_fix_1 [more prefixes ...] [--json out]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ground_station.analysis.rpm_signals import SYSTEM_CORE_CLOCK

RPM_MIN, RPM_MAX = 2000.0, 20000.0    # plausible spinning range; outside = missed or extra edge
CMD_FLY = 2500.0                      # all four commands above this = airborne segment (10-03 hover ~3000)
MAX_DELAY = 3                         # samples tried for the dead time


def segments(t_ms: np.ndarray, gap_ms: float) -> np.ndarray:
    """Segment id per sample; a new segment starts where time goes backwards or jumps by more than gap_ms."""
    dt = np.diff(t_ms, prepend=t_ms[0])
    return np.cumsum((dt < 0) | (dt > gap_ms))


def motor_rows(df: pd.DataFrame, i: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(cmd, rpm, valid) for motor i (0-based); rpm is NaN where invalid."""
    cmd = df[f'mymotor.motor{i + 1}'].to_numpy(float)
    per = df[f'rpm_dbg_period_cyc[{i}]'].to_numpy(float)
    edges = df[f'rpm_dbg_edges[{i}]'].to_numpy(float)
    with np.errstate(divide='ignore', invalid='ignore'):
        rpm = 60.0 * SYSTEM_CORE_CLOCK / per
    fresh = np.diff(edges, prepend=np.nan) != 0          # a new edge arrived since the last frame
    valid = np.isfinite(rpm) & (rpm > RPM_MIN) & (rpm < RPM_MAX) & fresh
    return cmd, np.where(valid, rpm, np.nan), valid


def fit(cmd: np.ndarray, rpm: np.ndarray, seg: np.ndarray, fly: np.ndarray, d: int, iv: bool = True) -> dict:
    """Fit rpm[k] = a rpm[k-1] + b cmd[k-d] + c on samples whose whole window is valid, airborne and in one segment."""
    lag = d + 2                                          # deepest index used (instrument cmd[k-d-2])
    k = np.arange(lag, len(rpm))
    ok = np.ones(len(k), bool)
    for j in range(lag + 1):
        ok &= (seg[k - j] == seg[k]) & fly[k - j]
    ok &= np.isfinite(rpm[k]) & np.isfinite(rpm[k - 1])
    k = k[ok]
    if len(k) < 50:
        return {'d': d, 'n': int(len(k))}
    y = rpm[k]
    X = np.column_stack([rpm[k - 1], cmd[k - d], np.ones(len(k))])
    if iv:   # 2SLS: project X on the instruments, then least squares on the projection
        Z = np.column_stack([cmd[k - d - 1], cmd[k - d - 2], cmd[k - d], np.ones(len(k))])
        Xh = Z @ np.linalg.lstsq(Z, X, rcond=None)[0]
        th = np.linalg.lstsq(Xh, y, rcond=None)[0]
    else:
        th = np.linalg.lstsq(X, y, rcond=None)[0]
    res = y - X @ th
    return {'d': d, 'n': int(len(k)), 'a': float(th[0]), 'b': float(th[1]), 'c': float(th[2]),
            'rms': float(np.sqrt(np.mean(res ** 2)))}


def analyse(prefixes: list[str], gap_ms: float = 60.0) -> dict:
    """Fit every motor of every prefix's slot-2 log for d = 0..MAX_DELAY, OLS and IV."""
    out = {}
    for p in prefixes:
        df = pd.read_csv(f'{p}.slot2.csv')
        t = df['t_src_ms'].to_numpy(float)
        seg = segments(t, gap_ms)
        dts = np.diff(t)
        ts = float(np.median(dts[(dts > 0) & (dts < gap_ms)])) / 1000.0
        fly = np.all([df[f'mymotor.motor{i + 1}'].to_numpy(float) > CMD_FLY for i in range(4)], axis=0)
        per = {}
        for i in range(4):
            cmd, rpm, valid = motor_rows(df, i)
            fits = []
            for d in range(MAX_DELAY + 1):
                for iv in (False, True):
                    f = fit(cmd, rpm, seg, fly, d, iv)
                    f['iv'] = iv
                    if 0 < f.get('a', 0) < 1:
                        f['tau_ms'] = -ts / np.log(f['a']) * 1000.0
                    fits.append(f)
            per[f'm{i + 1}'] = {'valid_frac_airborne': float(valid[fly].mean()) if fly.any() else 0.0,
                                'rpm_median_airborne': float(np.nanmedian(rpm[fly])) if fly.any() else None,
                                'fits': fits}
        out[Path(p).name] = {'ts_s': ts, 'airborne_frames': int(fly.sum()), 'motors': per}
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('prefixes', nargs='+', help='log prefix, e.g. logs/vofa/flight_test_drift_fix_1')
    ap.add_argument('--json', help='write the full result here')
    a = ap.parse_args(argv)
    res = analyse(a.prefixes)
    for name, r in res.items():
        print(f"{name}: Ts {r['ts_s'] * 1000:.1f} ms, airborne frames {r['airborne_frames']}")
        for m, v in r['motors'].items():
            best = {iv: min((f for f in v['fits'] if f['iv'] == iv and 'rms' in f), key=lambda f: f['rms'],
                            default=None) for iv in (False, True)}
            row = [f"{m} valid {v['valid_frac_airborne']:.2f} rpm~{v['rpm_median_airborne']:.0f}"]
            for f in v['fits']:
                if 'tau_ms' in f:
                    row.append(f"{'IV' if f['iv'] else 'LS'} d{f['d']} tau {f['tau_ms']:.0f}ms rms {f['rms']:.0f}")
            print('  ' + ' | '.join(row))
            for iv, f in best.items():
                if f and 'tau_ms' in f:
                    print(f"    best {'IV' if iv else 'LS'} by rms: d={f['d']} tau {f['tau_ms']:.0f} ms (n {f['n']})")
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1))


if __name__ == '__main__':
    main()
