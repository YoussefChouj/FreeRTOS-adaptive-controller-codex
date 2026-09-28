import sys
import numpy as np
import bench, fwpid, ctrl_mrac
import tierP2_c2
import time

rl = [
    ('zigzag_0.5','nominal',0), 
    ('circle_1.0','wind',0), 
    ('steps','payload',1), 
    ('lem_1.0','cog',0)
]

controllers = [
    fwpid.FwPID, 
    ctrl_mrac.MRAC_RBF24,
    tierP2_c2.MRAC_RBF48,
    tierP2_c2.MRAC_RBF96,
    tierP2_c2.MRAC_PhysRBF,
    tierP2_c2.MRAC_Deep
]

print("Running 4-row quick check:")
for c in controllers:
    t0 = time.time()
    res = bench.run_rows(c, {}, rl, 7)
    t1 = time.time()
    rmses = [round(d['rmse'], 3) for d in res]
    max_u = [round(np.max(np.abs(d['mot'])), 1) if 'mot' in d else 0 for d in res]
    diverged = [d.get('diverged', False) for d in res]
    print(f"{c.__name__}:")
    print(f"  rmse: {rmses}")
    print(f"  max |U|: {max_u}")
    print(f"  diverged: {diverged}")
    print(f"  time: {t1 - t0:.2f} s")

print("\nRunning ('steps','motor_loss',7):")
for c in controllers:
    t0 = time.time()
    res = bench.run_rows(c, {}, [('steps','motor_loss',7)], 7)
    t1 = time.time()
    rmses = [round(d['rmse'], 3) for d in res]
    print(f"{c.__name__}: rmse {rmses} in {t1 - t0:.2f} s")

