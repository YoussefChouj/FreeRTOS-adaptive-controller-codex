# Lever-arm (omega_z x r) flow correction: fit on R3 t>=124 s (camera truth), score hover drift on held-out t<124 s.
import numpy as np, sys
S = sys.argv[1]; sys.argv = [sys.argv[0], S]
exec(open(S + 'o4k.py').read().split("fwc=dict")[0])
N = np.load(sys.argv[0].replace('lever.py', 'nn2.npz')); mk = N['ts'] >= 124; E = N['Y'] - N['FW']; gz = N['gyr'][:, 2]
Ag = np.c_[gz, np.ones(len(gz))]
cf = [np.linalg.lstsq(Ag[mk], E[mk, j], rcond=None)[0][0] for j in (0, 1)]
print(f'fit on t>=124: err_x {100*cf[0]:+.2f} err_y {100*cf[1]:+.2f} cm/s per rad/s -> |r| {100*np.hypot(*cf):.1f} cm')
gzt = d['Gyro_Z_Real'] if 'Gyro_Z_Real' in d else np.interp(t, T['slot1.Gyro_Z_Real|t'], T['slot1.Gyro_Z_Real'])
fwc = dict(of_gate=5.0, q_acc=1e-3, of1_gate=10.0); base = {k: d[k].copy() for k in ('ano_of.of2_dx_fix', 'ano_of.of2_dy_fix', 'ano_of.of1_dx', 'ano_of.of1_dy')}
fx, fy, ft = T['slot0.ano_of.earth_x'], T['slot0.ano_of.earth_y'], T['slot0.ano_of.earth_x|t']; print(f'{"FW LOGGED (gate 5)":22s}', score(fx, fy, ft))
for k in (0.0, 0.5, 1.0, 1.5, -1.0):
    for key, jj in (('ano_of.of2_dx_fix', 0), ('ano_of.of1_dx', 0), ('ano_of.of2_dy_fix', 1), ('ano_of.of1_dy', 1)):
        d[key] = base[key] + k * 100 * cf[jj] * gzt
    tt, px, py = R.replay_truth(d, [fwc], tilt_gain=0.242)
    print(f'lever x{k:+.1f} {"":10s}', score(*earth(px[0], py[0]), tt))
