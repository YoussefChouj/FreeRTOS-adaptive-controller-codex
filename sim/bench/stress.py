"""Stress bench: every disturbance / mismatch axis as a severity ladder L0 (nominal) .. L4 (extreme).

Why (user critique 2026-10-05): the scen.py families sit at ONE moderate level each, the payload is static from t=0,
the adaptive laws see the exact plant constants, the PID was CMA-tuned on those same families, and the median RMSE
hides the rows that diverge.  Here each axis is stepped up until controllers break, and the headline number is the
divergence rate per level and the break point (first level with >= 25 % of rows diverged), not a median.

run_ext is plant.run (frozen file, not edited) plus optional per-row keys in sp:
  tau_m  (B,) motor time constant [s]        (plant: TAU_M for every row)
  delay  (B,) int command delay [5 ms ticks] (plant: DELAY_TICKS)
  load_m, load_r (B,3 body attach point), load_l, load_cable, load_t_on, load_t_off: a point-mass load on a stiff
         spring (LOAD_K, damping ratio 0.7), rigid (spring to the attach point) or on a cable of length load_l (tension
         only).  The load is a separate body: it swings, it pulls the drone at the attach point (force + r x F torque),
         and before load_t_on it rides along without force (sudden pickup).  Nothing in any controller knows about it.
Without those keys run_ext equals plant.run bit for bit (`python stress.py check`).

Axes (rows: traj in TRAJ_SET x seeds in SEEDS, base params scen.row_params('nominal', ...)):
  wind_gust   mean wind / Dryden sigma / 2 gusts [m/s]   (2,.5,3) (4,1,5) (6,1.5,7) (8,2,10)
  mass_step   rigid load picked up at t=8 s, 5 cm below CoG   .15 .30 .50 .70 kg
  swing_load  cable 0.3 m from the pads (8 cm below), t=0     .10 .25 .40 .55 kg
  arm_load    cable 0.1 m from motor 0's arm tip, t=0         .10 .20 .30 .40 kg
  motor_loss  one motor's efficiency from t=8 s               .85 .70 .60 .50
  kt_mismatch thrust coefficient +-(10 20 30 40) %, random sign per row (all 4 motors)
  actuator    motor tau x(1.5 2 3 4) and delay 4 6 8 10 ticks (nominal 3 = 15 ms)
  inertia     J x(1.2 1.4 1.7 2.0)
  combo       wind_gust + arm_load + kt_mismatch + actuator, all at the same level
  speed       lemniscate A=1.5 m at 1.0 1.5 2.0 2.5 3.0 m/s peak, nominal disturbances (levels L0..L4)

    python stress.py check                       # run_ext == plant.run, bit for bit
    python stress.py run fw_pid pid_tuned2 ...   # results/stress_<tag>.json (per-row metrics)
    python stress.py report                      # markdown tables from every results/stress_*.json
"""
import argparse
import json
import sys
import time

import numpy as np

import bench
import fwpid
import plant
import scen
from plant import (A1, A2, G, V_NOM, PWM_MIN, PWM_MAX, DELAY_TICKS, IIR, BG_B, BG_A, BA_B, BA_A, TOF_DELAY, B_YAW,
                   J0, DT_C, DT, SUB, TAU_M, R_PROP, ARM, KAPPA, DRAG_LIN, DRAG_ROT, SIG_GYRO, SIG_ACC, KP_MAHONY,
                   TOF_EVERY, OF_EVERY, SIG_TOF, SIG_OF, K_TOF, K_OF, rot, euler_rates)
from demo_loads import F1X_ANG, F1X_RATE, MOTOR_XY, U_IMB_1003

LOAD_K, LOAD_ZETA, LOAD_DRAG = 5000.0, 0.7, 0.02      # N/m (1.4 mm stretch at 0.7 kg), -, N/(m/s)
ARM_TIP = ARM * np.sqrt(2.0)                           # plant.ARM is the x / y moment arm; the tip sits at ARM, ARM
# Yaw mixer imbalance per row.  '1003': the flashed 10-03 mixer, pairs within 1-66 U (demo_loads.U_IMB_1003), which is
# the image that flies.  '0927': scen's own draw (350-500 U, the 09-27 spin flights); the first stress run (089ad55)
# used it and the large imbalance plus an arm load saturated yaw (doc sec K.3).
YAW_IMB = '1003'


def run_ext(ctrl, ref, sp, seed=0, div_err=2.0, tile=1):
    B, N = ref['p'].shape[0], ref['p'].shape[1]
    rng = np.random.default_rng(seed); B0 = B // tile

    def nrm(*s):
        x = rng.standard_normal((B0,) + s)
        return np.tile(x, (tile,) + (1,) * len(s))
    ns = sp['noise_scale']
    p = ref['p'][:, 0].copy(); v = np.zeros((B, 3)); e = np.zeros((B, 3))
    e[:, 2] = np.deg2rad(ref['yaw'][:, 0]); w = np.zeros((B, 3))
    m = sp['mass']; J = sp['J']
    x_h = (-A1 + np.sqrt(A1 ** 2 + 4 * A2 * m * G / 4 / (sp['V0'] / V_NOM) ** 2)) / (2 * A2)
    mot = np.repeat((PWM_MIN + x_h)[:, None], 4, 1)
    # ---- extensions ----
    TM = 'tau_m' in sp
    tau_m = sp['tau_m'][:, None] if TM else None
    DL = 'delay' in sp
    dly = sp['delay'].astype(int) if DL else None
    D = int(dly.max()) if DL else DELAY_TICKS
    LD = 'load_m' in sp
    if LD:
        lm = sp['load_m']; lms = np.maximum(lm, 1e-6); lr = sp['load_r']; ll = sp['load_l']
        cab = sp['load_cable'].astype(bool); lc = 2 * LOAD_ZETA * np.sqrt(LOAD_K * lms)
        hang = np.zeros((B, 3)); hang[:, 2] = -np.where(cab, ll + lms * G / LOAD_K, lms * G / LOAD_K)
        b1, b2, b3 = rot(e)
        lq = p + b1 * lr[:, :1] + b2 * lr[:, 1:2] + b3 * lr[:, 2:3] + hang; lu = np.zeros((B, 3))
        Fd = np.zeros((B, 3)); gz = np.array([0, 0, G])
    cmd_hist = [mot.copy() for _ in range(D + 1)]
    f0 = np.zeros((B, 3)); f0[:, 2] = G
    gyro_f = IIR(BG_B, BG_A, (B, 3)); acc_f = IIR(BA_B, BA_A, (B, 3), f0 + sp['acc_bias'])
    ehat = e.copy(); xkf = np.zeros((B, 3, 3)); xkf[:, :, 0] = p
    zhist = [p[:, 2].copy() for _ in range(TOF_DELAY + 1)]
    vhist = [v[:, :2].copy() for _ in range(17)]
    tz_imb = -sp['u_imb'] * B_YAW * J0[2]
    turb = np.zeros((B, 3))
    alive = np.ones(B, bool)
    L = dict(p=np.zeros((B, N, 3), np.float32), e=np.zeros((B, N, 3), np.float32),
             mot=np.zeros((B, N, 4), np.float32), phat=np.zeros((B, N, 3), np.float32),
             div_k=np.full(B, -1))
    g_meas = np.zeros((B, 3)); a_meas = f0 + sp['acc_bias']
    for k in range(N):
        t = k * DT_C
        # ---- observation (estimated only) and control at 200 Hz ----
        cthat = np.cos(ehat[:, 1]); cfhat = np.cos(ehat[:, 0])
        obs = dict(k=k, t=t, rpy=np.rad2deg(ehat), gyro=g_meas, acc=a_meas,
                   pos=np.stack([xkf[:, 0, 0], xkf[:, 1, 0], xkf[:, 2, 0]], 1),
                   vel=np.stack([xkf[:, 0, 1], xkf[:, 1, 1], xkf[:, 2, 1]], 1),
                   vbat=sp['V0'] - sp['vsag'] * t - sp['vstep'] * (t > sp['vstep_t']),
                   mot=cmd_hist[-1],
                   ref=dict(p=ref['p'][:, k], v=ref['v'][:, k], a=ref['a'][:, k], yaw=ref['yaw'][:, k]),
                   preview=lambda n, k=k: dict(p=ref['p'][:, min(k + n, N - 1)], v=ref['v'][:, min(k + n, N - 1)],
                                               a=ref['a'][:, min(k + n, N - 1)], yaw=ref['yaw'][:, min(k + n, N - 1)]))
        out = ctrl.step(obs)
        U = np.nan_to_num(out['U']); thr = np.clip(np.nan_to_num(out['thr'], nan=PWM_MIN), PWM_MIN, PWM_MAX)
        gx, gy, gz_ = U[:, 0], U[:, 1], U[:, 2]
        M = np.clip(np.stack([thr - gy - gx - gz_, thr + gy + gx - gz_, thr - gy + gx + gz_, thr + gy - gx + gz_], 1),
                    PWM_MIN, PWM_MAX)
        cmd_hist.append(M); cmd_hist.pop(0)
        Md = np.stack(cmd_hist)[D - dly, np.arange(B)] if DL else cmd_hist[0]
        # ---- per-tick environment ----
        V = obs['vbat']
        gain = sp['mgain'] * ((V / V_NOM) ** 2)[:, None]
        lost = (t >= sp['mloss_t'])
        gain[np.arange(B), sp['mloss_idx']] *= np.where(lost, sp['mloss_eff'], 1.0)
        ge = 1.0 / (1.0 - np.minimum((R_PROP / (4 * np.maximum(p[:, 2], 0.03))) ** 2, 0.25))
        gain *= ge[:, None]
        turb += DT_C / 1.0 * (-turb) + sp['dryden_sigma'][:, None] * np.sqrt(2 * DT_C / 1.0) * nrm(3)
        wind = sp['wind'] + turb
        for j in range(sp['gust_t'].shape[1]):
            tau = (t - sp['gust_t'][:, j]) / sp['gust_dur'][:, j]
            on = (tau >= 0) & (tau <= 1)
            wind = wind + (on * 0.5 * (1 - np.cos(2 * np.pi * np.clip(tau, 0, 1))))[:, None] * sp['gust_vec'][:, j]
        if LD:
            lon = (t >= sp['load_t_on']) & (t < sp['load_t_off']) & (lm > 0)
        # ---- 1 kHz plant + IMU ----
        for s in range(SUB):
            if TM:
                mot += DT / tau_m * (Md - mot)
            else:
                mot += DT / TAU_M * (Md - mot)
            x = mot - PWM_MIN
            T = (A1 * x + A2 * x * x) * gain
            Ts = T.sum(1)
            tau = np.stack([ARM * (T[:, 1] + T[:, 2] - T[:, 0] - T[:, 3]) - sp['cog'][:, 1] * Ts,
                            ARM * (T[:, 1] + T[:, 3] - T[:, 0] - T[:, 2]) + sp['cog'][:, 0] * Ts,
                            KAPPA * (T[:, 2] + T[:, 3] - T[:, 0] - T[:, 1]) + tz_imb], 1)
            b1, b2, b3 = rot(e)
            if LD:
                ra = b1 * lr[:, :1] + b2 * lr[:, 1:2] + b3 * lr[:, 2:3]
                att = p + ra
                adot = v + np.cross(b1 * w[:, :1] + b2 * w[:, 1:2] + b3 * w[:, 2:3], ra)
                off = ~lon
                lq[off] = att[off] + hang[off]; lu[off] = adot[off]
                d = lq - att; du = lu - adot
                dist = np.linalg.norm(d, axis=1); n = d / np.maximum(dist, 1e-9)[:, None]
                st = dist - ll
                Tn = np.maximum(0.0, LOAD_K * st + lc * (du * n).sum(1)) * (st > 0)
                FL = np.where(cab[:, None], -Tn[:, None] * n, -LOAD_K * d - lc[:, None] * du) * lon[:, None]
                Fd = -FL
                tau = tau + np.cross(lr, np.stack([(b1 * Fd).sum(1), (b2 * Fd).sum(1), (b3 * Fd).sum(1)], 1))
                lu += DT * ((FL - LOAD_DRAG * (lu - wind)) / lms[:, None] - gz)
                lq += DT * lu
            fw = (Ts[:, None] * b3 - DRAG_LIN * (v - wind)) / m[:, None]
            if LD:
                fw = fw + Fd / m[:, None]
            v += DT * (fw - np.array([0, 0, G])) * alive[:, None]
            p += DT * v
            Jw = J * w
            wd = (tau - DRAG_ROT * w - np.cross(w, Jw)) / J
            w += DT * wd * alive[:, None]
            e += DT * euler_rates(e, w)
            fb = np.stack([(b1 * fw).sum(1), (b2 * fw).sum(1), (b3 * fw).sum(1)], 1)
            g_raw = np.rad2deg(w) + sp['gyro_bias'] + SIG_GYRO * ns[:, None] * nrm(3)
            a_raw = fb + sp['acc_bias'] + SIG_ACC * ns[:, None] * nrm(3)
            g_meas = gyro_f(g_raw); a_meas = acc_f(a_raw)
        # ---- attitude estimate (Mahony-lite, no magnetometer) ----
        gr = np.deg2rad(g_meas)
        ea = np.stack([np.arctan2(a_meas[:, 1], a_meas[:, 2]),
                       np.arctan2(-a_meas[:, 0], np.hypot(a_meas[:, 1], a_meas[:, 2]))], 1)
        ed = euler_rates(ehat, gr)
        ehat[:, :2] += DT_C * (ed[:, :2] + KP_MAHONY * (ea - ehat[:, :2]))
        ehat[:, 2] += DT_C * ed[:, 2]
        # ---- position/velocity KFs (EkfOf approximation, steady-state gains) ----
        h1, h2, h3 = rot(ehat)
        aw = h1 * a_meas[:, :1] + h2 * a_meas[:, 1:2] + h3 * a_meas[:, 2:3]
        aw[:, 2] -= G
        for ax in range(3):
            xa = xkf[:, ax]
            u = aw[:, ax] - xa[:, 2]
            xa[:, 0] += DT_C * xa[:, 1] + 0.5 * DT_C ** 2 * u
            xa[:, 1] += DT_C * u
        zhist.append(p[:, 2].copy()); zhist.pop(0)
        vhist.append(v[:, :2].copy()); vhist.pop(0)
        if k % TOF_EVERY == 0:
            ct, cf = np.cos(e[:, 1]), np.cos(e[:, 0])
            rng_m = zhist[0] / (ct * cf) + SIG_TOF * ns * nrm() + sp['tof_spike'][:, k // TOF_EVERY]
            ok = ~sp['tof_drop'][:, k // TOF_EVERY]
            inn = (rng_m * cthat * cfhat - xkf[:, 2, 0]) * ok
            xkf[:, 2] += inn[:, None] * K_TOF
        if k % OF_EVERY == 0:
            i = k // OF_EVERY
            ok = ~sp['of_drop'][:, i]
            vd = np.stack(vhist, 0)[-1 - sp['of_delay'], np.arange(B)]
            vm = vd * (1 + sp['of_scale'])[:, None] + SIG_OF * ns[:, None] * nrm(2)
            for ax in range(2):
                inn = (vm[:, ax] - xkf[:, ax, 1]) * ok
                xkf[:, ax] += inn[:, None] * K_OF
        # ---- divergence / log ----
        err = np.linalg.norm(p - ref['p'][:, k], axis=1)
        bad = alive & ((err > div_err) | (np.abs(e[:, :2]).max(1) > np.deg2rad(60)) | (p[:, 2] < 0.01)
                       | ~np.isfinite(err))
        L['div_k'][bad] = k
        alive &= ~bad
        v[~alive] = 0; w[~alive] = 0
        L['p'][:, k] = p; L['e'][:, k] = e; L['mot'][:, k] = M; L['phat'][:, k] = obs['pos']
    L['diverged'] = L['div_k'] >= 0
    return L


# ------------------------------------------------------------------ ladders
TRAJ_SET = ['hover', 'circle_1.0', 'zigzag_1.0', 'lemA_1.5']
SEEDS = [200, 201, 202]
SPEEDS = [1.0, 1.5, 2.0, 2.5, 3.0]
LAD = {
    'wind_gust': [(2.0, 0.5, 3.0), (4.0, 1.0, 5.0), (6.0, 1.5, 7.0), (8.0, 2.0, 10.0)],
    'mass_step': [0.15, 0.30, 0.50, 0.70],
    'swing_load': [0.10, 0.25, 0.40, 0.55],
    'arm_load': [0.10, 0.20, 0.30, 0.40],
    'motor_loss': [0.85, 0.70, 0.60, 0.50],
    'kt_mismatch': [0.10, 0.20, 0.30, 0.40],
    'actuator': [(1.5, 4), (2.0, 6), (3.0, 8), (4.0, 10)],
    'inertia': [1.2, 1.4, 1.7, 2.0],
}
AXES = list(LAD) + ['combo', 'speed']
COMBO = ['wind_gust', 'arm_load', 'kt_mismatch', 'actuator']


def lem_a(v, A=1.5, z0=scen.Z0):
    """scen's lemniscate (peak speed v at the crossing) with a 1.5 m half-width instead of 0.8 m."""
    t = np.arange(scen.N) * DT_C
    th = v / (np.sqrt(2) * A) * scen.warp(t)
    p = np.stack([A * np.sin(th), 0.5 * A * np.sin(2 * th), np.full(scen.N, z0)], 1)
    vel = np.gradient(p, DT_C, axis=0)
    return dict(p=p, v=vel, a=np.gradient(vel, DT_C, axis=0), yaw=np.zeros(scen.N))


def base_q(seed, ti):
    q = scen.row_params('nominal', 0, ti, seed)
    if YAW_IMB == '1003':
        q['u_imb'] = np.random.default_rng([seed, 1003]).uniform(*U_IMB_1003)
    q.update(tau_m=TAU_M, delay=DELAY_TICKS, load_m=0.0, load_r=np.zeros(3), load_l=0.0, load_cable=False,
             load_t_on=0.0, load_t_off=1e9)
    return q


def apply(q, axis, lvl, rng):
    """Mutate row params q for `axis` at level lvl (1..4)."""
    if axis == 'combo':
        for a in COMBO:
            apply(q, a, lvl, rng)
        return q
    x = LAD[axis][lvl - 1]
    if axis == 'wind_gust':
        mean, sig, gust = x
        h = rng.uniform(0, 2 * np.pi)
        q['wind'] = np.array([mean * np.cos(h), mean * np.sin(h), 0.0]); q['dryden_sigma'] = sig
        q['gust_t'] = np.array([6.0, 12.0]); q['gust_dur'] = np.array([1.0, 1.5])
        hs = rng.uniform(0, 2 * np.pi, 2)
        q['gust_vec'] = gust * np.stack([np.cos(hs), np.sin(hs), 0.2 * rng.choice([-1, 1], 2)], 1)
    elif axis == 'mass_step':
        q.update(load_m=x, load_r=np.array([0.0, 0.0, -0.05]), load_cable=False, load_t_on=8.0)
    elif axis == 'swing_load':
        q.update(load_m=x, load_r=np.array([0.0, 0.0, -0.08]), load_l=0.3, load_cable=True)
    elif axis == 'arm_load':
        mx, my = MOTOR_XY[0]
        q.update(load_m=x, load_r=np.array([mx * ARM, my * ARM, -0.03]), load_l=0.1, load_cable=True)
    elif axis == 'motor_loss':
        q.update(mloss_t=8.0, mloss_idx=int(rng.integers(4)), mloss_eff=x)
    elif axis == 'kt_mismatch':
        q['mgain'] = q['mgain'] * (1 + rng.choice([-1, 1]) * x)
    elif axis == 'actuator':
        q['tau_m'] = TAU_M * x[0]; q['delay'] = x[1]
    elif axis == 'inertia':
        q['J'] = q['J'] * x
    return q


def build():
    rows, refs, qs = [], [], []

    def add(tr, axis, lvl, sd, ref, ti):
        q = base_q(sd, ti)
        if lvl and axis != 'speed':
            apply(q, axis, lvl, np.random.default_rng([sd, AXES.index(axis), lvl]))
        rows.append((tr, f'{axis}:L{lvl}', sd)); refs.append(ref); qs.append(q)
    trefs = {tr: (lem_a(1.5) if tr == 'lemA_1.5' else scen.traj(tr)) for tr in TRAJ_SET}
    for sd in SEEDS:
        for ti, tr in enumerate(TRAJ_SET):
            add(tr, 'nominal', 0, sd, trefs[tr], ti)
            for axis in LAD:
                for lvl in range(1, 5):
                    add(tr, axis, lvl, sd, trefs[tr], ti)
            for lvl in range(1, 5):
                add(tr, 'combo', lvl, sd, trefs[tr], ti)
        for lvl, sv in enumerate(SPEEDS):
            add(f'lemA_{sv}', 'speed', lvl, sd, lem_a(sv), 3)
    ref = {k: np.stack([r[k] for r in refs]) for k in refs[0]}
    sp = {k: np.array([q[k] for q in qs]) for k in qs[0]}
    return rows, ref, sp


# ------------------------------------------------------------------ controllers
# tag: (class spec or None = from results/<src>_test.json, params source, firmware F1X integral patch)
CTRLS = {
    'fw_pid': ('fwpid:FwPID', None, True),               # firmware gains as flashed: no tuning at all
    'pid_tuned2': (None, 'pid_tuned2', True),            # CMA-tuned on the scen families (oracle for them)
    'mrac_sataware': (None, 'mrac_sataware', True),
    'mrac5_xyz': (None, 'mrac5_xyz', False),
    'h0_sep_fw': ('ctrl_h0:H0_Sep', None, True),         # no-tuning adaptive layer on the firmware gains
    # tune_nominal.py: the same 2 x 64 CMA budget on nominal rows only (no oracle), read from <src>_tune.json
    'pid_nom': (None, 'pid_tuned2_nom', True),
    'mrac_sataware_nom': (None, 'mrac_sataware_nom', True),
    'mrac5_xyz_nom': (None, 'mrac5_xyz_nom', False),
    # ctrl_nn2.py (doc sec L): coupled RBF / 2-layer NN features on MRAC5_XYZ's x/y layers; class defaults, then _nom
    'rbf2_xyz': ('ctrl_nn2:RBF2_XYZ', None, False),
    'nn2_xyz': ('ctrl_nn2:NN2_XYZ', None, False),
    'rbf2_xyz_nom': (None, 'rbf2_xyz_nom', False),
    'nn2_xyz_nom': (None, 'nn2_xyz_nom', False),
    # tune_robust.py (doc sec M, item F): same budget on disturbed training rows (L1-L2, seed 300), read from <src>_tune.json
    'pid_rob': (None, 'pid_tuned2_rob', True),
    'mrac5_xyz_rob': (None, 'mrac5_xyz_rob', False),
}


def make(tag, B):
    spec, src, f1x = CTRLS[tag]
    params = None
    if src:
        st = json.load(open(f'results/{src}_tune.json' if src.endswith(('_nom', '_rob')) else f'results/{src}_test.json'))
        spec, params = st['ctrl'], {k: float(v) for k, v in st['params'].items()}
    ang, rate = fwpid.ANG_PR, fwpid.RATE_PR
    try:
        if f1x:
            fwpid.ANG_PR, fwpid.RATE_PR = dict(ang, **F1X_ANG), dict(rate, **F1X_RATE)
        return bench.load_cls(spec)(B, params)
    finally:
        fwpid.ANG_PR, fwpid.RATE_PR = ang, rate


def cmd_run(tags):
    rows, ref, sp = build()
    for tag in tags:
        t0 = time.time()
        L = run_ext(make(tag, len(rows)), ref, sp)
        met = bench.metrics(L, ref, rows)
        json.dump({'ctrl': tag, 'n': len(rows), 'rows': met}, open(f'results/stress_{tag}.json', 'w'))
        print(f'{tag}: {len(rows)} rows, div {np.mean([m["diverged"] for m in met]):.1%} ({time.time() - t0:.0f}s)',
              flush=True)


def summarise(met):
    """{axis: [(level, n, div frac, median rmse, p90 rmse)]}; diverged rows count as inf."""
    out = {}
    for axis in ['nominal'] + AXES:
        lv = sorted({int(m['fam'].split(':L')[1]) for m in met if m['fam'].split(':')[0] == axis})
        for l in lv:
            r = np.array([m['rmse'] for m in met if m['fam'] == f'{axis}:L{l}'])
            ok = np.isfinite(r)
            rr = np.where(ok, r, 1e9)                       # diverged = 1e9 so nearest-rank never interpolates
            pct = [float(x) if x < 1e8 else np.inf for x in np.percentile(rr, [50, 90], method='inverted_cdf')]
            out.setdefault(axis, []).append((l, len(r), float(np.mean(~ok)), *pct))
    return out


def break_point(levels, thr=0.25):
    return next((l for l, n, d, *_ in levels if d >= thr), None)


def cmd_report(tags):
    S = {t: summarise(json.load(open(f'results/stress_{t}.json'))['rows']) for t in tags}
    f = lambda x: 'div' if not np.isfinite(x) else f'{x:.3f}'
    lines = ['Divergence % per level (rows: 4 trajs x 3 seeds = 12 per cell; speed: 3 per cell); '
             'break = first level with >= 25 % diverged.', '',
             '| axis | ' + ' | '.join(f'{t} L0..L4 div % | {t} break' for t in tags) + ' |',
             '|---|' + '---|---|' * len(tags)]
    nom = {t: S[t]['nominal'][0] for t in tags}
    for axis in AXES:
        cells = []
        for t in tags:
            lv = S[t][axis] if axis == 'speed' else [nom[t]] + S[t][axis]
            cells += [' '.join(f'{100 * d:.0f}' for _, _, d, *_ in lv), str(break_point(lv) or '>4')]
        lines.append(f'| {axis} | ' + ' | '.join(cells) + ' |')
    lines += ['', 'p90 RMSE [m] per level, nearest rank (div = the p90 row diverged)', '',
              '| axis | ' + ' | '.join(tags) + ' |', '|---|' + '---|' * len(tags)]
    for axis in AXES:
        cells = []
        for t in tags:
            lv = S[t][axis] if axis == 'speed' else [nom[t]] + S[t][axis]
            cells.append(' '.join(f(p90) for *_, p90 in lv))
        lines.append(f'| {axis} | ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines)


def cmd_check():
    """run_ext without extension keys == plant.run bit for bit; with default-valued keys, same to rounding."""
    qs, refs = [], []
    for i, (tr, fam) in enumerate([('circle_1.0', 'wind'), ('zigzag_1.0', 'motor_loss'), ('hover', 'payload'),
                                   ('lem_1.5', 'combo_unseen')]):
        qs.append(scen.row_params(fam, scen.FAMS.index(fam), i, 7)); refs.append(scen.traj(tr))
    ref = {k: np.stack([r[k] for r in refs]) for k in refs[0]}
    sp = {k: np.array([q[k] for q in qs]) for k in qs[0]}
    N = 600
    ref = {k: v[:, :N] for k, v in ref.items()}
    a = plant.run(make('pid_tuned2', 4), ref, sp, seed=3)
    b = run_ext(make('pid_tuned2', 4), ref, sp, seed=3)
    same = all(np.array_equal(a[k], b[k]) for k in ('p', 'e', 'mot', 'phat', 'div_k'))
    sp2 = dict(sp, tau_m=np.full(4, TAU_M), delay=np.full(4, DELAY_TICKS), load_m=np.zeros(4),
               load_r=np.zeros((4, 3)), load_l=np.zeros(4), load_cable=np.zeros(4, bool), load_t_on=np.zeros(4),
               load_t_off=np.full(4, 1e9))
    c = run_ext(make('pid_tuned2', 4), ref, sp2, seed=3)
    dmax = max(float(np.abs(a[k].astype(float) - c[k]).max()) for k in ('p', 'e', 'mot'))
    print(f'bitwise (no keys): {same}; max |diff| with default keys: {dmax:.3g}')
    return 0 if same and dmax < 1e-9 else 1


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('cmd', choices=['check', 'run', 'report'])
    ap.add_argument('tags', nargs='*', default=list(CTRLS))
    a = ap.parse_args()
    if a.cmd == 'check':
        sys.exit(cmd_check())
    if a.cmd == 'run':
        cmd_run(a.tags)
    else:
        print(cmd_report([t for t in a.tags if __import__('os').path.exists(f'results/stress_{t}.json')]))
