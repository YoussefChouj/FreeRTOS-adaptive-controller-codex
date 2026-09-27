"""H6b sub-claim: MRAC3L_Predictive vs MRAC3L_Unrouted on corner-heavy test rows (zigzag*, steps).
  python h6b_subset.py      (reads results/mrac3l_{predictive,unrouted}_test.json; paired, diverged = inf)
"""
import numpy as np
import bench, ledger

key = lambda d: (d['traj'], d['fam'], d['seed'])
f = lambda d: d['traj'].startswith('zigzag') or d['traj'].startswith('step')


def sub(tag):
    return {key(d): (np.inf if d['diverged'] else d['rmse']) for d in ledger.load(tag)['rows'] if f(d)}


a, b = sub('mrac3l_predictive'), sub('mrac3l_unrouted'); k = sorted(a)
print('predictive - unrouted, zigzag+steps n=%d: %.4f [%.4f, %.4f]' % ((len(k),) + tuple(
    bench.boot_median([a[x] for x in k], [b[x] for x in k]))))
