import numpy as np
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'adaptive_compare'))
from sim_coupled import MRAC as MRACBase, features, P_N, PHI_N, UN_N, ACC_N, DT_C, LAM, SIGMA, TH_MAX, U_SCALE, OMEGA_U

from ctrl_mrac import MRAC_S6, get_params

class MRACSTBase(MRACBase):
    def step(self, pm_meas, phm_meas, U_pid, pmk, phmk, q, r, gamma, eps, phi_max):
        if self.nf == 0:
            return np.zeros_like(pm_meas)
        self.acc_f += DT_C * 2 * np.pi * 10 * ((pm_meas - self.p_prev) / DT_C - self.acc_f)
        Phi = features(self.ctrl, pm_meas / P_N, phm_meas / PHI_N, U_pid / UN_N,
                       np.full_like(pm_meas, pmk / P_N), q * r / 0.2, self.acc_f / ACC_N, self.rbf)
        s = (pm_meas - pmk) + LAM * (phm_meas - phmk)
        den = 1.0 + (Phi * Phi).sum(1)
        
        e_sq = s**2
        phi_e = np.ones_like(s) * phi_max
        mask = e_sq < eps**2
        phi_e[mask] = 1.0 / (eps[mask]**2 - e_sq[mask])
        phi_e = np.minimum(phi_e, phi_max)
        
        y = phi_e[:, None] * gamma[:, None] * Phi * (s / den)[:, None]
        upd = y - SIGMA * self.Th
        self.Th += DT_C * upd
        
        nrm = np.sqrt((self.Th * self.Th).sum(1))
        self.Th *= np.minimum(1.0, TH_MAX / (nrm + 1e-12))[:, None]
        raw = -U_SCALE * (self.Th * Phi).sum(1)
        self.u_ad += DT_C * OMEGA_U * (raw - self.u_ad)
        self.p_prev = pm_meas
        return self.u_ad

class MRAC_ST(MRAC_S6):
    """
    Set-Theoretic MRAC.
    Multiply the adaptation rate by a generalised restricted potential of the tracking error,
    phi(e) = 1 / (eps^2 - ||e||_P^2) for ||e||_P < eps. Source: Arabi/Yucelen.
    """
    name = 'mrac_st'
    PARAMS = get_params(0.31622776601683794)
    PARAMS['eps'] = (10.0, 0.1, 20.0, 'log')
    PARAMS['phi_max'] = (10.0, 1.0, 100.0, 'log')

    def __init__(self, B, params=None):
        super().__init__(B, params)
        self.mrac_r = MRACSTBase(self.ctrl_type, B)
        self.mrac_p = MRACSTBase(self.ctrl_type, B)

    def controller_update(self, o, u_nom, wd):
        cmd_r = self.des[:, 0]
        cmd_p = self.des[:, 1]
        cmd_y = o['ref']['yaw']
        cmd_z = o['ref']['p'][:, 2]

        ref_state = self.ref.step(cmd_r, cmd_p, cmd_y, cmd_z)

        phm_r = np.deg2rad(o['rpy'][:, 0])
        pm_r = np.deg2rad(o['gyro'][:, 0])
        qm = np.deg2rad(o['gyro'][:, 1])
        rm = np.deg2rad(o['gyro'][:, 2])
        phm_p = np.deg2rad(o['rpy'][:, 1])
        pm_p = qm 
        
        p_r_ref = np.deg2rad(ref_state['p'])
        phi_r_ref = np.deg2rad(ref_state['phi'])
        p_p_ref = np.deg2rad(ref_state['q'])
        phi_p_ref = np.deg2rad(ref_state['theta'])
        
        gamma = self.p['gamma'] if isinstance(self.p['gamma'], np.ndarray) else np.full(self.B, self.p['gamma'])
        eps = self.p['eps'] if isinstance(self.p['eps'], np.ndarray) else np.full(self.B, self.p['eps'])
        phi_max = self.p['phi_max'] if isinstance(self.p['phi_max'], np.ndarray) else np.full(self.B, self.p['phi_max'])
        
        u_ad_r = self.mrac_r.step(pm_r, phm_r, u_nom[:, 0], p_r_ref, phi_r_ref, qm, rm, gamma, eps, phi_max)
        u_ad_p = self.mrac_p.step(pm_p, phm_p, u_nom[:, 1], p_p_ref, phi_p_ref, pm_r, rm, gamma, eps, phi_max)
        
        U = u_nom.copy()
        U[:, 0] += u_ad_r
        U[:, 1] += u_ad_p
        return U


def log_bar_deriv(x, x_max, alpha=0.75, smoothing=0.01):
    activation_threshold = alpha * x_max
    out = np.zeros_like(x)
    mask = x > activation_threshold
    rem = x_max[mask] - x[mask] + smoothing
    rem = np.maximum(rem, smoothing)
    out[mask] = 1.0 / rem
    return out

class MRACSTBarrierBase(MRACBase):
    def step(self, pm_meas, phm_meas, U_pid, pmk, phmk, q, r, gamma, eps, phi_max, bar_gain):
        if self.nf == 0:
            return np.zeros_like(pm_meas)
        self.acc_f += DT_C * 2 * np.pi * 10 * ((pm_meas - self.p_prev) / DT_C - self.acc_f)
        Phi = features(self.ctrl, pm_meas / P_N, phm_meas / PHI_N, U_pid / UN_N,
                       np.full_like(pm_meas, pmk / P_N), q * r / 0.2, self.acc_f / ACC_N, self.rbf)
        s = (pm_meas - pmk) + LAM * (phm_meas - phmk)
        den = 1.0 + (Phi * Phi).sum(1)
        
        e_sq = s**2
        phi_e = np.ones_like(s) * phi_max
        mask = e_sq < eps**2
        phi_e[mask] = 1.0 / (eps[mask]**2 - e_sq[mask])
        phi_e = np.minimum(phi_e, phi_max)
        
        angle_error = phm_meas - phmk
        rate_error = pm_meas - pmk
        dphi_dangle = log_bar_deriv(np.abs(angle_error), eps) * np.sign(angle_error)
        dphi_drate = log_bar_deriv(np.abs(rate_error), eps) * np.sign(rate_error)
        s_bar = dphi_drate + LAM * dphi_dangle
        grad_bar = bar_gain[:, None] * Phi * s_bar[:, None]
        
        y = phi_e[:, None] * gamma[:, None] * Phi * (s / den)[:, None]
        upd = y - SIGMA * self.Th + grad_bar
        self.Th += DT_C * upd
        
        nrm = np.sqrt((self.Th * self.Th).sum(1))
        self.Th *= np.minimum(1.0, TH_MAX / (nrm + 1e-12))[:, None]
        raw = -U_SCALE * (self.Th * Phi).sum(1)
        self.u_ad += DT_C * OMEGA_U * (raw - self.u_ad)
        self.p_prev = pm_meas
        return self.u_ad

class MRAC_ST_Barrier(MRAC_S6):
    """
    Set-Theoretic MRAC plus log-barrier gradient modifier.
    Source: nb5 cell 24 for log-barrier gradient modifier.
    """
    name = 'mrac_st_barrier'
    PARAMS = get_params(0.31622776601683794)
    PARAMS['eps'] = (10.0, 0.1, 20.0, 'log')
    PARAMS['phi_max'] = (10.0, 1.0, 100.0, 'log')
    PARAMS['bar_gain'] = (1.0, 0.0, 10.0, 'lin')

    def __init__(self, B, params=None):
        super().__init__(B, params)
        self.mrac_r = MRACSTBarrierBase(self.ctrl_type, B)
        self.mrac_p = MRACSTBarrierBase(self.ctrl_type, B)

    def controller_update(self, o, u_nom, wd):
        cmd_r = self.des[:, 0]
        cmd_p = self.des[:, 1]
        cmd_y = o['ref']['yaw']
        cmd_z = o['ref']['p'][:, 2]

        ref_state = self.ref.step(cmd_r, cmd_p, cmd_y, cmd_z)

        phm_r = np.deg2rad(o['rpy'][:, 0])
        pm_r = np.deg2rad(o['gyro'][:, 0])
        qm = np.deg2rad(o['gyro'][:, 1])
        rm = np.deg2rad(o['gyro'][:, 2])
        phm_p = np.deg2rad(o['rpy'][:, 1])
        pm_p = qm 
        
        p_r_ref = np.deg2rad(ref_state['p'])
        phi_r_ref = np.deg2rad(ref_state['phi'])
        p_p_ref = np.deg2rad(ref_state['q'])
        phi_p_ref = np.deg2rad(ref_state['theta'])
        
        gamma = self.p['gamma'] if isinstance(self.p['gamma'], np.ndarray) else np.full(self.B, self.p['gamma'])
        eps = self.p['eps'] if isinstance(self.p['eps'], np.ndarray) else np.full(self.B, self.p['eps'])
        phi_max = self.p['phi_max'] if isinstance(self.p['phi_max'], np.ndarray) else np.full(self.B, self.p['phi_max'])
        bar_gain = self.p['bar_gain'] if isinstance(self.p['bar_gain'], np.ndarray) else np.full(self.B, self.p['bar_gain'])
        
        u_ad_r = self.mrac_r.step(pm_r, phm_r, u_nom[:, 0], p_r_ref, phi_r_ref, qm, rm, gamma, eps, phi_max, bar_gain)
        u_ad_p = self.mrac_p.step(pm_p, phm_p, u_nom[:, 1], p_p_ref, phi_p_ref, pm_r, rm, gamma, eps, phi_max, bar_gain)
        
        U = u_nom.copy()
        U[:, 0] += u_ad_r
        U[:, 1] += u_ad_p
        return U


class MRACPRBase(MRACBase):
    def step(self, pm_meas, phm_meas, U_pid, pmk, phmk, q, r, gamma, lambda_f, kappa):
        if self.nf == 0:
            return np.zeros_like(pm_meas)
        self.acc_f += DT_C * 2 * np.pi * 10 * ((pm_meas - self.p_prev) / DT_C - self.acc_f)
        Phi = features(self.ctrl, pm_meas / P_N, phm_meas / PHI_N, U_pid / UN_N,
                       np.full_like(pm_meas, pmk / P_N), q * r / 0.2, self.acc_f / ACC_N, self.rbf)
        s = (pm_meas - pmk) + LAM * (phm_meas - phmk)
        den = 1.0 + (Phi * Phi).sum(1)
        
        y = gamma[:, None] * Phi * (s / den)[:, None]
        upd = y - SIGMA * self.Th
        self.Th += DT_C * upd
        nrm = np.sqrt((self.Th * self.Th).sum(1))
        self.Th *= np.minimum(1.0, TH_MAX / (nrm + 1e-12))[:, None]
        
        if not hasattr(self, 'Th_f'):
            self.Th_f = np.zeros_like(self.Th)
        self.Th_f += DT_C * lambda_f[:, None] * (self.Th - self.Th_f)
        
        raw = -U_SCALE * (self.Th * Phi).sum(1) - U_SCALE * kappa * ((self.Th - self.Th_f) * Phi).sum(1)
        self.u_ad += DT_C * OMEGA_U * (raw - self.u_ad)
        self.p_prev = pm_meas
        return self.u_ad

class MRAC_PR(MRAC_S6):
    """
    Performance recovery / low-frequency learning.
    Adds a low-pass-filtered copy of the adaptive term, W_f' = -lambda_f (W_f - W),
    and uses it in the control law. Source: Yucelen & Calise.
    """
    name = 'mrac_pr'
    PARAMS = get_params(0.31622776601683794)
    PARAMS['lambda_f'] = (10.0, 1.0, 100.0, 'log')
    PARAMS['kappa'] = (1.0, 0.0, 10.0, 'lin')

    def __init__(self, B, params=None):
        super().__init__(B, params)
        self.mrac_r = MRACPRBase(self.ctrl_type, B)
        self.mrac_p = MRACPRBase(self.ctrl_type, B)

    def controller_update(self, o, u_nom, wd):
        cmd_r = self.des[:, 0]
        cmd_p = self.des[:, 1]
        cmd_y = o['ref']['yaw']
        cmd_z = o['ref']['p'][:, 2]

        ref_state = self.ref.step(cmd_r, cmd_p, cmd_y, cmd_z)

        phm_r = np.deg2rad(o['rpy'][:, 0])
        pm_r = np.deg2rad(o['gyro'][:, 0])
        qm = np.deg2rad(o['gyro'][:, 1])
        rm = np.deg2rad(o['gyro'][:, 2])
        phm_p = np.deg2rad(o['rpy'][:, 1])
        pm_p = qm 
        
        p_r_ref = np.deg2rad(ref_state['p'])
        phi_r_ref = np.deg2rad(ref_state['phi'])
        p_p_ref = np.deg2rad(ref_state['q'])
        phi_p_ref = np.deg2rad(ref_state['theta'])
        
        gamma = self.p['gamma'] if isinstance(self.p['gamma'], np.ndarray) else np.full(self.B, self.p['gamma'])
        lambda_f = self.p['lambda_f'] if isinstance(self.p['lambda_f'], np.ndarray) else np.full(self.B, self.p['lambda_f'])
        kappa = self.p['kappa'] if isinstance(self.p['kappa'], np.ndarray) else np.full(self.B, self.p['kappa'])
        
        u_ad_r = self.mrac_r.step(pm_r, phm_r, u_nom[:, 0], p_r_ref, phi_r_ref, qm, rm, gamma, lambda_f, kappa)
        u_ad_p = self.mrac_p.step(pm_p, phm_p, u_nom[:, 1], p_p_ref, phi_p_ref, pm_r, rm, gamma, lambda_f, kappa)
        
        U = u_nom.copy()
        U[:, 0] += u_ad_r
        U[:, 1] += u_ad_p
        return U
