# OF-IMU delay: shift the four flow channels by n replay samples vs gyro/tilt (n<0 = use later flow = flow arrives late), score.
import numpy as np, sys
S = sys.argv[1]; sys.argv = [sys.argv[0], S]
exec(open(S + 'o4k.py').read().split("fwc=dict")[0])
dts = np.median(np.diff(t)); print(f'replay dt {1000*dts:.1f} ms')
fwc = dict(of_gate=5.0, q_acc=1e-3, of1_gate=10.0); keys = ('ano_of.of2_dx_fix', 'ano_of.of2_dy_fix', 'ano_of.of1_dx', 'ano_of.of1_dy')
base = {k: d[k].copy() for k in keys}; segs = [(160.6, 167.6), (183.4, 190.8), (197.3, 204.7)]
for n in (-5, -3, -2, -1, 0, 1, 2, 3):
    for k in keys:
        b = base[k]; d[k] = np.r_[b[-n:], np.repeat(b[-1], -n)] if n < 0 else np.r_[np.repeat(b[0], n), b[:len(b) - n]]
    tt, px, py = R.replay_truth(d, [fwc], tilt_gain=0.242); ex, ey = earth(px[0], py[0])
    er = 100 * ((np.interp(G, tt, ex) + 1j * np.interp(G, tt, ey)) / 100 * rot - c)
    sg = ' '.join(f'{abs(er[np.searchsorted(G, sb)] - er[np.searchsorted(G, sa)]):5.1f}' for sa, sb in segs)
    print(f'shift {n:+d} ({1000*n*dts:+4.0f} ms) seg {sg} |', score(ex, ey, tt))
