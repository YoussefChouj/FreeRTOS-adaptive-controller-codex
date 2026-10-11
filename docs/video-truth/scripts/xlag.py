# Direct OF-IMU lag: cross-correlate raw flow of1 with gyro x/y (rotation term), high-passed, uniform 100 Hz grid.
import numpy as np, sys
O = sys.argv[1]; T = np.load(O + 'tel_r3.npz', allow_pickle=True)
g = np.arange(75.0, 235.0, 0.01)
def ch(k):
    tk = T['slot1.' + k + '|t']; return np.interp(g, tk, T['slot1.' + k].astype(float))
def hp(x, n=50): return x - np.convolve(x, np.ones(n) / n, 'same')
L = np.arange(-15, 16)
for f in ('ano_of.of1_dx', 'ano_of.of1_dy', 'ano_of.of2_dx_fix', 'ano_of.of2_dy_fix'):
    a = hp(ch(f)); out = []
    for gk in ('Gyro_X_Real', 'Gyro_Y_Real'):
        b = hp(ch(gk)); r = [np.corrcoef(a[30:-30], np.roll(b, l)[30:-30])[0, 1] for l in L]
        k = int(np.argmax(np.abs(r))); out.append(f'{gk[5]}: peak r {r[k]:+.2f} at {10*L[k]:+4d} ms (r@0 {r[15]:+.2f})')
    print(f'{f:18s}', ' | '.join(out))
print('positive lag = gyro shifted later = flow lags gyro')
