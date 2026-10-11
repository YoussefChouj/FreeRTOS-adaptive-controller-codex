# fuse2: o5a EKF-state camera fusion (pos update, bof learned through the covariance) with on/off hold-out scoring.
# Fusion only inside "on" segments; drift scored inside "off" segments (camera tracked, not fused).
import numpy as np, sys
S = sys.argv[1]; A2 = sys.argv[2:]; sys.argv = [sys.argv[0], S]
exec(open(S + 'o4k.py').read().split("fwc=dict")[0])
import ground_station.analysis.ekf_of_model as M
fwc = dict(of_gate=5.0, q_acc=1e-3, of1_gate=10.0); t0 = 72.0
SH = float(A2[1]) if len(A2) > 1 else 0.0  # camera sync shift s: truth at tel time q = camera at q - SH
ct = lambda q: np.interp(q - SH, C['tt'], C['xy'][:, 0]) + 1j * np.interp(q - SH, C['tt'], C['xy'][:, 1]); c72 = ct(t0)
segoff = lambda q, p: p > 0 and ((q - t0) // p) % 2 == 1
# (name, period s, seg length s (0 = all on, -1 = never), Rc sd m, gate m)
V = [('none', 1, -1, .05, 0), ('on p1 R5', 1, 0, .05, 0), ('on p1 R1', 1, 0, .01, 0), ('on p2 R1', 2, 0, .01, 0), ('on p5 R1', 5, 0, .01, 0), ('on p1 R1 g40', 1, 0, .01, .4)]
B = len(V)
class Fused(M.EkfOfModel):
    def __init__(s, *a, **k):
        super().__init__(*a, **k); s.n = -400; s.anc = None; s.nxt = np.full(B, t0); s.pend = [[] for _ in range(B)]
        s.E = np.zeros(B, complex); s.pv = np.zeros(B, complex)
    def predict(s, dt, ax, ay):
        super().predict(dt, ax, ay); s.n += 1
        if s.n < 1: return
        tn = t[s.n]; cur = s.x[:, 0] + 1j * s.x[:, 3]; s.E += (cur - s.pv) * np.exp(-1j * yaw[s.n]); s.pv = cur
        if tn < t0: return
        if s.anc is None: s.anc = s.E.copy()
        for b, (_, per, seg, _, _) in enumerate(V):
            if tn >= s.nxt[b]:
                if seg >= 0 and not segoff(tn, seg): s.pend[b].append((tn, s.E[b]))
                s.nxt[b] += per
        for b, (_, _, _, rc, gt) in enumerate(V):
            while s.pend[b] and tn >= s.pend[b][0][0] + 0.3:
                tk, xk = s.pend[b].pop(0); ye = (ct(tk) - c72) / rot / 1.094 - (xk - s.anc[b])
                if gt > 0 and abs(ye) > gt: continue
                y = ye * np.exp(1j * yaw[s.n]); now = s.x[b, 0] + 1j * s.x[b, 3]; mk = np.zeros(B); mk[b] = 1
                s._update_one(0, [1, 0, 0, 0], np.full(B, (now + y).real), rc * rc, mk)
                s._update_one(1, [1, 0, 0, 0], np.full(B, (now + y).imag), rc * rc, mk)
R.EkfOfModel = Fused
tt, px, py = R.replay_truth(d, [fwc] * B, tilt_gain=0.242)
HEND = float(A2[0]) if A2 else 124.0; k0 = np.searchsorted(tt, t0); Z = ct(tt) - c72
def sc(Q, off):
    E = 100 * (Q - Z); m = tt >= t0; a = abs(E[m]); g = []; s = None
    for k in np.where(m)[0]:
        if off(tt[k]) and s is None: s = k
        if (not off(tt[k]) or k == len(tt) - 1) and s is not None:
            if tt[k] - tt[s] > 3 and tt[k] <= HEND: g.append(abs(E[k] - E[s]) / (tt[k] - tt[s]))
            s = None
    return np.sqrt(np.mean(a ** 2)), np.percentile(a, 90), np.median(g) if g else np.nan, len(g)
print('variant       hover72-124 rms  max | moves124+ rms  p90  max  (cm, camera frame, camera fused always)')
for b, (n, _, _, _, _) in enumerate(V):
    ex, ey = earth(px[b], py[b]); Q = (ex + 1j * ey) / 100 * rot; Q = Q - Q[k0]; E = abs(100 * (Q - Z))
    h = (tt >= t0) & (tt < HEND); m = tt >= HEND
    print(f'{n:14s} {np.sqrt(np.mean(E[h]**2)):10.1f} {E[h].max():5.1f} | {np.sqrt(np.mean(E[m]**2)):9.1f} {np.percentile(E[m],90):5.1f} {E[m].max():5.1f}')
