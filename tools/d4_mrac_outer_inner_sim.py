"""D4: xy MRAC in the inner (velocity) loop, the outer (position) loop, or both, around the PID cascade.

Plant, delay, noise, PID gains and the inner MRAC are the D3 sim (tools/d3_mrac_wrap_sim.py). Both layers use the
noise-free twin as reference model (the nominal closed loop on the header plant), the reference D3 found consistent.
  inner  phi=[1, v, v tanh v, u_nom] (SI), u_ad = -W.phi added to the acceleration command, clip 1 m/s^2
  outer  phi=[1, p, vd] (SI), v_ad = -W.phi added to the velocity demand, clip 0.3 m/s; e = measured p - twin p
The outer layer is a design study: the firmware has no position-layer xy MRAC (API/mrac.c adapts velocity only).
Each law: W' = g phi e - 0.1 g |e| W, |W_i| <= 2. 'both' runs the outer law at g_o = g_i or g_i / 5 (time-scale split).
Each case runs 3 noise seeds; the table gives mean and spread (max - min) of the rms position error.
Run from the repo root: PYTHONPATH=. python tools/d4_mrac_outer_inner_sim.py (about 3 min).
Written for docs/research/2026-10-22-d4-outer-inner.md.
"""
import numpy as np
from tools.d3_mrac_wrap_sim import DT, Loop, sine, step, wind


def law(W, phi, e, g):
    return np.clip(W + DT * g * (phi * e - 0.1 * abs(e) * W), -2, 2)


def run(g_in, g_out, path, k_scale=1.0, dist=None, seed=1, t_end=30.0):
    real = Loop("pid", k_scale, dist, True, seed)
    twin = Loop("pid", 1.0, None, False, seed)
    Wi, Wo, v_ad = np.zeros(4), np.zeros(3), 0.0
    E, U, UI, UO = [], [], [], []
    for i in range(int(t_end / DT)):
        t = i * DT
        des, vff = path(t)
        tu, _, _ = twin.nominal(t, des, vff)
        twin.step(t, tu, 0.0)
        u_nom, vd, vm = real.nominal(t, des, vff, 100 * v_ad)
        u_ad = 0.0
        if g_in:
            x = vm / 100
            phi = np.array([1.0, x, x * np.tanh(x), u_nom / 100])
            u_ad = float(np.clip(-Wi @ phi, -1.0, 1.0))
            Wi = law(Wi, phi, x - twin.v / 100, g_in)
        if g_out:                                   # v_ad computed now, applied on the next tick's velocity demand
            phi_o = np.array([1.0, real.pm / 100, vd / 100])
            v_ad = float(np.clip(-Wo @ phi_o, -0.3, 0.3))
            Wo = law(Wo, phi_o, (real.pm - twin.p) / 100, g_out)
        u = real.step(t, u_nom, 100 * u_ad)
        if t >= 3.0:
            E.append(real.p - des), U.append(u), UI.append(u_ad), UO.append(v_ad)
    rms = lambda z: float(np.sqrt(np.mean(np.square(z))))
    return rms(E), float(np.max(np.abs(E))), rms(U), rms(UI), rms(UO)


if __name__ == "__main__":
    cases = [("none", 0, 0), ("inner", 1, 0), ("inner", 5, 0), ("outer", 0, 1), ("outer", 0, 5),
             ("both", 1, 1), ("both", 5, 5), ("both", 5, 1), ("both", 1, 0.2)]
    print("layer  g_i g_o | scenario        | rms err (mean, spread)  max err | rms cmd | rms u_ad  rms v_ad")
    for scen, path, ks, dist in (("step+wind", step, 1.0, wind), ("step+wind K.85", step, 0.85, wind),
                                 ("sine.25Hz+wind", sine, 1.0, wind)):
        for name, gi, go in cases:
            R = np.array([run(gi, go, path, ks, dist, seed) for seed in (1, 2, 3)])
            m = R.mean(axis=0)
            print(f"{name:5} {gi:3g} {go:4g} | {scen:15} | {m[0]:6.2f} ({np.ptp(R[:, 0]):4.2f})          {m[1]:6.1f}"
                  f"  | {m[2]:6.1f}  | {m[3]:7.3f}  {m[4]:7.3f}", flush=True)
