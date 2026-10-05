"""Item F of the adaptive-arch study (doc sec M): tune on disturbed rows instead of nominal-only ones.

Same tuner and budget as tune_nominal.py (bench.tune, CMA-ES, 2 x 64 evals, starting from the class defaults), but the
training rows are stress-ladder rows: the 4 ladder trajectories x (nominal + the 8 LAD axes at L1 and L2) on seed(s)
disjoint from the ladder's (200-202).  combo, speed and L3-L4 are held out, so the ladder still tests extrapolation.
The rows are built exactly as stress.build builds them and run through stress.run_ext; bench.py stays frozen (its
scen.rows / run_rows lookups are swapped at runtime).
  python tune_robust.py pid_tuned2 mrac5_xyz    ->  results/<tag>_rob_tune.json (stress.CTRLS pid_rob, mrac5_xyz_rob)
"""
import sys
import numpy as np
import bench, scen, stress, tune_nominal

SEEDS = [300]
LEVELS = [1, 2]


def rob_rows(seeds=SEEDS, levels=LEVELS):
    return [(tr, f'{ax}:L{lv}', sd) for sd in seeds for tr in stress.TRAJ_SET
            for ax, lv in [('nominal', 0)] + [(a, l) for a in stress.LAD for l in levels]]


def build_rows(rowlist):
    """(ref, sp) for (traj, 'axis:Lk', seed) rows, as stress.build makes them."""
    trefs = {tr: (stress.lem_a(1.5) if tr == 'lemA_1.5' else scen.traj(tr)) for tr in stress.TRAJ_SET}
    refs, qs = [], []
    for tr, fam, sd in rowlist:
        axis, lvl = fam.split(':L'); lvl = int(lvl)
        q = stress.base_q(sd, stress.TRAJ_SET.index(tr))
        if lvl:
            stress.apply(q, axis, lvl, np.random.default_rng([sd, stress.AXES.index(axis), lvl]))
        refs.append(trefs[tr]); qs.append(q)
    ref = {k: np.stack([r[k] for r in refs]) for k in refs[0]}
    sp = {k: np.array([q[k] for q in qs]) for k in qs[0]}
    return ref, sp


_CACHE = {}


def run_rows(cls, params, rowlist, seed, tile=1):
    """bench.run_rows on stress rows: candidate-major tiling, stress.run_ext, bench.metrics."""
    n = len(rowlist); key = tuple(rowlist)
    if key not in _CACHE:
        _CACHE[key] = build_rows(rowlist)
    ref1, sp1 = _CACHE[key]
    ref = {k: np.concatenate([v] * tile) for k, v in ref1.items()}
    sp = {k: np.concatenate([v] * tile) for k, v in sp1.items()}
    pb = {k: (np.repeat(np.asarray(v, float), n) if np.ndim(v) else float(v)) for k, v in params.items()}
    L = stress.run_ext(cls(n * tile, pb), ref, sp, seed=seed, tile=tile)
    return bench.metrics(L, ref, rowlist * tile)


if __name__ == '__main__':
    rows = rob_rows()
    bench.scen.rows = lambda split: list(rows)
    bench.run_rows = run_rows
    scen.SPLITS['tune'] = dict(rows='stress', seeds=SEEDS, levels=LEVELS, trajs=stress.TRAJ_SET, axes=list(stress.LAD))
    print(f'{len(rows)} training rows', flush=True)
    for t in sys.argv[1:]:
        tune_nominal.tune_one(t, suffix='rob')
