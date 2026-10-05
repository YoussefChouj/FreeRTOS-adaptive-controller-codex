"""Item G of the adaptive-arch study (doc sec N): an MRAC derived for (augmenting) the cascaded PID's rate loop.

Two things the existing inner MRAC (ctrl_mrac.MRACBaseController / MRAC_SatAware, used by MRAC5_XYZ) does not do:
  1. Reference model = the PID loop AS TUNED.  ctrl_mrac.RefModel builds its PIDs from sim_coupled's default ANG_PR /
     RATE_PR, so once rate_kp / rate_kd / ang_kp are tuned the nominal plant no longer matches its own reference and the
     adaptation fights the PID (the robust tune drove MRAC5_XYZ's inner gamma to its lower bound, doc sec M).  Here the
     reference model's roll / pitch PIDs share the controller's tuned gain dicts.
  2. The adapted parameters are the rate PID's own terms: per roll / pitch axis th = [bias, P, I, D] and
       U = U_pid - UN_N th'phi,   phi = [1, Up, Ui, Ud] / UN_N
     so the result is always the firmware PID with gains (1 - th_P, th_I, th_D) x tuned, plus a torque bias: a
     control-effectiveness loss (kt_mismatch, motor_loss) has the exact match th_P = th_I = th_D = 1 - 1/lambda, an
     arm load the bias.  Gains are boxed to [0.5, 2] x tuned (projection by clipping), the bias to 0.3 of the range.
Law, error and leakage are MRAC_SatAware's: s = e_p + LAM e_phi against the reference model, th' = gamma_g phi s / (1 +
phi'phi) - SIGMA th - mu |dU| th (dU = commanded minus mixer-achieved torque, freezes adaptation in saturation).
gamma_g replaces MRAC5_XYZ's dead inner gamma, so the knob count is unchanged.  x, y, z layers are MRAC5_XYZ's.
Frozen bench files are not edited.
"""
import numpy as np
import ctrl_mrac6 as m6
from ctrl_nn2 import _rows
from sim_coupled import LAM, SIGMA, UN_N, DT_C

TH_LO = np.array([-0.3, -1.0, -1.0, -1.0])   # [bias, P, I, D]: gain 1 - th in [0.5, 2]
TH_HI = np.array([0.3, 0.5, 0.5, 0.5])


class PIDG_XYZ(m6.MRAC5_XYZ):
    name = 'pidg_xyz'
    PARAMS = {k: v for k, v in m6.MRAC5_XYZ.PARAMS.items() if k != 'gamma'}
    PARAMS['gamma_g'] = (1.0, 1e-3, 100.0, 'log')

    def __init__(self, B, params=None):
        super().__init__(B, params)
        r = self.ref   # reference model shares the tuned roll / pitch gains (item 1 above)
        r.ang_r.g, r.ang_p.g = self.ang[0].g, self.ang[1].g
        r.rate_r.g, r.rate_p.g = self.rate[0].g, self.rate[1].g
        self.th = np.zeros((B, 2, 4))
        self.terms = np.zeros((B, 2, 3))

    def rate_loop(self, o, wd):
        pre = [self.rate[i].PreE.copy() for i in range(2)]
        U = super().rate_loop(o, wd)
        e = wd - o['gyro']
        for i in range(2):   # the PID's own clipped terms, as sim_coupled.PID.step forms them
            pid = self.rate[i]; g = pid.g
            self.terms[:, i, 0] = np.clip(g['Kp'] * e[:, i], -g['Upmax'], g['Upmax'])
            self.terms[:, i, 1] = np.clip(g['Ki'] * pid.SumE, -g['Uimax'], g['Uimax'])
            self.terms[:, i, 2] = np.clip(g['Kd'] * (e[:, i] - pre[i]), -g['Udmax'], g['Udmax'])
        return U

    def controller_update(self, o, u_nom, wd):
        ref = self.ref.step(self.des[:, 0], self.des[:, 1], o['ref']['yaw'], o['ref']['p'][:, 2])
        pm = np.deg2rad(np.stack([ref['p'], ref['q']], 1))
        phm = np.deg2rad(np.stack([ref['phi'], ref['theta']], 1))
        s = (np.deg2rad(o['gyro'][:, :2]) - pm) + LAM * (np.deg2rad(o['rpy'][:, :2]) - phm)
        m = o['mot']
        u_act = np.stack([m[:, 1] + m[:, 2] - m[:, 0] - m[:, 3], m[:, 0] + m[:, 2] - m[:, 1] - m[:, 3]], 1) / 4.0
        dU = np.abs(self.U_cmd_prev - u_act)
        phi = np.concatenate([np.ones((self.B, 2, 1)), self.terms / UN_N], 2)
        g = _rows(self.p['gamma_g'], self.B, 1) * (s / (1.0 + np.sum(phi * phi, 2)))
        self.th += DT_C * (g[:, :, None] * phi - (SIGMA + _rows(self.p['mu'], self.B, 1) * dU)[:, :, None] * self.th)
        np.clip(self.th, TH_LO, TH_HI, out=self.th)
        U = u_nom.copy()
        U[:, :2] -= UN_N * np.sum(self.th * phi, 2)
        self.U_cmd_prev[:] = U[:, :2]
        return U
