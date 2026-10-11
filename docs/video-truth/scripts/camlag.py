# camlag: is the 0.3 s fused-estimate lag a camera/telemetry sync offset or a real estimate lag?
# Cross-correlate camera body velocity with raw flow (physical delay ~32 ms) and with the firmware EKF velocity.
import numpy as np, sys
exec(open(sys.argv[0].replace('camlag.py', 'nn1.py')).read().split("# samples every 5 ticks")[0])
dt = np.median(np.diff(t)); m = t >= 124
def best(a, b, name):   # lag L > 0: b trails a by L s (b(t+L) ~ a(t))
    out = []
    for L in np.arange(-0.6, 0.61, 0.02):
        n = int(round(L / dt)); x = a[m]; y = np.roll(b, -n)[m]; out.append((np.corrcoef(x, y)[0, 1], L))
    c, L = max(out, key=lambda q: abs(q[0])); c0 = [q for q in out if abs(q[1]) < 1e-9][0][0]
    print(f'{name:34s} best lag {L:+.2f} s corr {c:+.3f} (corr at 0: {c0:+.3f})')
print(f'dt {dt*1000:.1f} ms, samples {m.sum()}')
for j, ax in ((0, 'x'), (1, 'y')):
    v = vb.real if j == 0 else vb.imag
    best(v, ch['ano_of.of2_dx_fix' if j == 0 else 'ano_of.of2_dy_fix'], f'cam vel {ax} -> of2 fix {ax}')
    best(v, ch['ano_of.of2_dy_fix' if j == 0 else 'ano_of.of2_dx_fix'], f'cam vel {ax} -> of2 fix {"y" if j == 0 else "x"}')
    best(v, fw.real if j == 0 else fw.imag, f'cam vel {ax} -> fw EKF vel {ax}')
