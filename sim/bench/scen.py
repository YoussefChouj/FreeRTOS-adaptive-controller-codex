"""bench_v1 scenarios: trajectories (onboard reference generator), disturbance families, splits.

Each row = (trajectory, family, seed).  Row parameters come from an RNG seeded by
(seed, family index, trajectory index) only, so a row is identical whatever batch it is in.
"""
import numpy as np
from plant import J0, MASS, DT_C, TOF_EVERY, OF_EVERY

T_END, T_HOLD, Z0 = 20.0, 3.0, 1.0
N = int(T_END / DT_C)
TRAJS = ['hover', 'steps', 'circle_0.5', 'circle_1.0', 'lem_0.5', 'lem_1.0', 'lem_1.5',
         'minsnap', 'yaw_translate', 'zigzag_0.5', 'zigzag_1.0']
TUNE_FAMS = ['nominal', 'wind', 'dryden', 'gust', 'payload', 'cog', 'yaw_imb_hi', 'battery',
             'sensor_noise', 'dropout', 'jitter', 'combo_wind_payload']
TEST_ONLY_FAMS = ['ground_effect', 'motor_loss', 'combo_unseen']   # held out / unseen combination
FAMS = TUNE_FAMS + TEST_ONLY_FAMS
SPLITS = {'tune': dict(fams=TUNE_FAMS, trajs=['steps', 'circle_1.0', 'lem_1.0', 'zigzag_0.5'], seeds=[0, 1]),
          'test': dict(fams=FAMS, trajs=TRAJS, seeds=[100, 101, 102])}


def septic(tau):
    tau = np.clip(tau, 0, 1)
    return tau ** 4 * (35 - 84 * tau + 70 * tau ** 2 - 20 * tau ** 3)


def warp(t, ramp=2.0):
    """Phase time with a smooth start after the hold: d(warp)/dt goes 0 -> 1 over `ramp` s."""
    s = np.clip((t - T_HOLD) / ramp, 0, 1)
    rate = s ** 3 * (10 - 15 * s + 6 * s * s)
    return np.cumsum(rate) * DT_C


def zigzag(t, v):
    L, dy, Ty = 1.0, 0.05, 0.6
    Tl = 2.1875 * L / v
    x = np.zeros_like(t); y = np.zeros_like(t)
    t0, xs, ys, d = T_HOLD, 0.0, 0.0, 1.0
    while t0 < T_END:
        m = t >= t0
        x[m] = xs + d * L * septic((t[m] - t0) / Tl); y[m] = ys
        t0 += Tl; xs += d * L; d = -d
        m = t >= t0
        y[m] = ys + dy * septic((t[m] - t0) / Ty)
        t0 += Ty; ys += dy
    return x, y


def traj(name, z0=Z0):
    t = np.arange(N) * DT_C
    x = np.zeros(N); y = np.zeros(N); z = np.full(N, z0); yaw = np.zeros(N)
    if name == 'steps':
        for ts, (a, b, c) in [(3, (0.5, 0, 0)), (7, (0.5, 0.5, 0)), (11, (0.5, 0.5, 0.3)), (15, (0, 0, 0))]:
            m = t >= ts
            x[m], y[m], z[m] = a, b, z0 + c
    elif name.startswith('circle') or name == 'yaw_translate':
        v = 0.5 if name == 'yaw_translate' else float(name.split('_')[1])
        R = 0.6; th = v / R * warp(t)
        x, y = R * np.cos(th) - R, R * np.sin(th)
        if name == 'yaw_translate':
            yaw = 45.0 * warp(t)
            yaw = (yaw + 180) % 360 - 180
    elif name.startswith('lem'):
        v = float(name.split('_')[1]); A = 0.8
        th = v / (np.sqrt(2) * A) * warp(t)
        x, y = A * np.sin(th), 0.5 * A * np.sin(2 * th)
    elif name == 'minsnap':
        wp = np.array([[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]], float)
        for i in range(4):
            s = septic((t - T_HOLD - 3 * i) / 3.0)
            m = t >= T_HOLD + 3 * i
            x[m] = wp[i, 0] + (wp[i + 1, 0] - wp[i, 0]) * s[m]
            y[m] = wp[i, 1] + (wp[i + 1, 1] - wp[i, 1]) * s[m]
    elif name.startswith('zigzag'):
        x, y = zigzag(t, float(name.split('_')[1]))
    p = np.stack([x, y, z], 1)
    if name == 'steps':
        v = np.zeros_like(p); a = np.zeros_like(p)
    else:
        v = np.gradient(p, DT_C, axis=0); a = np.gradient(v, DT_C, axis=0)
    return dict(p=p, v=v, a=a, yaw=yaw)


def row_params(fam, fi, ti, seed):
    r = np.random.default_rng([seed, fi, ti, 7])
    nt, no = N // TOF_EVERY + 2, N // OF_EVERY + 2
    q = dict(noise_scale=1.0, mass=MASS, J=J0.copy(), V0=r.uniform(15.0, 16.6), vsag=0.01, vstep=0.0,
             vstep_t=1e9, mgain=1 + 0.03 * r.standard_normal(4), mloss_t=1e9, mloss_idx=0, mloss_eff=1.0,
             dryden_sigma=0.0, wind=np.zeros(3), gust_t=np.full(2, 1e9), gust_dur=np.ones(2),
             gust_vec=np.zeros((2, 3)), cog=np.zeros(2), u_imb=r.uniform(350, 500),
             gyro_bias=0.3 * r.standard_normal(3), acc_bias=0.05 * r.standard_normal(3),
             tof_spike=np.zeros(nt), tof_drop=np.zeros(nt, bool), of_drop=np.zeros(no, bool),
             of_delay=8, of_scale=0.03 * r.standard_normal(), z0=Z0)
    parts = {'combo_wind_payload': ['wind', 'payload'], 'combo_unseen': ['dryden', 'cog', 'yaw_imb_hi']}.get(fam, [fam])
    for f in parts:
        hd = r.uniform(0, 2 * np.pi)
        if f == 'wind':
            q['wind'] = r.uniform(2, 4) * np.array([np.cos(hd), np.sin(hd), 0])
        elif f == 'dryden':
            q['dryden_sigma'] = r.uniform(0.7, 1.5)
        elif f == 'gust':
            q['gust_t'] = np.sort(r.uniform(5, 17, 2)); q['gust_dur'] = r.uniform(0.8, 1.5, 2)
            for j in range(2):
                h = r.uniform(0, 2 * np.pi)
                q['gust_vec'][j] = r.uniform(3, 5) * np.array([np.cos(h), np.sin(h), 0])
        elif f == 'payload':
            k = r.uniform(1.15, 1.30); q['mass'] = MASS * k; q['J'] = J0 * (1 + 0.5 * (k - 1))
        elif f == 'cog':
            q['cog'] = r.uniform(0.01, 0.02) * np.array([np.cos(hd), np.sin(hd)])
        elif f == 'yaw_imb_hi':
            q['u_imb'] = r.uniform(550, 650)
        elif f == 'battery':
            q['V0'] = r.uniform(14.2, 14.6); q['vsag'] = 0.03; q['vstep'] = 0.3; q['vstep_t'] = r.uniform(5, 15)
        elif f == 'sensor_noise':
            q['noise_scale'] = 3.0
        elif f == 'dropout':
            for key, n, dt in (('of_drop', no, OF_EVERY * DT_C), ('tof_drop', nt, TOF_EVERY * DT_C)):
                for _ in range(3):
                    s = r.uniform(4, 18); d = r.uniform(0.3, 1.0)
                    q[key][int(s / dt):int((s + d) / dt)] = True
            q['tof_spike'] = (r.random(nt) < 0.01) * r.uniform(0.2, 0.8, nt)
        elif f == 'jitter':
            q['of_delay'] = int(r.integers(8, 17)); q['of_drop'] = r.random(no) < 0.2
        elif f == 'ground_effect':
            q['z0'] = 0.35
        elif f == 'motor_loss':
            q['mloss_t'] = 8.0; q['mloss_idx'] = int(r.integers(0, 4)); q['mloss_eff'] = r.uniform(0.75, 0.85)
    return q


def rows(split):
    s = SPLITS[split]
    return [(tr, fa, sd) for fa in s['fams'] for tr in s['trajs'] for sd in s['seeds']]


def build(rowlist):
    """Stack per-row trajectories and parameters into batch arrays for plant.run."""
    refs, qs = [], []
    for tr, fa, sd in rowlist:
        q = row_params(fa, FAMS.index(fa), TRAJS.index(tr), sd)
        refs.append(traj(tr, q['z0'])); qs.append(q)
    ref = {k: np.stack([d[k] for d in refs]) for k in refs[0]}
    sp = {k: np.array([q[k] for q in qs]) for k in qs[0]}
    return ref, sp
