import re, sys, numpy as np
h = open("API/xy_ctrl_gains.h").read()
def arr(name):
    m = re.search(name + r"\[[^=]*=\s*\{(.*?)\};", h, re.S)
    return np.array([[float(v) for v in re.findall(r"-?[\d.e+-]+(?=f)", row)] for row in re.findall(r"\{([^{}]*)\}", m.group(1))] or [[float(v) for v in re.findall(r"-?[\d.e+-]+(?=f)", m.group(1))]])
def sc(name): return float(re.search(r"#define %s\s+([-\d.e+]+)" % name, h).group(1))
PHI, GAM, K = arr("XYC_PHI"), arr("XYC_GAM"), arr("XYC_LQR_K")[0]
H, F = arr("XYC_MPC_H"), arr("XYC_MPC_F")
D, AL, KK, DR, AMAX = int(sc("XYC_D")), sc("XYC_AL"), sc("XYC_K"), sc("XYC_DRAG"), sc("XYC_AMAX")
RD, STEP, N = sc("XYC_MPC_RD"), sc("XYC_MPC_STEP"), int(sc("XYC_MPC_N"))
def run(kind, dtrue, scale=1.0, T=12.0, noise=0.0, seed=0):
    rng = np.random.default_rng(seed); n = int(T / 0.01)
    pipe = np.zeros(D); head = 0; a_est = 0; z = 0; plan = np.zeros(N); uprev = 0
    plant = np.zeros(max(dtrue, 1)); ph = 0; a = v = p = 0.0; us = []
    for i in range(n):
        des = 40.0 if i * 0.01 > 1 else 0.0
        pm, vm = p, v + noise * rng.standard_normal()
        e = pm - des
        if abs(e) < 10: z = np.clip(z + 0.01 * e, -300, 300)
        x0 = np.array([pm, vm, a_est]); order = [(head + j) % D for j in range(D)]
        xp = PHI @ x0 + GAM @ pipe[order]
        x = np.array([xp[0] - des, xp[1], xp[2], z]) * scale
        if kind == "lqr": u = -(K @ x)
        else:
            lin = F @ x; lin[0] -= RD * uprev; uu = yy = plan.copy(); t = 1.0
            for _ in range(30):
                tn = .5 * (1 + np.sqrt(1 + 4 * t * t)); g = lin + H @ yy
                un = np.clip(yy - STEP * g, -AMAX, AMAX); yy = un + (t - 1) / tn * (un - uu); uu = un; t = tn
            plan = uu; u = uu[0]
        u = float(np.clip(u, -AMAX, AMAX)); us.append(u)
        a_est = AL * a_est + (1 - AL) * pipe[head]; pipe[head] = u; head = (head + 1) % D; uprev = u
        ua = plant[ph] if dtrue else u; plant[ph] = u; ph = (ph + 1) % len(plant)
        a = AL * a + (1 - AL) * ua; v += 0.01 * (KK * a - DR * v); p += 0.01 * v
    us = np.array(us[int(4 / .01):]); us -= us.mean()
    f = np.fft.rfftfreq(len(us), .01); P = np.abs(np.fft.rfft(us)) ** 2
    band = P[(f >= 1) & (f <= 4)].sum() / max(P[f > 0.05].sum(), 1e-9)
    return round(us.std(), 1), round(f[1:][P[1:].argmax()], 2), round(band, 2)
for kind in ["lqr", "mpc"]:
    for s in [1.0, 0.6]:
        print(kind, "scale", s, "| dtrue:(rms u, peak Hz, 1-4Hz frac)",
              {d: run(kind, d, s) for d in [5, 15, 25, 33, 41]})
