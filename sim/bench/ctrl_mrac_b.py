import numpy as np
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'adaptive_compare'))
from sim_coupled import MRAC as MRACBase, features, P_N, PHI_N, UN_N, ACC_N, DT_C, LAM, SIGMA, TH_MAX, U_SCALE, OMEGA_U, c_PR, Jx, Jy

from ctrl_mrac import MRAC_S6, get_params

b_r = (c_PR * 4) / Jx
b_p = (c_PR * 4) / Jy

# MRAC_CRM
class MRAC_CRM(MRAC_S6):
    name = 'mrac_crm'
    PARAMS = get_params(0.31622776601683794)
    PARAMS['L'] = (0.0, 0.0, 50.0, 'lin')

    def controller_update(self, o, u_nom, wd):
        U = super().controller_update(o, u_nom, wd)
        
        p_L = self.p['L']
        L = p_L if isinstance(p_L, np.ndarray) else np.full(self.B, p_L)
        
        phm_r = np.deg2rad(o['rpy'][:, 0])
        pm_r = np.deg2rad(o['gyro'][:, 0])
        phm_p = np.deg2rad(o['rpy'][:, 1])
        qm = np.deg2rad(o['gyro'][:, 1])
        
        self.ref.phi += DT_C * L * (phm_r - self.ref.phi)
        self.ref.p += DT_C * L * (pm_r - self.ref.p)
        self.ref.theta += DT_C * L * (phm_p - self.ref.theta)
        self.ref.q += DT_C * L * (qm - self.ref.q)
        
        return U

# MRAC_Composite
class MRACCompositeBase(MRACBase):
    def step(self, pm_meas, phm_meas, U_pid, pmk, phmk, q, r, gamma, kc, b_val, U_act):
        if self.nf == 0:
            return np.zeros_like(pm_meas)
        self.acc_f += DT_C * 2 * np.pi * 10 * ((pm_meas - self.p_prev) / DT_C - self.acc_f)
        Phi = features(self.ctrl, pm_meas / P_N, phm_meas / PHI_N, U_pid / UN_N,
                       np.full_like(pm_meas, pmk / P_N), q * r / 0.2, self.acc_f / ACC_N, self.rbf)
        s = (pm_meas - pmk) + LAM * (phm_meas - phmk)
        den = 1.0 + (Phi * Phi).sum(1)
        
        upd = gamma[:, None] * Phi * (s / den)[:, None] - SIGMA * self.Th
        
        rate_dot_hat = b_val * (U_act + U_SCALE * (self.Th * Phi).sum(1))
        E = rate_dot_hat - self.acc_f
        
        upd -= kc[:, None] * gamma[:, None] * Phi * E[:, None]
        
        self.Th += DT_C * upd
        
        nrm = np.sqrt((self.Th * self.Th).sum(1))
        self.Th *= np.minimum(1.0, TH_MAX / (nrm + 1e-12))[:, None]
        raw = -U_SCALE * (self.Th * Phi).sum(1)
        self.u_ad += DT_C * OMEGA_U * (raw - self.u_ad)
        self.p_prev = pm_meas
        return self.u_ad

class MRAC_Composite(MRAC_S6):
    name = 'mrac_comp'
    PARAMS = get_params(0.31622776601683794)
    PARAMS['kc'] = (0.0, 0.0, 10.0, 'lin')
    
    def __init__(self, B, params=None):
        super().__init__(B, params)
        self.mrac_r = MRACCompositeBase(self.ctrl_type, B)
        self.mrac_p = MRACCompositeBase(self.ctrl_type, B)
        
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
        kc = self.p['kc'] if isinstance(self.p['kc'], np.ndarray) else np.full(self.B, self.p['kc'])
        
        U_act_r = (o['mot'][:, 1] + o['mot'][:, 2] - o['mot'][:, 0] - o['mot'][:, 3]) / 4.0
        U_act_p = (o['mot'][:, 0] + o['mot'][:, 2] - o['mot'][:, 1] - o['mot'][:, 3]) / 4.0
        
        u_ad_r = self.mrac_r.step(pm_r, phm_r, u_nom[:, 0], p_r_ref, phi_r_ref, qm, rm, gamma, kc, b_r, U_act_r)
        u_ad_p = self.mrac_p.step(pm_p, phm_p, u_nom[:, 1], p_p_ref, phi_p_ref, pm_r, rm, gamma, kc, b_p, U_act_p)
        
        U = u_nom.copy()
        U[:, 0] += u_ad_r
        U[:, 1] += u_ad_p
        return U

# MRAC_SatAware
class MRACSatAwareBase(MRACBase):
    def step(self, pm_meas, phm_meas, U_pid, pmk, phmk, q, r, gamma, mu, delta_U):
        if self.nf == 0:
            return np.zeros_like(pm_meas)
        self.acc_f += DT_C * 2 * np.pi * 10 * ((pm_meas - self.p_prev) / DT_C - self.acc_f)
        Phi = features(self.ctrl, pm_meas / P_N, phm_meas / PHI_N, U_pid / UN_N,
                       np.full_like(pm_meas, pmk / P_N), q * r / 0.2, self.acc_f / ACC_N, self.rbf)
        s = (pm_meas - pmk) + LAM * (phm_meas - phmk)
        den = 1.0 + (Phi * Phi).sum(1)
        
        upd = gamma[:, None] * Phi * (s / den)[:, None] - SIGMA * self.Th
        upd -= mu[:, None] * np.abs(delta_U)[:, None] * self.Th
        
        self.Th += DT_C * upd
        
        nrm = np.sqrt((self.Th * self.Th).sum(1))
        self.Th *= np.minimum(1.0, TH_MAX / (nrm + 1e-12))[:, None]
        raw = -U_SCALE * (self.Th * Phi).sum(1)
        self.u_ad += DT_C * OMEGA_U * (raw - self.u_ad)
        self.p_prev = pm_meas
        return self.u_ad

class MRAC_SatAware(MRAC_S6):
    name = 'mrac_sataware'
    PARAMS = get_params(0.31622776601683794)
    PARAMS['mu'] = (0.0, 0.0, 10.0, 'lin')
    
    def __init__(self, B, params=None):
        super().__init__(B, params)
        self.mrac_r = MRACSatAwareBase(self.ctrl_type, B)
        self.mrac_p = MRACSatAwareBase(self.ctrl_type, B)
        self.U_cmd_prev = np.zeros((B, 2))
        
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
        mu = self.p['mu'] if isinstance(self.p['mu'], np.ndarray) else np.full(self.B, self.p['mu'])
        
        U_act_r = (o['mot'][:, 1] + o['mot'][:, 2] - o['mot'][:, 0] - o['mot'][:, 3]) / 4.0
        U_act_p = (o['mot'][:, 0] + o['mot'][:, 2] - o['mot'][:, 1] - o['mot'][:, 3]) / 4.0
        
        delta_U_r = self.U_cmd_prev[:, 0] - U_act_r
        delta_U_p = self.U_cmd_prev[:, 1] - U_act_p
        
        u_ad_r = self.mrac_r.step(pm_r, phm_r, u_nom[:, 0], p_r_ref, phi_r_ref, qm, rm, gamma, mu, delta_U_r)
        u_ad_p = self.mrac_p.step(pm_p, phm_p, u_nom[:, 1], p_p_ref, phi_p_ref, pm_r, rm, gamma, mu, delta_U_p)
        
        U = u_nom.copy()
        U[:, 0] += u_ad_r
        U[:, 1] += u_ad_p
        
        self.U_cmd_prev[:, 0] = U[:, 0]
        self.U_cmd_prev[:, 1] = U[:, 1]
        return U

# MRAC_Proj
class MRACProjBase(MRACBase):
    def step(self, pm_meas, phm_meas, U_pid, pmk, phmk, q, r, gamma, proj_on, eps):
        if self.nf == 0:
            return np.zeros_like(pm_meas)
        self.acc_f += DT_C * 2 * np.pi * 10 * ((pm_meas - self.p_prev) / DT_C - self.acc_f)
        Phi = features(self.ctrl, pm_meas / P_N, phm_meas / PHI_N, U_pid / UN_N,
                       np.full_like(pm_meas, pmk / P_N), q * r / 0.2, self.acc_f / ACC_N, self.rbf)
        s = (pm_meas - pmk) + LAM * (phm_meas - phmk)
        den = 1.0 + (Phi * Phi).sum(1)
        
        y = gamma[:, None] * Phi * (s / den)[:, None]
        
        nrm = np.sqrt((self.Th * self.Th).sum(1))
        
        f = (nrm**2 - TH_MAX**2) / ( (TH_MAX*(1+eps))**2 - TH_MAX**2 + 1e-12 )
        f = np.clip(f, 0.0, 1.0)
        
        y_dot_Th = (y * self.Th).sum(1)
        
        cond = (f > 0) & (y_dot_Th > 0)
        
        proj_sub = f[:, None] * (y_dot_Th / (nrm**2 + 1e-12))[:, None] * self.Th
        
        y_proj = np.where(cond[:, None], y - proj_sub, y)
        
        # S6 update
        upd_s6 = y - SIGMA * self.Th
        
        # Interpolate or switch based on proj_on
        # If proj_on == 1, use y_proj. If proj_on == 0, use upd_s6.
        upd = proj_on[:, None] * y_proj + (1.0 - proj_on[:, None]) * upd_s6
        
        self.Th += DT_C * upd
        
        nrm_new = np.sqrt((self.Th * self.Th).sum(1))
        
        # for S6 (proj_on=0), limit is TH_MAX. For proj (proj_on=1), limit is TH_MAX*(1+eps).
        limit_s6 = TH_MAX
        limit_proj = TH_MAX * (1 + eps)
        limit = proj_on * limit_proj + (1.0 - proj_on) * limit_s6
        
        self.Th *= np.minimum(1.0, limit / (nrm_new + 1e-12))[:, None]
        
        raw = -U_SCALE * (self.Th * Phi).sum(1)
        self.u_ad += DT_C * OMEGA_U * (raw - self.u_ad)
        self.p_prev = pm_meas
        return self.u_ad

class MRAC_Proj(MRAC_S6):
    name = 'mrac_proj'
    PARAMS = get_params(0.31622776601683794)
    PARAMS['proj_on'] = (0.0, 0.0, 1.0, 'lin')
    PARAMS['eps'] = (0.1, 0.01, 1.0, 'log')
    
    def __init__(self, B, params=None):
        super().__init__(B, params)
        self.mrac_r = MRACProjBase(self.ctrl_type, B)
        self.mrac_p = MRACProjBase(self.ctrl_type, B)
        
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
        proj_on = self.p['proj_on'] if isinstance(self.p['proj_on'], np.ndarray) else np.full(self.B, self.p['proj_on'])
        eps = self.p['eps'] if isinstance(self.p['eps'], np.ndarray) else np.full(self.B, self.p['eps'])
        
        u_ad_r = self.mrac_r.step(pm_r, phm_r, u_nom[:, 0], p_r_ref, phi_r_ref, qm, rm, gamma, proj_on, eps)
        u_ad_p = self.mrac_p.step(pm_p, phm_p, u_nom[:, 1], p_p_ref, phi_p_ref, pm_r, rm, gamma, proj_on, eps)
        
        U = u_nom.copy()
        U[:, 0] += u_ad_r
        U[:, 1] += u_ad_p
        return U
