from ground_station.research.sim.cascade_rank import calibrate, evaluate_candidates
from ground_station.research.sim.cascade import simulate
c = calibrate()
scene = {}
res = simulate(c, scene, 10.0)
import numpy as np
print(np.mean(res["pos_x"]), np.mean(res["pos_y"]))
