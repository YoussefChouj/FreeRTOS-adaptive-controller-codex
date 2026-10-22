"""D3: what xy MRAC does when it wraps a nominal controller other than the PID it was designed around.

One axis, 100 Hz, plant and ADRC from API/xy_ctrl_gains.h (port of test_xy_ctrl_adrc_noise.hold_step), loop delay
21 ticks, 0.9 cm position noise, 2 cm/s velocity noise. PID cascade gains from API/pid.c:36/38 (per-tick D, like
ComputePID). MRAC = the velocity layer of API/mrac.c reduced to its core: phi=[1, v, v tanh v, u_nom] (SI),
u_ad = -W.phi clipped to 1 m/s^2, W' = g phi e - g sig |e| W, |W_i| <= 2. Gamma g is swept (firmware value not read).
Reference cases:
  fw      xm' = 2 (r - xm), r = PID position-loop velocity demand + vff   (what the firmware does for every kind)
  twin    xm = velocity of a noise-free twin: the same nominal controller on the header plant (nominal closed loop)
ESO input (ADRC): 'final' = u_nom + u_ad (firmware XyCtrl_Commit), 'nom' = u_nom only.
Run from the repo root: PYTHONPATH=. python tools/d3_mrac_wrap_sim.py (about 2 min).
Written for docs/research/2026-10-22-d3-mrac-wrap.md.
"""
import numpy as np
from ground_station.analysis.tests.test_xy_ctrl_adrc_noise import HEADER, gains

G = gains(HEADER.read_text())
DT, DELAY, AMAX = 0.01, 21, G["AMAX"]
PX, PV = (0.3214, 0.0001, 6.2937), (3.2242, 0.0157, 4.7283)


class Pid:
    def __init__(s, k, lim):
        s.k, s.lim, s.sum, s.pre = k, lim, 0.0, None

    def __call__(s, e):
        s.pre = e if s.pre is None else s.pre
        s.sum = np.clip(s.sum + e, -3000, 3000)
        u = s.k[0] * e + s.k[1] * s.sum + s.k[2] * (e - s.pre)
        s.pre = e
        return float(np.clip(u, -s.lim, s.lim))


class Adrc:
    def __init__(s):
        s.z1, s.z2, s.z3 = None, 0.0, 0.0

    def __call__(s, des, pm, u_eso_delayed):
        wc, b0, b1, b2, b3 = (G[n] for n in ("ADRC_WC", "ADRC_B0", "ADRC_B1", "ADRC_B2", "ADRC_B3"))
        s.z1 = pm if s.z1 is None else s.z1
        e = s.z1 - pm
        s.z1 += DT * (s.z2 - b1 * e)
        s.z2 += DT * (s.z3 - b2 * e + b0 * u_eso_delayed)
        s.z3 += DT * (-b3 * e)
        return float(np.clip((wc * wc * (des - s.z1) - 2.0 * wc * s.z2 - s.z3) / b0, -AMAX, AMAX))


class Loop:
    """Nominal controller + plant (+ delay). Returns u_nom; plant advanced by step()."""

    def __init__(s, kind, k_scale=1.0, dist=None, noise=True, seed=1, eso="final"):
        s.kind, s.ks, s.dist, s.eso = kind, k_scale, dist or (lambda t: 0.0), eso
        s.rng = np.random.default_rng(seed) if noise else None
        s.px, s.pv, s.adrc = Pid(PX, 300), Pid(PV, 600), Adrc()
        s.pipe_eso, s.pipe_plant, s.head = np.zeros(DELAY), np.zeros(DELAY), 0
        s.a = s.v = s.p = 0.0

    def nominal(s, t, des, vff):
        n = (lambda sd: sd * s.rng.standard_normal()) if s.rng else (lambda sd: 0.0)
        pm, vm = s.p + n(0.9), s.v + n(2.0)
        vd = s.px(des - pm) + vff                      # PID position loop: computed for every kind (locxsPID.Des)
        if s.kind == "pid":
            u = float(np.clip(s.pv(vd - vm), -AMAX, AMAX))
        else:
            u = s.adrc(des, pm, s.pipe_eso[s.head])
        return u, vd, vm

    def step(s, t, u_nom, u_ad_cm):
        u = float(np.clip(u_nom + u_ad_cm, -AMAX, AMAX))
        s.pipe_eso[s.head] = u if s.eso == "final" else u_nom
        ua, s.pipe_plant[s.head] = s.pipe_plant[s.head], u
        s.head = (s.head + 1) % DELAY
        s.a = G["AL"] * s.a + (1 - G["AL"]) * ua
        s.v += DT * (G["K"] * s.ks * s.a + s.dist(t) - G["DRAG"] * s.v)
        s.p += DT * s.v
        return u


def run(kind, ref, gam, path, k_scale=1.0, dist=None, eso="final", t_end=30.0):
    real = Loop(kind, k_scale, dist, True, 1, eso)
    twin = Loop(kind, 1.0, None, False, 1, eso)
    W, xm = np.zeros(4), 0.0
    E, U, UAD, Z3 = [], [], [], []
    for i in range(int(t_end / DT)):
        t = i * DT
        des, vff = path(t)
        u_nom, vd, vm = real.nominal(t, des, vff)
        if ref == "twin":
            tu, _, _ = twin.nominal(t, des, vff)
            twin.step(t, tu, 0.0)
            xm = twin.v / 100
        elif ref == "fw":
            xm += DT * 2.0 * (vd / 100 - xm)
        x = vm / 100
        phi = np.array([1.0, x, x * np.tanh(x), u_nom / 100])
        u_ad = float(np.clip(-W @ phi, -1.0, 1.0)) if ref != "none" else 0.0
        if ref != "none":
            e = x - xm
            W = np.clip(W + DT * gam * (phi * e - 0.1 * abs(e) * W), -2, 2)
        u = real.step(t, u_nom, 100 * u_ad)
        if t >= 3.0:
            E.append(real.p - des), U.append(u), UAD.append(u_ad), Z3.append(real.adrc.z3)
    E, U, UAD, Z3 = map(np.array, (E, U, UAD, Z3))
    rms = lambda z: float(np.sqrt(np.mean(z ** 2)))
    cz = float(np.corrcoef(UAD, -np.array(Z3))[0, 1]) if kind == "adrc" and np.std(UAD) > 1e-9 else float("nan")
    return rms(E), float(np.max(np.abs(E))), rms(U), 100 * float(np.mean(np.abs(U) >= AMAX - 1e-6)), rms(UAD), cz


step = lambda t: (40.0 if t > 1.0 else 0.0, 0.0)
sine = lambda t: (30.0 * np.sin(2 * np.pi * 0.25 * t), 30.0 * 2 * np.pi * 0.25 * np.cos(2 * np.pi * 0.25 * t))
wind = lambda t: 40.0 if t > 10.0 else 0.0           # 40 cm/s^2 gust from 10 s (~2.3 deg of lean)

print("kind ref  eso   gam | scenario        | rms err  max err | rms cmd  sat% | rms u_ad  corr(u_ad,-z3)")
for kind in ("pid", "adrc"):
    for scen, path, ks, dist in (("step+wind", step, 1.0, wind), ("step+wind K.85", step, 0.85, wind),
                                 ("sine.25Hz+wind", sine, 1.0, wind)):
        cases = [("none", 0)] + [(r, g) for r in ("fw", "twin") for g in (1.0, 5.0)]
        for ref, gam in cases:
            for eso in (("final", "nom") if kind == "adrc" and ref != "none" else ("final",)):
                r = run(kind, ref, gam, path, ks, dist, eso)
                print(f"{kind:4} {ref:4} {eso:5} {gam:3.0f} | {scen:15} | {r[0]:7.2f} {r[1]:7.1f}  | {r[2]:7.1f} {r[3]:5.1f}"
                      f" | {r[4]:7.3f}  {r[5]:+.2f}", flush=True)
