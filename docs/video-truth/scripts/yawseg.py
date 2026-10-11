import numpy as np, sys
exec(open(sys.argv[0].replace('yawseg.py', 'nn1.py')).read().split("pred = {k:")[0])
gz = X[:, 5]; dt = np.median(np.diff(t)); yu = np.unwrap(yaw)
g1 = np.convolve(gz, np.ones(100) / 100, 'same'); on = np.abs(g1) > 0.3
st = np.flatnonzero(np.diff(on.astype(int)) == 1) + 1; en = np.flatnonzero(np.diff(on.astype(int)) == -1) + 1
E = np.exp(-1j * yaw); tre = vb * E; fwe = fw * E
lev = -(0.0625 - 0.0742j) * gz           # lever.py fits truth-fw = cf*gz, so EKF err = -cf*gz
print(' seg s        int(gz) dyaw_unwrap | cam disp | EKF err vec | lever pred err | resid |  max|gz|')
for a, b in zip(st, en):
    if b - a < 150: continue
    s = slice(a, b); dc = 100 * 1.094 * tre[s].sum() * dt; er = 100 * 1.094 * (fwe[s] - tre[s]).sum() * dt
    lp = 100 * 1.094 * (lev[s] * E[s]).sum() * dt
    print(f'{t[a]:5.1f}-{t[b]:5.1f} {np.degrees(gz[s].sum()*dt):+6.0f} {np.degrees(yu[b]-yu[a]):+6.0f} | {abs(dc):5.1f} | {er.real:+6.1f}{er.imag:+6.1f} | {lp.real:+6.1f}{lp.imag:+6.1f} | {abs(er-lp):5.1f} | {np.abs(gz[s]).max():.2f}')
# hover baseline: same-length windows with |gz|<0.05 for comparison
q = np.flatnonzero(np.abs(g1) < 0.05); w = 700; errs = []
for a in range(q[0], len(t) - w, w):
    s = slice(a, a + w)
    if np.all(np.abs(g1[s]) < 0.05): errs.append(abs(100 * 1.094 * (fwe[s] - tre[s]).sum() * dt))
print(f'calm 7 s windows: n {len(errs)}, EKF err |disp| median {np.median(errs):.1f} max {np.max(errs):.1f} cm')
