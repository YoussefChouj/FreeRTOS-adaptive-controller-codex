import numpy as np, sys
exec(open('nn1.py').read().split("pred = {k:")[0])
E = Y - FW; G = X[:, 3:6]                                         # 100 Hz gyro rad/s
for L in (-30, -20, -10, -5, 0, 5, 10, 20, 30):                   # gyro at tick idx-L (L>0: gyro earlier than err)
    g = np.stack([G[np.clip(idx - L - k, 0, len(G) - 1)] for k in range(10)], 0).mean(0); A = np.c_[g, np.ones(len(g))]
    out = []
    for j in (0, 1):
        w, *_ = np.linalg.lstsq(A, E[:, j], rcond=None); out.append((w, 1 - ((E[:, j] - A @ w) ** 2).mean() / E[:, j].var()))
    print(f'lag {L*10:+4d} ms  ex: gy {100*out[0][0][1]:+6.1f} gz {100*out[0][0][2]:+5.1f} R2 {out[0][1]:.3f} | ey: gx {100*out[1][0][0]:+6.1f} gz {100*out[1][0][2]:+5.1f} R2 {out[1][1]:.3f}')
