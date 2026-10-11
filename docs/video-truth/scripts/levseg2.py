import numpy as np, sys
S = sys.argv[1]; sys.argv = [sys.argv[0], S]
exec(open(S + 'o4k.py').read().split("fwc=dict")[0])
cf = (0.0625, -0.0742)
gzt = d['Gyro_Z_Real'] if 'Gyro_Z_Real' in d else np.interp(t, T['slot1.Gyro_Z_Real|t'], T['slot1.Gyro_Z_Real'])
yd = np.nan_to_num(np.gradient(np.unwrap(yaw), t), posinf=0, neginf=0); yd = np.clip(yd, -5, 5); ydl = np.convolve(yd, np.ones(20) / 20, 'same')
g1 = np.convolve(gzt, np.ones(100) / 100, 'same'); calm = np.abs(g1) < 0.05
print(f'gz median calm {np.median(gzt[calm]):+.4f} rad/s, -d(yaw)/dt median calm {np.median(-yd[calm]):+.4f}, gz/(-dyaw) slope {np.polyfit(-ydl, gzt, 1)[0]:.3f}')
fwc = dict(of_gate=5.0, q_acc=1e-3, of1_gate=10.0); base = {k: d[k].copy() for k in ('ano_of.of2_dx_fix', 'ano_of.of2_dy_fix', 'ano_of.of1_dx', 'ano_of.of1_dy')}
segs = [(160.6, 167.6), (183.4, 190.8), (197.3, 204.7)]
V = [('gz - calm median', gzt - np.median(gzt[calm])), ('gz, |g1|>0.3 only', gzt * (np.abs(g1) > 0.3)), ('-dyaw/dt (att)', -ydl), ('gz x0.5', 0.5 * gzt)]
for nm, w in V:
    for key, jj in (('ano_of.of2_dx_fix', 0), ('ano_of.of1_dx', 0), ('ano_of.of2_dy_fix', 1), ('ano_of.of1_dy', 1)):
        d[key] = base[key] + 100 * cf[jj] * w
    tt, px, py = R.replay_truth(d, [fwc], tilt_gain=0.242); ex, ey = earth(px[0], py[0])
    er = 100 * ((np.interp(G, tt, ex) + 1j * np.interp(G, tt, ey)) / 100 * rot - c)
    sg = ' '.join(f'{abs(er[np.searchsorted(G, sb)] - er[np.searchsorted(G, sa)]):5.1f}' for sa, sb in segs)
    print(f'{nm:18s} seg {sg} |', score(ex, ey, tt))
