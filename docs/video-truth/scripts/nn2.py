# NN2: residual learning on R3. Target = camera body vel - firmware EKF body vel. Also a lever-arm (omega x r) fit.
import numpy as np, sys
exec(open(__file__.replace('nn2.py', 'nn1.py')).read().split("pred = {k:")[0])
def lowpass(e, n=200):
    k = np.ones(n) / n; return np.column_stack([np.convolve(e[:, j], k, 'valid') for j in (0, 1)])
E = Y - FW                                                         # EKF velocity error, body m/s
gyr = blk[:, -10:, 3:6].mean(1)                                    # 0.1 s mean gyro rad/s (x, y, z)
# lever arm: flow sees v + w x r -> EKF err = -(w x r): ex = -(wy rz - wz ry), ey = -(wz rx - wx rz)
A = np.c_[gyr, np.ones(len(gyr))]
for j, nm in ((0, 'x'), (1, 'y')):
    w, *_ = np.linalg.lstsq(A, E[:, j], rcond=None); r2 = 1 - ((E[:, j] - A @ w) ** 2).mean() / E[:, j].var()
    print(f'err_{nm} ~ gx {w[0]*100:+.2f} gy {w[1]*100:+.2f} gz {w[2]*100:+.2f} cm per rad + c {w[3]*100:+.2f} cm/s  R2 {r2:.3f}')
print(f'implied lever arm (in-sample): ry = {100*np.linalg.lstsq(A, E[:,0], rcond=None)[0][2]:+.1f} cm, rx = {-100*np.linalg.lstsq(A, E[:,1], rcond=None)[0][2]:+.1f} cm; |gz| p95 {np.percentile(abs(gyr[:,2]),95):.2f} rad/s')
# residual features: IMU/OF window + EKF vel window + slow accel (2 s not available, use 1 s mean)
fwb = np.stack([np.interp(t, T['s_ekf_of.x[1]|t'], T['s_ekf_of.x[1]']), np.interp(t, T['s_ekf_of.x[4]|t'], T['s_ekf_of.x[4]'])], 1)
fwF = fwb[idx[:, None] - 100 + np.arange(100)[None, :]].reshape(len(idx), 10, 10, 2).mean(2).reshape(len(idx), -1)
F2 = np.c_[F, fwF, blk[:, :, :2].mean(1)]
pr = {k: np.zeros_like(Y) for k in ('R lever ridge', 'R ridge', 'R NN')}
for tr, te in folds():
    pr['R lever ridge'][te] = np.column_stack([ridge(A[tr, :3], E[tr, j], A[te, :3], 1.0) for j in (0, 1)])
    pr['R ridge'][te] = np.column_stack([ridge(F2[tr], E[tr, j], F2[te], 1000.0) for j in (0, 1)])
    pr['R NN'][te] = np.mean([mlp(F2[tr], E[tr], F2[te], seed=s, wd=3e-3)[0] for s in range(3)], 0)
print(f"{'model':14s} {'10s-mean err x':>14s} {'y':>5s}  rmse x    y (cm/s, OOF)")
for k, P_ in (('B0 fw EKF', FW), *[(k, FW + v) for k, v in pr.items()]):
    e = P_ - Y; lp = lowpass(e); print(f'{k:14s} {100*np.sqrt((lp[:,0]**2).mean()):14.2f} {100*np.sqrt((lp[:,1]**2).mean()):5.2f}  {100*np.sqrt((e[:,0]**2).mean()):5.1f} {100*np.sqrt((e[:,1]**2).mean()):5.1f}')
np.savez(OUT, ts=ts, Y=Y, FW=FW, gyr=gyr, **{k.replace(' ', '_'): v for k, v in pr.items()})
