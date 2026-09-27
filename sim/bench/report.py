"""Leaderboard + per-family table from results/*_<split>.json (paired bootstrap, bench_v1 constraints).

  python report.py [--split test] [--ref pid_tuned2] [--out ../../.agent-ops/out/night/leaderboard.md]
Held-out subsets of the test split: held-out families (ground_effect, motor_loss), the unseen
combination (combo_unseen), and held-out trajectories (not in the tune split).
Constraints (bench_v1.json): div rate <= ref, sat <= 0.05, every family median <= 1.1 x ref.
"""
import argparse, glob, json, os
import numpy as np
import bench

CFG = json.load(open(os.path.join(bench.HERE, 'bench_v1.json')))
TUNE_TRAJ = set(CFG['splits']['tune']['trajs'])
HELD_FAM = ('ground_effect', 'motor_loss')


def key(d):
    return (d['traj'], d['fam'], d['seed'])


def med(rows):
    return float(np.median([d['rmse'] for d in rows])) if rows else float('nan')


def paired(ra, rb, sel):
    ka = [k for k in ra if sel(ra[k])]
    return bench.boot_median([ra[k]['rmse'] for k in ka], [rb[k]['rmse'] for k in ka])


def fmt_ci(t):
    return f"{t[0]:+.3f} [{t[1]:+.3f}, {t[2]:+.3f}]"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--split', default='test'); ap.add_argument('--ref', default='pid_tuned2')
    ap.add_argument('--out'); a = ap.parse_args()
    R = {}
    for f in sorted(glob.glob(os.path.join(bench.RES, f'*_{a.split}.json'))):
        d = json.load(open(f)); R[d['tag']] = {key(r): r for r in d['rows']}
    ref = a.ref if a.ref in R else 'pid_tuned'
    fams = [f for f in bench.scen.FAMS if any(k[1] == f for k in R[ref])]
    ref_fam = {f: med([r for k, r in R[ref].items() if k[1] == f]) for f in fams}
    ref_div = np.mean([r['diverged'] for r in R[ref].values()])
    order = sorted(R, key=lambda t: med(list(R[t].values())))
    L = [f"# Leaderboard ({a.split} split, bench {bench.BENCH_VERSION}; constraint/paired reference = {ref})", '',
         '| controller | median RMSE m | vs pid_fw (paired, 95% CI) | vs ' + ref + ' (paired, 95% CI) | held-out fam | '
         'unseen combo | held-out traj | div | sat | zigzag xtrack | constraints |',
         '|---|---|---|---|---|---|---|---|---|---|---|']
    for t in order:
        rows = R[t]; v = list(rows.values())
        div = np.mean([r['diverged'] for r in v]); sat = np.mean([r['sat'] for r in v])
        xt = [r['xtrack'] for r in v if r['xtrack'] is not None]
        famok = all(med([r for k, r in rows.items() if k[1] == f]) <= 1.1 * ref_fam[f] for f in fams)
        bad = [n for n, ok in (('div', div <= ref_div + 1e-9), ('sat', sat <= 0.05), ('fam', famok)) if not ok]
        c_fw = fmt_ci(paired(rows, R['pid_fw'], lambda r: True)) if 'pid_fw' in R and t != 'pid_fw' else '-'
        c_rf = fmt_ci(paired(rows, R[ref], lambda r: True)) if t != ref else '-'
        L.append(f"| {t} | {med(v):.3f} | {c_fw} | {c_rf} | "
                 f"{med([r for k, r in rows.items() if k[1] in HELD_FAM]):.3f} | "
                 f"{med([r for k, r in rows.items() if k[1] == 'combo_unseen']):.3f} | "
                 f"{med([r for k, r in rows.items() if k[0] not in TUNE_TRAJ]):.3f} | {div:.3f} | {sat:.3f} | "
                 f"{np.median(xt) if xt else float('nan'):.3f} | {'PASS' if not bad else 'FAIL ' + ','.join(bad)} |")
    L += ['', '## Per-family median RMSE (m); * = worse than 1.1 x ' + ref, '',
          '| family | ' + ' | '.join(order) + ' |', '|---|' + '---|' * len(order)]
    for f in fams:
        cells = []
        for t in order:
            m = med([r for k, r in R[t].items() if k[1] == f]); cells.append(f"{m:.3f}{'*' if m > 1.1 * ref_fam[f] else ''}")
        L.append(f"| {f} | " + ' | '.join(cells) + ' |')
    txt = '\n'.join(L) + '\n'
    print(txt)
    if a.out:
        open(a.out, 'w', encoding='utf-8').write(txt)


if __name__ == '__main__':
    main()
