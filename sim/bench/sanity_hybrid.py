"""Sanity for ctrl_hybrid.Hybrid_RBF12_L1Yaw (H8), pid_tuned knobs + class defaults, 1 seed per row.
(a) hover/yaw_imb_hi: hybrid yaw RMSE within 10% of tierA.L1 and < 0.8 x tierA.MRAC_RBF12
(b) zigzag_1.0/nominal: hybrid position RMSE (the roll/pitch path) within 10% of tierA.MRAC_RBF12
(c) 4-row smoke on zigzag_0.5 (nominal, wind, payload, yaw_imb_hi): all finite, median RMSE per class
"""
import json, time
import numpy as np
import bench, tierA
from ctrl_hybrid import Hybrid_RBF12_L1Yaw as H

P = json.load(open('results/pid_tuned_tune.json'))['params']
CL = {'hybrid': H, 'l1': tierA.L1, 'rbf12': tierA.MRAC_RBF12}


def run(rows):
    return {n: bench.run_rows(c, P, rows, seed=3) for n, c in CL.items()}


a = run([('hover', 'yaw_imb_hi', 3)])
ya = {n: r[0]['rmse_yaw'] for n, r in a.items()}
ok_a = abs(ya['hybrid'] - ya['l1']) <= 0.1 * ya['l1'] and ya['hybrid'] < 0.8 * ya['rbf12']
print('(a) yaw rmse deg', {k: round(v, 3) for k, v in ya.items()}, 'PASS' if ok_a else 'FAIL')

b = run([('zigzag_1.0', 'nominal', 3)])
pb = {n: r[0]['rmse'] for n, r in b.items()}
ok_b = abs(pb['hybrid'] - pb['rbf12']) <= 0.1 * pb['rbf12']
print('(b) pos rmse m', {k: round(v, 4) for k, v in pb.items()}, 'PASS' if ok_b else 'FAIL')

t0 = time.time()
c = run([('zigzag_0.5', f, 3) for f in ('nominal', 'wind', 'payload', 'yaw_imb_hi')])
ok_c = all(np.isfinite(d['rmse']) for d in c['hybrid'])
print('(c) median rmse m', {n: round(float(np.median([d['rmse'] for d in r])), 4) for n, r in c.items()},
      'PASS' if ok_c else 'FAIL', '%.0f s (3 classes)' % (time.time() - t0))
