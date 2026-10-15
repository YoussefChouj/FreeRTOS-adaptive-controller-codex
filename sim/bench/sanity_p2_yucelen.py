import numpy as np
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from sim.bench import bench, fwpid
from sim.bench.tierA import MRAC_S6
from sim.bench.tierP2_c1 import MRAC_ST, MRAC_ST_Barrier, MRAC_PR

rl = [
    ('zigzag_0.5', 'nominal', 0),
    ('circle_1.0', 'wind', 0),
    ('steps', 'payload', 1),
    ('lem_1.0', 'cog', 0),
    ('steps', 'motor_loss', 7)
]

max_u_global = {}

def wrap_step(cls):
    orig_step = cls.step
    def step(self, obs):
        out = orig_step(self, obs)
        cname = cls.__name__
        if cname not in max_u_global:
            max_u_global[cname] = 0.0
        max_u_global[cname] = max(max_u_global[cname], float(np.max(np.abs(out['U']))))
        return out
    cls.step = step

controllers = [fwpid.FwPID, MRAC_S6, MRAC_ST, MRAC_ST_Barrier, MRAC_PR]
for c in controllers:
    wrap_step(c)

print(f"{'Controller':<20} | {'RMSE':<30} | {'Max |U|':<10} | {'Diverged'}")
print("-" * 80)

for c in controllers:
    max_u_global[c.__name__] = 0.0
    res = bench.run_rows(c, {}, rl, 7)
    rmses = [round(d['rmse'], 3) for d in res]
    diverged = any(d['diverged'] for d in res)
    max_u = max_u_global[c.__name__]
    
    # Format rmses list as string
    rmses_str = str(rmses)
    
    print(f"{c.__name__:<20} | {rmses_str:<30} | {max_u:<10.1f} | {diverged}")
