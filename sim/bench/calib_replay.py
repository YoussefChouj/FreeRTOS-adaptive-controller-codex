"""Closed-loop replay calibration of the roll/pitch control effectiveness b.

The 50 Hz ARX fit (calib_logs.py) cannot identify b.  Here the logged angle
demand (rollPID.Des / pitchPID.Des, 25 Hz) drives a single-axis copy of the
firmware angle->rate PID cascade (sim_coupled gains, 200 Hz, 15 ms delay,
motor lag) for each b on a grid; the simulated angle is compared with the
logged angle FB on the longest airborne segment of each flight.
Metrics: NRMSE = rms(sim-log)/std(log); Des->FB lag (sim vs log, xcorr);
std of 2 Hz low-passed rate-loop U (sim vs log);
baseline NRMSE of using Des itself as the predictor of FB.  Writes calib_replay_v1.json.
"""
import json, os, sys
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'adaptive_compare'))
from sim_coupled import PID, ANG_PR, RATE_PR, DT_C, DT_P, TAU_M, DELAY  # noqa: E402
from calib_logs import load, airborne  # noqa: E402

B_GRID = np.array([4, 6, 8, 11, 16, 22, 32], float)   # deg/s^2 per U
FLIGHTS = [1, 3, 5, 6, 7, 8]
AXES = {'roll': ('Ctrler.rollPID.Des', 'Ctrler.rollPID.FB', 'Ctrler.gyroxPID.U'),
        'pitch': ('Ctrler.pitchPID.Des', 'Ctrler.pitchPID.FB', 'Ctrler.gyroyPID.U')}


def longest(ok):
    best, i, n = (0, 0), 0, len(ok)
    while i < n:
        if ok[i]:
            j = i
            while j < n and ok[j]:
                j += 1
            if j - i > best[1] - best[0]:
                best = (i, j)
            i = j
        else:
            i += 1
    return best


def lp(x, dt, fc=2.0):
    a = dt / (dt + 1 / (2 * np.pi * fc)); y = np.empty_like(x); y[0] = x[0]
    for k in range(1, len(x)):
        y[k] = y[k - 1] + a * (x[k] - y[k - 1])
    return y


def lag_ms(x, y, dt, maxlag=0.4):
    """Lag of y behind x (s->ms) from the xcorr peak of the detrended signals."""
    x = x - x.mean(); y = y - y.mean(); L = int(maxlag / dt)
    c = [np.dot(x[:len(x) - k], y[k:]) for k in range(L)]
    return 1e3 * dt * int(np.argmax(c))


def replay(t_des, des, th0, T):
    """Run the cascade for every b at once; returns 200 Hz time, angle[B,N], U[B,N]."""
    B = len(B_GRID); ang, rate = PID(ANG_PR, B), PID(RATE_PR, B)
    th = np.full(B, th0); w = np.zeros(B); um = np.zeros(B)
    nd = int(round(DELAY / DT_P)); buf = np.zeros((nd + 1, B)); bi = 0
    n_c = int(T / DT_C); sub = int(round(DT_C / DT_P))
    out_t, out_th, out_u = np.zeros(n_c), np.zeros((B, n_c)), np.zeros((B, n_c))
    u = np.zeros(B)
    for k in range(n_c):
        t = k * DT_C
        r = np.interp(t, t_des, des)
        wd = ang.step(np.full(B, r) - th)
        u = rate.step(wd - w)
        for _ in range(sub):
            buf[bi] = u; bi = (bi + 1) % (nd + 1)
            ud = buf[bi]
            um += DT_P / TAU_M * (ud - um)
            w += DT_P * B_GRID * um
            th += DT_P * w
        out_t[k], out_th[:, k], out_u[:, k] = t, th, u
    return out_t, out_th, out_u


def main():
    res = {'b_grid': B_GRID.tolist(), 'per_flight': {}}
    agg = {ax: {'nrmse': [], 'lag_sim': [], 'lag_log': [], 'ustd_sim': [], 'ustd_log': [], 'w': [], 'nd': []} for ax in AXES}
    for f in FLIGHTS:
        s0, s1, _ = load(f)
        ok, _ = airborne(s0)
        i0, i1 = longest(ok)
        if i1 - i0 < 500:
            continue
        t0 = s0.t_src_ms.values[i0:i1].astype(float) / 1e3
        ta, T = t0[0], t0[-1] - t0[0]
        t1 = s1.t_src_ms.values.astype(float) / 1e3
        m1 = (t1 >= ta) & (t1 <= t0[-1])
        res['per_flight'][f] = {'T_s': float(T)}
        for ax, (dc, fc, uc) in AXES.items():
            des = s1[dc].values.astype(float)[m1]; td = t1[m1] - ta
            fb = s0[fc].values.astype(float)[i0:i1]
            tt, th, uu = replay(td, des, fb[0], T)
            ths = np.array([np.interp(t0 - ta, tt, th[j]) for j in range(len(B_GRID))])
            e = ths - fb
            nr = np.sqrt(np.mean(e[:, 250:] ** 2, 1)) / np.std(fb[250:])
            des50 = np.interp(t0 - ta, td, des)
            ls = [lag_ms(des50, ths[j], 0.02) for j in range(len(B_GRID))]
            ll = lag_ms(des50, fb, 0.02)
            ulog = np.interp(t0 - ta, td, s1[uc].values.astype(float)[m1])
            us = [float(np.std(lp(np.interp(t0 - ta, tt, uu[j]), 0.02)[250:])) for j in range(len(B_GRID))]
            ul = float(np.std(lp(ulog, 0.02)[250:]))
            nd = float(np.sqrt(np.mean((des50 - fb)[250:] ** 2)) / np.std(fb[250:]))
            res['per_flight'][f][ax] = dict(nrmse=nr.tolist(), lag_sim_ms=ls, lag_log_ms=ll, ustd_sim=us, ustd_log=ul,
                                            nrmse_des_as_pred=nd)
            a = agg[ax]; a['nd'].append(nd); a['nrmse'].append(nr); a['lag_sim'].append(ls); a['lag_log'].append(ll)
            a['ustd_sim'].append(us); a['ustd_log'].append(ul); a['w'].append(T)
    print(f"b grid {B_GRID.tolist()} deg/s2/U; flights {list(res['per_flight'])}")
    res['axes'] = {}
    for ax, a in agg.items():
        w = np.array(a['w']); w = w / w.sum()
        nr = np.average(np.array(a['nrmse']), 0, w); ls = np.average(np.array(a['lag_sim'], float), 0, w)
        ll = float(np.average(a['lag_log'], weights=w)); us = np.average(np.array(a['ustd_sim']), 0, w)
        ul = float(np.average(a['ustd_log'], weights=w))
        # pick: smallest Des->FB lag error among b whose NRMSE is within 0.05 of the best
        # (U std is not usable: logged U is dominated by unmodelled disturbance torque)
        nd = float(np.average(a['nd'], weights=w))
        score = np.where(nr <= nr.min() + 0.05, np.abs(ls - ll), np.inf)
        jb = int(np.argmin(score))
        res['axes'][ax] = dict(nrmse=nr.tolist(), lag_sim_ms=ls.tolist(), lag_log_ms=ll, ustd_sim=us.tolist(),
                               ustd_log=ul, nrmse_des_as_pred=nd, b_best=float(B_GRID[jb]),
                               nrmse_best=float(nr[jb]))
        print(f" {ax}: NRMSE {np.round(nr, 2)}  lag_sim {np.round(ls).astype(int)} ms (log {ll:.0f})")
        print(f"   U_lp std sim {np.round(us).astype(int)} (log {ul:.0f})  -> b={B_GRID[jb]:.0f} NRMSE {nr[jb]:.2f} (Des-as-predictor {nd:.2f})")
    with open(os.path.join(HERE, 'calib_replay_v1.json'), 'w') as fh:
        json.dump(res, fh, indent=1)


if __name__ == '__main__':
    main()
