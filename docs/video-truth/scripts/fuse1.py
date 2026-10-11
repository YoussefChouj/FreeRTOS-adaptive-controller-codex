# fuse1: replay of the live-fusion design sec 3 (earth-path complementary PI, ring buffer, age, gate, slew) on R3.
# Approximation: the Ki drift-rate term is a body-frame velocity added to the earth path (what a flow-bias trim does
# at low frequency). Scored in the camera frame. Hold-out: alternating on/off segments, drift scored only when off.
import numpy as np, sys
S = sys.argv[1]; sys.argv = [sys.argv[0], S]
exec(open(S + 'o4k.py').read().split("fwc=dict")[0])
fwc = dict(of_gate=5.0, q_acc=1e-3, of1_gate=10.0)
tt, px, py = R.replay_truth(d, [fwc], tilt_gain=0.242); ex, ey = earth(px[0], py[0])
t0 = 72.0; k0 = np.searchsorted(tt, t0)
ct = lambda q: np.interp(q, C['tt'], C['xy'][:, 0]) + 1j * np.interp(q, C['tt'], C['xy'][:, 1])
P = (ex + 1j * ey) / 100 * rot; P = P - P[k0]; Z = ct(tt) - ct(t0)          # path and truth, camera frame, m
yw = np.interp(tt, t, np.unwrap(yaw)); dtv = np.r_[0, np.diff(tt)]
def run(Kp, Ki, on, lat=0.3, Tf=1.0, gate=0.40, slew=0.03):
    Q = P.copy(); b = 0j; nxt = t0; pend = []; nrej = 0; nb = int(1.6 / 0.0095)
    for k in range(k0 + 1, len(tt)):
        Q[k] = Q[k - 1] + (P[k] - P[k - 1]) + b * np.exp(-1j * yw[k]) * dtv[k]
        if tt[k] >= nxt:
            if on(tt[k]): pend.append(tt[k])
            nxt += Tf
        while pend and tt[k] >= pend[0] + lat:
            tk = pend.pop(0); e = Z[np.searchsorted(tt, tk)] - Q[np.searchsorted(tt, tk)]
            if abs(e) > gate:
                nrej += 1
                if nrej < 3: continue
            nrej = 0; cp = Kp * e
            if abs(cp) > slew: cp *= slew / abs(cp)
            Q[max(k0, k - nb):k + 1] += cp; b += Ki * e * np.exp(1j * yw[k]) / Tf
    return Q
def sc(Q, off):
    E = 100 * (Q - Z); m = (tt >= t0); a = abs(E[m]); g = []; s = None
    for k in np.where(m)[0]:                       # drift growth inside each off segment
        if off(tt[k]) and s is None: s = k
        if (not off(tt[k]) or k == len(tt) - 1) and s is not None:
            if tt[k] - tt[s] > 5: g.append(abs(E[k] - E[s]) / (tt[k] - tt[s]))
            s = None
    return np.sqrt(np.mean(a ** 2)), np.percentile(a, 90), np.median(g) if g else np.nan, len(g)
print('variant                 rms   p90 | off-seg drift med cm/s (n)')
for per in (20, 10):
    off = lambda q, p=per: ((q - t0) // p) % 2 == 1; on = lambda q, p=per: not off(q, p)
    r = sc(P, off); print(f'none        seg{per:2d}     {r[0]:5.1f} {r[1]:5.1f} | {r[2]:5.2f} ({r[3]})')
    for Kp, Ki in ((0.3, 0.0), (0.3, 0.05), (0.3, 0.2), (1.0, 0.05), (0.1, 0.05)):
        r = sc(run(Kp, Ki, on), off); print(f'Kp{Kp:.1f} Ki{Ki:.2f} seg{per:2d}  {r[0]:5.1f} {r[1]:5.1f} | {r[2]:5.2f} ({r[3]})')
r = sc(run(0.3, 0.05, lambda q: True), lambda q: False); print(f'Kp0.3 Ki0.05 all-on    {r[0]:5.1f} {r[1]:5.1f} | circular')
