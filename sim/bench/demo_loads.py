"""Bench prediction for the 10-06 asymmetric-load demo (campaigns asym_load_pid / asym_load_mrac).

Load cases on the nominal family (rigid load, no swing: a hung load's pendulum mode is NOT modelled):
  pad<g>     load centred under the body, hung H_PAD below the centre of mass: mass + m, roll/pitch J + m H_PAD^2
  arm<g>_m<k> load next to motor k, ARM_R from the centre: mass + m, centre of mass moves m r / (M + m) towards
             that motor (the plant's 'cog' offset), J + m r^2 about each axis
Motor k sits at MOTOR_XY[k] (signs read off the plant torque rows; bench k = firmware M(k+1) for roll and pitch,
API/controller.c g_mix). Yaw imbalance: the bench's nominal draw (scen u_imb 350-500 U, from the 09-27 spin flights)
runs the diagonal pair m2+m3 that much hotter than m0+m1, which leaves m0+m1 near the PWM floor. The 10-03 flights on
the flashed mixer hover with the pairs within 1-66 U (U_IMB_1003), so '--yaw 1003' (default) redraws u_imb there;
'--yaw logged' keeps the scen draw.
Trajectories: hover, and the bench zig-zag at 0.2 m/s (the demo's cruise speed; not the demo's waypoint list).
Controllers: the stored test-split parameters of pid_tuned2 and mrac_sataware (the variant in the firmware), both
'+f1x': the angle and rate integrator rows of the flashed API/pid.c (F1x: angle Ki 0.02 / Uimax 26 / SumEmax 1300 /
EMin 10, rate Uimax 160 / SumEmax 16000 / EMin 50) in place of the bench's pre-F1x rows; and mrac5_xyz (bench only,
not in the firmware; it replaces the x/y loops, so the F1x rows do not apply to it).

    python demo_loads.py [--seeds 10] [--yaw 1003|logged] [--sweep] [--out results/demo_loads.json]
    python demo_loads.py --tables results/demo_loads.json      # reprint the tables of a stored run
    python demo_loads.py --stress-tags pid_nom h0g_nom --out results/demo_loads_h0g.json
'--stress-tags' builds the controllers with stress.make (stress.CTRLS tags, their parameter sources and F1x flags)
instead of CTRLS; paired differences are then against the first tag.
'--sweep' runs the arm masses between the 250 g that holds and the 500 g that diverges (SWEEP) instead of CASES.
"""
import argparse
import json
import time

import numpy as np

import bench
import fwpid
import plant
import scen

H_PAD = 0.10                         # PROPOSED: load hangs ~10 cm below the centre of mass (not measured)
ARM_R = plant.ARM * np.sqrt(2.0)     # motor distance from the centre on the X frame
MOTOR_XY = {0: (1, -1), 1: (-1, 1), 2: (1, 1), 3: (-1, -1)}
# measured, logs/vofa/*drift_fix*_1.slot2.csv airborne (Throttle_out > 2600): (M1+M2-M3-M4)/4 = 66 U (drift_fix_1),
# 1 U (roaming_and_returning_1); gyrozPID.U median 12 / 0
U_IMB_1003 = (0.0, 70.0)
CASES = {'noload': (0.0, None), 'pad250': (0.25, 'pad'), 'pad500': (0.50, 'pad'),
         **{f'arm250_m{k}': (0.25, k) for k in range(4)}, 'arm500_m0': (0.50, 0), 'arm500_m2': (0.50, 2)}
SWEEP = {f'arm{g}_m{k}': (g / 1000, k) for g in (300, 350, 400, 450) for k in (0, 2)}
TRAJS = ['hover', 'zigzag_0.2']
CTRLS = [('pid_tuned2', True), ('mrac_sataware', True), ('mrac5_xyz', False)]
BASE = 'pid_tuned2+f1x'               # paired differences are against the flashed PID
F1X_ANG = dict(Ki=0.02, Uimax=26, SumEmax=1300, EMin=10)
F1X_RATE = dict(Uimax=160, SumEmax=16000, EMin=50)
ROPE = dict(m=0.57, L=0.43, r=(0.015, 0.0, -0.03), k=2000.0, c=5.0, th0=0.1, ph0=0.0)   # m, L measured (rope 33 cm +
# half bottle 10 cm, swing 0.76 Hz), x offset measured 1-2 cm; attach z, rope k/c and the initial swing PROPOSED
ROPE_CASES = {'rope570': {}, 'rope570_drop': dict(t_rel=10.0)}   # drop: the rope is cut at 10 s (load removal)
ARM_CASES = {f'arm293_m{k}': (0.293, k) for k in range(4)}   # the flown 293 g arm load; mount motor not known, so all 4


def label(tag, f1x):
    return tag + ('+f1x' if f1x else '')


def load_q(q, m, where):
    q = dict(q, J=q['J'].copy(), cog=q['cog'].copy())
    if where is None:
        return q
    M = q['mass']; q['mass'] = M + m
    if where == 'pad':
        q['J'][:2] += m * H_PAD ** 2
    else:
        x, y = np.array(MOTOR_XY[where]) * ARM_R / np.sqrt(2.0)
        q['cog'] += m / (M + m) * np.array([x, y])
        q['J'] += m * np.array([y * y, x * x, x * x + y * y])
    return q


def build(cases, seeds, yaw, trajs=TRAJS):
    rowlist, refs, qs = [], [], []
    for case, (m, where) in cases.items():
        for tr in trajs:
            for sd in seeds:
                q = load_q(scen.row_params('nominal', scen.FAMS.index('nominal'), 0, sd), m, where)
                if yaw == '1003':
                    q['u_imb'] = np.random.default_rng([sd, 1003]).uniform(*U_IMB_1003)
                refs.append(scen.traj(tr, q['z0'])); qs.append(q); rowlist.append((tr, case, sd))
    ref = {k: np.stack([d[k] for d in refs]) for k in refs[0]}
    sp = {k: np.array([q[k] for q in qs]) for k in qs[0]}
    return rowlist, ref, sp


def run(tag, f1x, rowlist, ref, sp, wrap=None):
    st = json.load(open(f'results/{tag}_test.json'))
    cls = bench.load_cls(st['ctrl'])
    ang, rate = fwpid.ANG_PR, fwpid.RATE_PR
    if f1x:
        fwpid.ANG_PR, fwpid.RATE_PR = dict(ang, **F1X_ANG), dict(rate, **F1X_RATE)
    try:
        ctrl = (wrap(cls) if wrap else cls)(len(rowlist), {k: float(v) for k, v in st['params'].items()})
    finally:
        fwpid.ANG_PR, fwpid.RATE_PR = ang, rate
    try:
        return bench.metrics(plant.run(ctrl, ref, sp, seed=3000), ref, rowlist)
    finally:
        if hasattr(ctrl, 'close'):
            ctrl.close()


def rope_ctrls(wu=()):
    """The flashed PID alone and with the flown 3L-v2 rows on top: vp row 17 (L2 only g8), row 19 (L2 g20 + D).
    wu: extra row 17 copies with the u_ad low-pass omega_u [rad/s] on pitch/roll raised from the firmware 4/5 (Q20)."""
    import ctrl_fwmrac
    rp = ctrl_fwmrac.rp
    r17 = rp.variants_3l()['L2only g8']['args']
    rows = {'row17': r17, 'row19': rp.variants_d()['L2only g20+D p20 f5']['args'],
            **{f'row17wu{w:g}': [*r17, f'cfg:0:omega_u:{w}', f'cfg:1:omega_u:{w}'] for w in wu}}
    return [(BASE, None)] + [(f'{BASE}+{k}', lambda c, a=a: ctrl_fwmrac.with_mrac(c, inj=1, cfg=a))
                             for k, a in rows.items()]


def run_rope(seeds, yaw, wu=()):
    """570 g bottle on the measured rope (plant.py sling), hover; each case is its own batch (sp['sling'] is shared)."""
    res = {}
    for case, extra in ROPE_CASES.items():
        rowlist, ref, sp = build({case: (0.0, None)}, seeds, yaw, ['hover'])
        sp['sling'] = dict(ROPE, r=np.array(ROPE['r']), **extra)
        for lb, wrap in rope_ctrls(wu):
            t0 = time.time(); res.setdefault(lb, []).extend(run('pid_tuned2', True, rowlist, ref, sp, wrap))
            print(f'{case} {lb}: {time.time() - t0:.0f} s', flush=True)
    return res


def run_arm(seeds, yaw, wu=()):
    """293 g rigid offset at each motor mount, hover, PID alone and with rows 17 / 19 (one batch: no shared sling)."""
    rowlist, ref, sp = build(ARM_CASES, seeds, yaw, ['hover'])
    res = {}
    for lb, wrap in rope_ctrls(wu):
        t0 = time.time(); res[lb] = run('pid_tuned2', True, rowlist, ref, sp, wrap)
        print(f'{lb}: {time.time() - t0:.0f} s', flush=True)
    return res


def run_stress(tag, rowlist, ref, sp):
    import stress   # stress imports this module at load time
    return bench.metrics(plant.run(stress.make(tag, len(rowlist)), ref, sp, seed=3000), ref, rowlist)


def med(rows, key='rmse'):
    a = np.array([r[key] for r in rows if not r['diverged']])
    return float(np.median(a)) if len(a) else float('nan')


def tables(res, cases, n_seeds, base_lb=BASE, trajs=TRAJS):
    labels = list(res)
    print('\nmedian RMSE over the non-diverged seeds [m] (n div = diverged seeds of ' + str(n_seeds) + ')\n')
    print('| case | traj | ' + ' | '.join(labels) + ' |\n' + '|---' * (len(labels) + 2) + '|')
    for case in cases:
        for tr in trajs:
            sel = {lb: [r for r in res[lb] if r['fam'] == case and r['traj'] == tr] for lb in labels}
            cells = []
            for lb in labels:
                div = sum(r['diverged'] for r in sel[lb])
                cells.append(f'{med(sel[lb]):.4f}' + (f' ({div} div)' if div else ''))
            print(f'| {case} | {tr} | ' + ' | '.join(cells) + ' |')
    others = [lb for lb in labels if lb != base_lb]
    if base_lb in res and others:
        print(f'\npaired median RMSE difference vs {base_lb} [m], 95% bootstrap CI, seeds where neither diverged '
              f'(negative = better than {base_lb})\n')
        print('| case | traj | ' + ' | '.join(others) + ' |\n' + '|---' * (len(others) + 2) + '|')
        for case in cases:
            for tr in trajs:
                base = {r['seed']: r for r in res[base_lb] if r['fam'] == case and r['traj'] == tr}
                cells = []
                for lb in others:
                    pr = [(r['rmse'], base[r['seed']]['rmse']) for r in res[lb] if r['fam'] == case and
                          r['traj'] == tr and not r['diverged'] and not base[r['seed']]['diverged']]
                    if len(pr) < 3:
                        cells.append(f'n={len(pr)}'); continue
                    d, lo, hi = bench.boot_median([x for x, _ in pr], [y for _, y in pr])
                    cells.append(f'{d:+.4f} [{lo:+.4f}, {hi:+.4f}]')
                print(f'| {case} | {tr} | ' + ' | '.join(cells) + ' |')
    print('\nmax tilt [deg] / mean sat / median rmse_z [m], hover only\n')
    print('| case | ' + ' | '.join(labels) + ' |\n' + '|---' * (len(labels) + 1) + '|')
    for case in cases:
        sel = {lb: [r for r in res[lb] if r['fam'] == case and r['traj'] == 'hover'] for lb in labels}
        print(f'| {case} | ' + ' | '.join(f"{max(r['tilt_max'] for r in sel[lb]):.1f} / "
                                          f"{np.mean([r['sat'] for r in sel[lb]]):.3f} / {med(sel[lb], 'rmse_z'):.4f}"
                                          for lb in labels) + ' |')


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--seeds', type=int, default=10)
    ap.add_argument('--yaw', choices=['1003', 'logged'], default='1003')
    ap.add_argument('--sweep', action='store_true'); ap.add_argument('--tables'); ap.add_argument('--stress-tags', nargs='+')
    ap.add_argument('--rope', action='store_true'); ap.add_argument('--arm', action='store_true')
    ap.add_argument('--wu', type=float, nargs='*', default=[])
    ap.add_argument('--out', default='results/demo_loads.json'); a = ap.parse_args()
    if a.tables:
        d = json.load(open(a.tables)); res = d['rows']
        tables(res, d['cases'], len({r['seed'] for r in next(iter(res.values()))}), d.get('base', BASE),
               d.get('trajs', TRAJS))
        return
    if a.rope:
        res = run_rope(list(range(2000, 2000 + a.seeds)), a.yaw, a.wu)
        json.dump({'cases': ROPE_CASES, 'rope': ROPE, 'trajs': ['hover'], 'f1x': [F1X_ANG, F1X_RATE], 'yaw': a.yaw,
                   'base': BASE, 'rows': res}, open(a.out, 'w'), indent=1)
        tables(res, ROPE_CASES, a.seeds, BASE, ['hover'])
        return
    if a.arm:
        res = run_arm(list(range(2000, 2000 + a.seeds)), a.yaw, a.wu)
        json.dump({'cases': {k: list(v) for k, v in ARM_CASES.items()}, 'h_pad': H_PAD, 'trajs': ['hover'],
                   'f1x': [F1X_ANG, F1X_RATE], 'yaw': a.yaw, 'base': BASE, 'rows': res}, open(a.out, 'w'), indent=1)
        tables(res, ARM_CASES, a.seeds, BASE, ['hover'])
        return
    cases = SWEEP if a.sweep else CASES
    rowlist, ref, sp = build(cases, list(range(2000, 2000 + a.seeds)), a.yaw)
    res = {}
    jobs = [(t, lambda t=t: run_stress(t, rowlist, ref, sp)) for t in a.stress_tags or []] or            [(label(t, f), lambda t=t, f=f: run(t, f, rowlist, ref, sp)) for t, f in CTRLS]
    for lb, job in jobs:
        t0 = time.time(); res[lb] = job()
        print(f'{lb}: {time.time() - t0:.0f} s', flush=True)
    base_lb = a.stress_tags[0] if a.stress_tags else BASE
    json.dump({'cases': {k: list(v) for k, v in cases.items()}, 'h_pad': H_PAD, 'f1x': [F1X_ANG, F1X_RATE],
               'yaw': a.yaw, 'u_imb_1003': U_IMB_1003, 'base': base_lb, 'rows': res}, open(a.out, 'w'), indent=1)
    tables(res, cases, a.seeds, base_lb)


if __name__ == '__main__':
    main()
