"""combo L1 arm load (A) + actuator lag (D) diagnosis, doc sec K.5: which part of the pair breaks the controllers.

    python combo_ad_diag.py     (prints per-variant divergence for pid_tuned2 and mrac5_xyz, 12 rows each)
"""
import numpy as np
import bench, scen, stress
from stress import TAU_M, DELAY_TICKS
lvl = 1
VAR = {
 'AD cable (combo L1)': {},
 'AD rigid':            dict(load_cable=False, load_l=0.0),
 'A + tau 1.5x only':   dict(delay=DELAY_TICKS),
 'A + delay 4 only':    dict(tau_m=TAU_M),
 'A 0.05 kg + D':       dict(load_m=0.05),
 'centre 0.1 kg cable + D': dict(load_r=np.array([0.0, 0.0, -0.03])),
 'D, no load':          dict(load_m=0.0),
}
trefs = {tr: (stress.lem_a(1.5) if tr == 'lemA_1.5' else scen.traj(tr)) for tr in stress.TRAJ_SET}
rows, refs, qs = [], [], []
for sd in stress.SEEDS:
    for ti, tr in enumerate(stress.TRAJ_SET):
        full = stress.apply(stress.base_q(sd, ti), 'combo', lvl, np.random.default_rng([sd, stress.AXES.index('combo'), lvl]))
        for name, over in VAR.items():
            q = stress.base_q(sd, ti)
            for k in ['load_m', 'load_r', 'load_l', 'load_cable', 'tau_m', 'delay']:
                q[k] = full[k]
            q.update(over)
            rows.append((tr, name, sd)); refs.append(trefs[tr]); qs.append(q)
ref = {k: np.stack([r[k] for r in refs]) for k in refs[0]}
sp = {k: np.array([q[k] for q in qs]) for k in qs[0]}
for tag in ['pid_tuned2', 'mrac5_xyz']:
    L = stress.run_ext(stress.make(tag, len(rows)), ref, sp)
    print(tag)
    for name in VAR:
        idx = [i for i, r in enumerate(rows) if r[1] == name]
        d = L['diverged'][idx]
        per = {tr: int(sum(L['diverged'][i] for i in idx if rows[i][0] == tr)) for tr in stress.TRAJ_SET}
        tk = L['div_k'][idx][d]
        print(f'  {name:26} div {int(d.sum()):2}/12  per traj {per}  t_div med {np.median(tk)*0.005 if len(tk) else float("nan"):.1f}s')
