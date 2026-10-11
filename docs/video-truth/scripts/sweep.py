# R1 ranked items #2 (freeze accel bias), bias random walk, #5 flow scale, #6 tilt-corrected ToF height. In-sample, R3 only.
import numpy as np, sys
S = sys.argv[1]; sys.argv = [sys.argv[0], S]
exec(open(S + 'o4k.py').read().split("fwc=dict")[0])
fwc = dict(of_gate=5.0, q_acc=1e-3, of1_gate=10.0)
V = [('base gate10', {}), ('q_ba 0 (frozen)', dict(q_ba=0.0)), ('q_ba 1e-8', dict(q_ba=1e-8)), ('q_bof 1e-6', dict(q_bof=1e-6)),
     ('q_bof 1e-4', dict(q_bof=1e-4)), ('q_ba0 q_bof1e-6', dict(q_ba=0.0, q_bof=1e-6)), ('flow_scale 0.95', dict(flow_scale=0.95)),
     ('flow_scale 1.05', dict(flow_scale=1.05)), ('flow_scale 0.90', dict(flow_scale=0.90)), ('R_of1 0 (of1 off)', dict(R_of1=0.0))]
grid = [dict(fwc, **v) for _, v in V]
tt, px, py = R.replay_truth(d, grid, tilt_gain=0.242)
for k, (n, _) in enumerate(V): print(f'{n:20s}', score(*earth(px[k], py[k]), tt))
a0 = d['ano_of.of_alt_cm'].copy(); d['ano_of.of_alt_cm'] = a0 * np.cos(np.radians(d['imu_data.pit'])) * np.cos(np.radians(d['imu_data.rol']))
tt, px, py = R.replay_truth(d, grid[:1], tilt_gain=0.242); print(f'{"alt*cos tilt":20s}', score(*earth(px[0], py[0]), tt))
print('pit/rol units check: max |pit|', float(np.max(np.abs(d['imu_data.pit']))))
