"""4-row smoke check (CONTROLLER_API.md, seed 7) for the Tier A classes, defaults and with pid_tuned knobs."""
import json, os, sys, time
import numpy as np
import bench, tierA
from fwpid import FwPID

RL = [('zigzag_0.5', 'nominal', 0), ('circle_1.0', 'wind', 0), ('steps', 'payload', 1), ('lem_1.0', 'cog', 0)]
PT = json.load(open(os.path.join(bench.RES, 'pid_tuned_tune.json')))['params']
names = sys.argv[1:] or ['INDI', 'L1', 'MRAC_S6', 'MRAC_S10', 'MRAC_RBF6', 'MRAC_RBF12', 'MRAC_RBF24', 'SE3ESO']
for n, c in [('FwPID', FwPID)] + [(n, getattr(tierA, n)) for n in names]:
    for lab, par in (('default', {}), ('pid_tuned', {k: v for k, v in PT.items() if k in c.PARAMS})):
        if lab == 'pid_tuned' and n == 'SE3ESO':
            continue
        t0 = time.time(); rows = bench.run_rows(c, par, RL, 7)
        print(f"{n:11s} {lab:9s} rmse {np.round([r['rmse'] for r in rows], 3)} sat {np.mean([r['sat'] for r in rows]):.3f} "
              f"({time.time() - t0:.0f}s)", flush=True)
