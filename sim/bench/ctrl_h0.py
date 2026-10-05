"""Item H step 1 (docs/analysis/adaptive-arch-study-2026-10-05.md sec H): adaptation with NO tuned knob.

The tuned PID (FwPID, any parameter set, e.g. results/pid_tuned2_test.json) stays as it is; the x, y velocity and
z velocity loops get the ctrl_mrac6 normalised layer (same reference model, phi, projection and add-on). The one
knob MRAC6 tunes, gamma_o, is replaced by a rate read off the loop each layer wraps:
  gamma_i = 1 / (SEP * tau_i)    tau_i = that loop's closed-loop time constant (the layer's own reference model)
H0 uses SEP = 1 (adapt as fast as the loop settles); H0_Sep uses SEP = 10, the two-time-scale rule (adaptation a
decade slower than the loop it corrects).  Attitude stays on the PID (no SatAware gamma / mu either).
Every parameter is a FwPID one, so the PID's tuned set is passed unchanged:
  python bench.py eval ctrl_h0:H0 --params <pid_tuned2 params json> --split test --tag h0
"""
import numpy as np
from fwpid import FwPID, VXY_MAX, TILT_MAX, LOCXS
from sim_coupled import POS_Z, RATE_Z
from plant import PWM_MIN
from ctrl_mrac6 import _Layer, G_CM, DT_XY, DT_IN


class H0(FwPID):
    name = 'h0'
    SEP = 1.0

    def __init__(self, B, params=None):
        super().__init__(B, params)
        p = self.p
        kp, kd = LOCXS['Kp'] * p['vel_kp'], LOCXS['Kd'] * p['vel_kd']
        tau_v = (1.0 + kd * DT_XY) / kp
        self.x_h = p['thr_base'] - PWM_MIN
        tau_z = self.x_h / (RATE_Z['Kp'] * p['zr_kp'] * 2.0 * 9.81)
        self.g_xy, self.g_z = 1.0 / (self.SEP * tau_v), 1.0 / (self.SEP * tau_z)
        self.lxy = [_Layer(B, 3, tau_v, DT_XY, np.tan(np.deg2rad(TILT_MAX))) for _ in range(2)]
        self.lz = _Layer(B, 2, tau_z, DT_IN, 0.3)

    def xy_loop(self, o):
        p = self.p; r = o['ref']
        e = (r['p'][:, :2] - o['pos'][:, :2]) * 100.0
        v = o['vel'][:, :2] * 100.0 / VXY_MAX
        a = np.zeros((self.B, 2))
        for i in range(2):
            vd = np.clip(self.locx[i].step(e[:, i]), -VXY_MAX, VXY_MAX) + p['ff_v'] * r['v'][:, i] * 100.0
            a[:, i] = self.locxs[i].step(vd - o['vel'][:, i] * 100.0) + p['ff_a'] * r['a'][:, i] * 100.0
            vi = v[:, i]
            phi = np.stack([np.ones(self.B), vi, vi * np.abs(vi)], 1)
            a[:, i] -= G_CM * self.lxy[i].step(vd / VXY_MAX, vi, phi, self.g_xy)
        ps = np.deg2rad(o['rpy'][:, 2]); c, s = np.cos(ps), np.sin(ps)
        af, al = c * a[:, 0] + s * a[:, 1], -s * a[:, 0] + c * a[:, 1]
        return np.clip(np.rad2deg(np.stack([np.arctan(-al / 981.0), np.arctan(af / 981.0)], 1)), -TILT_MAX, TILT_MAX)

    def z_rate(self, o, vz_des):
        vs = POS_Z['Umax']
        vz = o['vel'][:, 2] / vs
        ad = self.lz.step(vz_des / vs, vz, np.stack([np.ones(self.B), vz], 1), self.g_z)
        return super().z_rate(o, vz_des) - 0.5 * self.x_h * ad


class H0_Sep(H0):
    name = 'h0_sep'
    SEP = 10.0
