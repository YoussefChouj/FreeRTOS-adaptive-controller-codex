"""Which ingredients of stress.py's combo ladder break the controllers at L1 (doc sec K.5).

Each ingredient alone at L1 diverges <= 8 % of rows, yet combo L1 (all four at L1) diverges 42-100 %.  This script
draws every combo row exactly as stress.build does (same rng, same order), then rebuilds it with only a subset of
the four ingredients switched on by copying that subset's keys from the full draw onto the base row.  So every
subset shares the same wind heading, kT sign, etc. (common random numbers).  Sensor noise is not shared with the
stress.py run: run_ext draws it for the whole batch from one rng, so a row's noise depends on the batch layout, and
the full subset 'WAKD' is a second noise draw of the stress.py combo rows.

    python combo_ablate.py [level=1] [ctrl ...]     -> results/combo_ablate_L<level>.json + table
"""
import itertools
import json
import sys

import numpy as np

import bench
import scen
import stress

KEYS = {
    'wind_gust': ['wind', 'dryden_sigma', 'gust_t', 'gust_dur', 'gust_vec'],
    'arm_load': ['load_m', 'load_r', 'load_l', 'load_cable'],
    'kt_mismatch': ['mgain'],
    'actuator': ['tau_m', 'delay'],
}
SHORT = {'wind_gust': 'W', 'arm_load': 'A', 'kt_mismatch': 'K', 'actuator': 'D'}


def build(level):
    subsets = [s for n in range(1, 5) for s in itertools.combinations(stress.COMBO, n)]
    trefs = {tr: (stress.lem_a(1.5) if tr == 'lemA_1.5' else scen.traj(tr)) for tr in stress.TRAJ_SET}
    rows, refs, qs = [], [], []
    for sd in stress.SEEDS:
        for ti, tr in enumerate(stress.TRAJ_SET):
            full = stress.apply(stress.base_q(sd, ti), 'combo', level,
                                np.random.default_rng([sd, stress.AXES.index('combo'), level]))
            for sub in subsets:
                q = stress.base_q(sd, ti)
                for a in sub:
                    q.update({k: full[k] for k in KEYS[a]})
                rows.append((tr, ''.join(SHORT[a] for a in sub), sd)); refs.append(trefs[tr]); qs.append(q)
    ref = {k: np.stack([r[k] for r in refs]) for k in refs[0]}
    sp = {k: np.array([q[k] for q in qs]) for k in qs[0]}
    return rows, ref, sp, [''.join(SHORT[a] for a in s) for s in subsets]


def main():
    args = sys.argv[1:]
    level = int(args.pop(0)) if args and args[0].isdigit() else 1
    tags = args or ['pid_tuned2', 'mrac_sataware', 'mrac5_xyz']
    rows, ref, sp, names = build(level)
    out = {}
    for tag in tags:
        L = stress.run_ext(stress.make(tag, len(rows)), ref, sp)
        met = bench.metrics(L, ref, rows)
        out[tag] = {n: {tr: int(sum(m['diverged'] for m, r in zip(met, rows) if r[1] == n and r[0] == tr))
                        for tr in stress.TRAJ_SET} for n in names}
    json.dump({'level': level, 'seeds': stress.SEEDS, 'trajs': stress.TRAJ_SET, 'div': out},
              open(f'results/combo_ablate_L{level}.json', 'w'), indent=1)
    n = len(stress.SEEDS) * len(stress.TRAJ_SET)
    print(f'combo L{level} subsets (W wind, A arm load, K kT, D actuator lag): diverged rows of {n}')
    print('subset ' + ' '.join(f'{t:>14}' for t in tags))
    for s in names:
        print(f'{s:6} ' + ' '.join(f'{sum(out[t][s].values()):>14}' for t in tags))


if __name__ == '__main__':
    main()
