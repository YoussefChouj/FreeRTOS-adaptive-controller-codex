# NN1: learned body-velocity from IMU + OF on R3 (camera truth). Blocked 5-fold CV, out-of-fold predictions.
# Models: B0 firmware EKF vel, B1 rotor-drag linear (LPF acc -> vel), B2 ridge on 1 s window, NN numpy MLP (Gaussian NLL).
# Velocities in EKF body units (m/s, the units of s_ekf_of.x[1]/x[4]); camera -> EKF earth: c/rot/1.094, earth -> body: *exp(i*yaw).
import numpy as np, sys
from scipy.signal import savgol_filter
O = sys.argv[1]; OUT = sys.argv[2]
T = np.load(O + 'tel_r3.npz', allow_pickle=True); C = np.load(O + 'cam_r3.npz'); rot = complex(np.load(O + 'rot.npy'))
t = T['slot1.ano_of.of2_dx_fix|t']; t = t[(t > 70.0) & (t < 236.0)]
def hold(k):
    tk = T[k + '|t']; i = np.clip(np.searchsorted(tk, t, side='right') - 1, 0, len(tk) - 1); return T[k][i].astype(float)
ch = {k: hold('slot1.' + k) for k in ('Acc_X_Real', 'Acc_Y_Real', 'Acc_Z_Real', 'Gyro_X_Real', 'Gyro_Y_Real', 'Gyro_Z_Real',
                                     'imu_data.pit', 'imu_data.rol', 'ano_of.of1_dx', 'ano_of.of1_dy', 'ano_of.of2_dx_fix', 'ano_of.of2_dy_fix')}
ch['alt'] = hold('slot2.ano_of.of_alt_cm')
yaw = -np.radians(hold('slot1.imu_data.yaw'))
# camera truth velocity (30 Hz, savgol derivative over ~0.5 s), mapped to EKF body frame
tt = C['tt']; cz = C['xy'][:, 0] + 1j * C['xy'][:, 1]; dtc = np.median(np.diff(tt))
cv = savgol_filter(cz.real, 15, 2, deriv=1, delta=dtc) + 1j * savgol_filter(cz.imag, 15, 2, deriv=1, delta=dtc)
ve = (np.interp(t, tt, cv.real) + 1j * np.interp(t, tt, cv.imag)) / rot / 1.094
vb = ve * np.exp(1j * yaw)
fw = np.interp(t, T['s_ekf_of.x[1]|t'], T['s_ekf_of.x[1]']) + 1j * np.interp(t, T['s_ekf_of.x[4]|t'], T['s_ekf_of.x[4]'])
# samples every 5 ticks (20 Hz) with a full 1 s causal window
names = list(ch); X = np.stack([ch[k] for k in names], 1)          # (N, 13)
idx = np.arange(100, len(t), 5)
blk = X[idx[:, None] - 100 + np.arange(100)[None, :]]                # (S, 100, 13)
F = blk.reshape(len(idx), 10, 10, -1).mean(2).reshape(len(idx), -1)  # 10 block means x 13 ch = 130
ts = t[idx]; Y = np.column_stack([vb[idx].real, vb[idx].imag]); FW = np.column_stack([fw[idx].real, fw[idx].imag])
accl = blk[:, -50:, :2].mean(1)                                      # 0.5 s mean body acc (mg) for B1
# blocked folds with a 2 s purge gap
K = 5; edges = np.linspace(ts[0], ts[-1] + 1e-6, K + 1)
def folds():
    for k in range(K):
        te = (ts >= edges[k]) & (ts < edges[k + 1]); tr = (ts < edges[k] - 2) | (ts >= edges[k + 1] + 2); yield tr, te
def ridge(Xtr, ytr, Xte, lam):
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9; A = (Xtr - mu) / sd; B = (Xte - mu) / sd
    A1 = np.c_[A, np.ones(len(A))]; W = np.linalg.solve(A1.T @ A1 + lam * np.diag(np.r_[np.ones(A.shape[1]), 0]), A1.T @ ytr)
    return np.c_[B, np.ones(len(B))] @ W
def mlp(Xtr, ytr, Xte, H=32, wd=1e-3, ep=400, lr=3e-3, seed=0):
    rng = np.random.default_rng(seed); mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9; ym, ys = ytr.mean(0), ytr.std(0)
    A = (Xtr - mu) / sd; Yn = (ytr - ym) / ys; nv = max(1, len(A) // 8); va = np.arange(len(A) - nv, len(A)); tr = np.arange(len(A) - nv)
    D = A.shape[1]; P = [rng.normal(0, 1 / np.sqrt(D), (D, H)), np.zeros(H), rng.normal(0, 1 / np.sqrt(H), (H, H)), np.zeros(H),
                         rng.normal(0, 0.1 / np.sqrt(H), (H, 4)), np.zeros(4)]
    M = [np.zeros_like(p) for p in P]; V = [np.zeros_like(p) for p in P]; best = (1e9, None); step = 0
    def fwd(Z, P):
        h1 = np.tanh(Z @ P[0] + P[1]); h2 = np.tanh(h1 @ P[2] + P[3]); o = h2 @ P[4] + P[5]; return h1, h2, o
    def nll(o, y):
        mu_, lv = o[:, :2], np.clip(o[:, 2:], -6, 4); return np.mean(0.5 * (lv + (y - mu_) ** 2 * np.exp(-lv)))
    for e in range(ep):
        for b in np.array_split(rng.permutation(tr), max(1, len(tr) // 64)):
            Z, y = A[b], Yn[b]; h1, h2, o = fwd(Z, P); mu_, lv = o[:, :2], np.clip(o[:, 2:], -6, 4); iv = np.exp(-lv); n = len(b)
            go = np.c_[-(y - mu_) * iv, 0.5 * (1 - (y - mu_) ** 2 * iv)] / n
            if e < 50: go[:, 2:] = 0; go[:, :2] = -(y - mu_) / n          # warm-up: plain MSE on the mean head
            g4 = h2.T @ go; g5 = go.sum(0); d2 = (go @ P[4].T) * (1 - h2 ** 2); g2 = h1.T @ d2; g3 = d2.sum(0)
            d1 = (d2 @ P[2].T) * (1 - h1 ** 2); g0 = Z.T @ d1; g1 = d1.sum(0); G = [g0, g1, g2, g3, g4, g5]; step += 1
            for i in range(6):
                G[i] = G[i] + wd * P[i] * (i % 2 == 0); M[i] = 0.9 * M[i] + 0.1 * G[i]; V[i] = 0.999 * V[i] + 0.001 * G[i] ** 2
                P[i] = P[i] - lr * (M[i] / (1 - 0.9 ** step)) / (np.sqrt(V[i] / (1 - 0.999 ** step)) + 1e-8)
        if e >= 50:
            lv_ = nll(fwd(A[va], P)[2], Yn[va])
            if lv_ < best[0]: best = (lv_, [p.copy() for p in P])
    P = best[1] or P; o = fwd((Xte - mu) / sd, P)[2]
    return o[:, :2] * ys + ym, np.exp(0.5 * np.clip(o[:, 2:], -6, 4)) * ys
pred = {k: np.zeros_like(Y) for k in ('B1 drag', 'B2 ridge', 'NN mlp')}; sig = np.zeros_like(Y)
for tr, te in folds():
    pred['B1 drag'][te] = np.column_stack([ridge(accl[tr], Y[tr, j], accl[te], 1.0) for j in (0, 1)])
    pred['B2 ridge'][te] = np.column_stack([ridge(F[tr], Y[tr, j], F[te], 300.0) for j in (0, 1)])
    ps = [mlp(F[tr], Y[tr], F[te], seed=s) for s in range(3)]
    pred['NN mlp'][te] = np.mean([p[0] for p in ps], 0); sig[te] = np.mean([p[1] for p in ps], 0)
pred['B0 fw EKF'] = FW; pred['zero'] = np.zeros_like(Y)
def lowpass(e, n=200):  # 10 s moving mean of the error (20 Hz samples): the part that integrates into drift
    k = np.ones(n) / n; return np.column_stack([np.convolve(e[:, j], k, 'valid') for j in (0, 1)])
print(f'truth vel sd cm/s x {100*Y[:,0].std():.1f} y {100*Y[:,1].std():.1f}; samples {len(Y)}')
print(f"{'model':12s} {'rmse x':>7s} {'rmse y':>7s} {'corr x':>6s} {'corr y':>6s} {'10s-mean err x':>15s} {'y':>5s}  (cm/s, out-of-fold)")
for k in ('zero', 'B0 fw EKF', 'B1 drag', 'B2 ridge', 'NN mlp'):
    e = pred[k] - Y; lp = lowpass(e)
    cx = np.corrcoef(pred[k][:, 0], Y[:, 0])[0, 1] if pred[k][:, 0].std() > 0 else 0; cy = np.corrcoef(pred[k][:, 1], Y[:, 1])[0, 1] if pred[k][:, 1].std() > 0 else 0
    print(f'{k:12s} {100*np.sqrt((e[:,0]**2).mean()):7.1f} {100*np.sqrt((e[:,1]**2).mean()):7.1f} {cx:6.2f} {cy:6.2f} {100*np.sqrt((lp[:,0]**2).mean()):15.2f} {100*np.sqrt((lp[:,1]**2).mean()):5.2f}')
z = (pred['NN mlp'] - Y) / sig; print(f'NN calibration: std of z x {z[:,0].std():.2f} y {z[:,1].std():.2f} (1.0 = honest sigma); median sigma cm/s {100*np.median(sig):.1f}')
np.savez(OUT, ts=ts, Y=Y, sig=sig, **{k.replace(' ', '_'): v for k, v in pred.items()})
