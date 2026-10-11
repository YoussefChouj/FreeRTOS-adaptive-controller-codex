# Replay with lever correction k*cf*gz added to flow; per-yaw-segment displacement error vs camera (cm) + whole-flight score.
import numpy as np, sys
S = sys.argv[1]; sys.argv = [sys.argv[0], S]
exec(open(S + 'o4k.py').read().split("fwc=dict")[0])
cf = (0.0625, -0.0742)                     # truth - fw = cf * gz (m/s per rad/s), lever.py fit t>=124
gzt = d['Gyro_Z_Real'] if 'Gyro_Z_Real' in d else np.interp(t, T['slot1.Gyro_Z_Real|t'], T['slot1.Gyro_Z_Real'])
fwc = dict(of_gate=5.0, q_acc=1e-3, of1_gate=10.0); base = {k: d[k].copy() for k in ('ano_of.of2_dx_fix', 'ano_of.of2_dy_fix', 'ano_of.of1_dx', 'ano_of.of1_dy')}
segs = [(160.6, 167.6), (183.4, 190.8), (197.3, 204.7)]
for kk in (0.0, 1.0, -1.0):
    for key, jj in (('ano_of.of2_dx_fix', 0), ('ano_of.of1_dx', 0), ('ano_of.of2_dy_fix', 1), ('ano_of.of1_dy', 1)):
        d[key] = base[key] + kk * 100 * cf[jj] * gzt
    tt, px, py = R.replay_truth(d, [fwc], tilt_gain=0.242); ex, ey = earth(px[0], py[0])
    er = 100 * ((np.interp(G, tt, ex) + 1j * np.interp(G, tt, ey)) / 100 * rot - c)
    sg = ' '.join(f'{abs(er[np.searchsorted(G, sb)] - er[np.searchsorted(G, sa)]):5.1f}' for sa, sb in segs)
    print(f'lever k{kk:+.0f} seg err cm {sg} |', score(ex, ey, tt))
