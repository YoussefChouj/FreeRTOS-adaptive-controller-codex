# Cross-fit lever coefficient: fit on one yaw segment (nn2 residual truth-fw vs gz), test replay on the other.
import numpy as np, sys
S = sys.argv[1]; sys.argv = [sys.argv[0], S]
exec(open(S + 'o4k.py').read().split("fwc=dict")[0])
N = np.load(sys.argv[0].replace('levcv.py', 'nn2.npz')); Er = N['Y'] - N['FW']; gq = N['gyr'][:, 2]; tq = N['ts']
gzt = d['Gyro_Z_Real'] if 'Gyro_Z_Real' in d else np.interp(t, T['slot1.Gyro_Z_Real|t'], T['slot1.Gyro_Z_Real'])
fwc = dict(of_gate=5.0, q_acc=1e-3, of1_gate=10.0); base = {k: d[k].copy() for k in ('ano_of.of2_dx_fix', 'ano_of.of2_dy_fix', 'ano_of.of1_dx', 'ano_of.of1_dy')}
segs = [(160.6, 167.6), (183.4, 190.8), (197.3, 204.7)]
for fi, (fa, fb) in ((0, segs[0]), (2, segs[2]), (-1, (124, 236)), (-2, (70, 124))):
    mk = (tq >= fa) & (tq < fb); Ag = np.c_[gq[mk], np.ones(mk.sum())]
    cf = [np.linalg.lstsq(Ag, Er[mk, jj], rcond=None)[0][0] for jj in (0, 1)]
    for key, jj in (('ano_of.of2_dx_fix', 0), ('ano_of.of1_dx', 0), ('ano_of.of2_dy_fix', 1), ('ano_of.of1_dy', 1)):
        d[key] = base[key] + 100 * cf[jj] * gzt
    tt, px, py = R.replay_truth(d, [fwc], tilt_gain=0.242); ex, ey = earth(px[0], py[0])
    er = 100 * ((np.interp(G, tt, ex) + 1j * np.interp(G, tt, ey)) / 100 * rot - c)
    sg = ' '.join(f'{abs(er[np.searchsorted(G, sb)] - er[np.searchsorted(G, sa)]):5.1f}' for sa, sb in segs)
    print(f'fit {fa:5.1f}-{fb:5.1f} cf {100*cf[0]:+5.2f} {100*cf[1]:+5.2f} |r| {100*np.hypot(*cf):4.1f} | seg {sg} |', score(ex, ey, tt))
