"""Item D (docs/analysis/adaptive-arch-study-2026-10-05.md): 6-layer MRAC = roll/pitch MRAC_SatAware plus
normalised adaptive layers on the outer loops it leaves to PID: x, y velocity (earth frame, acceleration add-on
before the yaw rotation), z velocity (throttle add-on) and yaw rate (U_yaw add-on).

Every layer is the same law in dimensionless units, so one knob (gamma_o, 1/s) drives all four:
  reference model  v_ref' = (v_cmd - v_ref) / tau       tau from the PID loop it wraps (no new knob)
  error            e = (v - v_ref) / v_scale             v_scale = the loop's command limit
  update           th' = gamma_o * phi * e / (1 + phi'phi),  |th_i| <= th_max (projection by clipping)
  add-on           u_ad = -u_scale * th'phi              u_scale turns 1.0 into one g (xy, z) or the yaw PID range
phi (all terms divided by v_scale): x, y [1, v_i, v_i|v_i|] (MRAC6_Dec) or [1, v_i, v_j, v_i|v_i|] (MRAC6_Cpl,
the x/y coupling term); z [1, vz]; yaw [1, r].  th_max: tan(TILT_MAX) g for xy, 0.3 g for z, 0.3 of the yaw PID
range.  Frozen bench files are not edited.
"""
import numpy as np
from fwpid import VXY_MAX, TILT_MAX, YAWRATE_MAX, LOCXS
from sim_coupled import RATE_Y, POS_Z, RATE_Z
from plant import PWM_MIN
import tierA

G_CM = 981.0
DT_XY, DT_IN = 0.01, 0.005          # xy_loop runs every 2nd 5 ms control step
TAU_YAW = 0.5                       # measured yaw rate bandwidth ~2 rad/s (doc sec B)


class _Layer:
    def __init__(self, B, n, tau, dt, th_max):
        self.th = np.zeros((B, n)); self.ref = None
        self.a, self.dt, self.th_max = dt / np.maximum(tau, dt), dt, th_max

    def step(self, cmd, v, phi, gamma):
        self.ref = v.copy() if self.ref is None else self.ref + self.a * (cmd - self.ref)
        e = v - self.ref
        g = gamma * self.dt / (1.0 + np.sum(phi * phi, 1))
        self.th = np.clip(self.th + (g * e)[:, None] * phi, -self.th_max, self.th_max)
        return np.sum(self.th * phi, 1)


class MRAC6_Dec(tierA.MRAC_SatAware):
    name = 'mrac6_dec'
    COUPLED = False
    PARAMS = dict(tierA.MRAC_SatAware.PARAMS)
    PARAMS['gamma_o'] = (0.5, 0.01, 10.0, 'log')

    def __init__(self, B, params=None):
        super().__init__(B, params)
        p = self.p
        # velocity loop a = Kp e + Kd de: pole Kp / (1 + Kd DT) per the firmware PID (D on the per-sample difference)
        kp, kd = LOCXS['Kp'] * p['vel_kp'], LOCXS['Kd'] * p['vel_kd']
        tau_v = (1.0 + kd * DT_XY) / kp
        # throttle: thrust ~ x^2 above PWM_MIN, so one g of vertical accel = x_h / 2 PWM at hover
        self.x_h = p['thr_base'] - PWM_MIN
        kz = RATE_Z['Kp'] * p['zr_kp'] * 2.0 * 9.81 / self.x_h
        n = 4 if self.COUPLED else 3
        self.lxy = [_Layer(B, n, tau_v, DT_XY, np.tan(np.deg2rad(TILT_MAX))) for _ in range(2)]
        self.lz = _Layer(B, 2, 1.0 / kz, DT_IN, 0.3)
        self.ly = _Layer(B, 2, TAU_YAW, DT_IN, 0.3)

    def xy_loop(self, o):
        p = self.p; r = o['ref']; go = p['gamma_o']
        e = (r['p'][:, :2] - o['pos'][:, :2]) * 100.0
        v = o['vel'][:, :2] * 100.0 / VXY_MAX
        a = np.zeros((self.B, 2))
        for i in range(2):
            vd = np.clip(self.locx[i].step(e[:, i]), -VXY_MAX, VXY_MAX) + p['ff_v'] * r['v'][:, i] * 100.0
            a[:, i] = self.locxs[i].step(vd - o['vel'][:, i] * 100.0) + p['ff_a'] * r['a'][:, i] * 100.0
            vi, vj = v[:, i], v[:, 1 - i]
            cols = [np.ones(self.B), vi, vj, vi * np.abs(vi)] if self.COUPLED else [np.ones(self.B), vi, vi * np.abs(vi)]
            a[:, i] -= G_CM * self.lxy[i].step(vd / VXY_MAX, vi, np.stack(cols, 1), go)
        ps = np.deg2rad(o['rpy'][:, 2]); c, s = np.cos(ps), np.sin(ps)
        af, al = c * a[:, 0] + s * a[:, 1], -s * a[:, 0] + c * a[:, 1]
        return np.clip(np.rad2deg(np.stack([np.arctan(-al / 981.0), np.arctan(af / 981.0)], 1)), -TILT_MAX, TILT_MAX)

    def z_rate(self, o, vz_des):
        vs = POS_Z['Umax']
        vz = o['vel'][:, 2] / vs
        ad = self.lz.step(vz_des / vs, vz, np.stack([np.ones(self.B), vz], 1), self.p['gamma_o'])
        return super().z_rate(o, vz_des) - 0.5 * self.x_h * ad

    def rate_loop(self, o, wd):
        U = super().rate_loop(o, wd)
        r = o['gyro'][:, 2] / YAWRATE_MAX
        ad = self.ly.step(wd[:, 2] / YAWRATE_MAX, r, np.stack([np.ones(self.B), r], 1), self.p['gamma_o'])
        U[:, 2] -= RATE_Y['Umax'] * ad
        return U


class MRAC6_Cpl(MRAC6_Dec):
    name = 'mrac6_cpl'
    COUPLED = True


class MRAC5_XYZ(MRAC6_Dec):
    """x, y, z layers only. The yaw layer is dropped: alone it raised the tune J from 0.1012 to 0.2611 at gamma_o 0.03
    (0.1743 with its sign flipped), while x, y + z at gamma_o 0.3 gave 0.0910 (doc sec D ablation)."""
    name = 'mrac5_xyz'
    PARAMS = dict(MRAC6_Dec.PARAMS)
    PARAMS['gamma_o'] = (0.3, 0.01, 10.0, 'log')
    rate_loop = tierA.MRAC_SatAware.rate_loop
