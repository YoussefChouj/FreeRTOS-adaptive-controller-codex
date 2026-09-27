import numpy as np
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'adaptive_compare'))
from sim_coupled import MRAC as MRACBase, PID, ANG_PR, RATE_PR, ANG_Y, RATE_Y, POS_Z, RATE_Z
from sim_coupled import DELAY, DT_P, DT_C, SUB, TAU_M, HOVER_PWM, c_PR, c_Y, c_T, Jx, Jy, Jz, M_mass, g
from fwpid import FwPID

class RefModel:
    def __init__(self, B):
        self.B = B
        self.ang_r = PID(ANG_PR, B)
        self.rate_r = PID(RATE_PR, B)
        self.ang_p = PID(ANG_PR, B)
        self.rate_p = PID(RATE_PR, B)
        self.ang_y = PID(ANG_Y, B)
        self.rate_y = PID(RATE_Y, B)
        self.pos_z = PID(POS_Z, B)
        self.rate_z = PID(RATE_Z, B)
        self.d_steps = int(round(DELAY / DT_P))
        self.buf1 = np.ones((self.d_steps + 1, B)) * HOVER_PWM
        self.buf2 = np.ones((self.d_steps + 1, B)) * HOVER_PWM
        self.buf3 = np.ones((self.d_steps + 1, B)) * HOVER_PWM
        self.buf4 = np.ones((self.d_steps + 1, B)) * HOVER_PWM
        self.bi = 0
        self.M1_act = np.ones(B) * HOVER_PWM
        self.M2_act = np.ones(B) * HOVER_PWM
        self.M3_act = np.ones(B) * HOVER_PWM
        self.M4_act = np.ones(B) * HOVER_PWM
        self.phi = np.zeros(B)
        self.theta = np.zeros(B)
        self.psi = np.zeros(B)
        self.p = np.zeros(B)
        self.q = np.zeros(B)
        self.r = np.zeros(B)
        self.z = np.zeros(B)
        self.vz = np.zeros(B)
        
    def step(self, cmd_r, cmd_p, cmd_y, cmd_z):
        phm_meas = self.phi.copy()
        thm_meas = self.theta.copy()
        psm_meas = self.psi.copy()
        pm_meas = self.p.copy()
        qm_meas = self.q.copy()
        rm_meas = self.r.copy()
        zm_meas = self.z.copy()
        vzm_meas = self.vz.copy()
        
        U_pid_r = self.rate_r.step(self.ang_r.step(cmd_r - np.rad2deg(phm_meas)) - np.rad2deg(pm_meas))
        U_pid_p = self.rate_p.step(self.ang_p.step(cmd_p - np.rad2deg(thm_meas)) - np.rad2deg(qm_meas))
        U_pid_y = self.rate_y.step(self.ang_y.step(cmd_y - np.rad2deg(psm_meas)) - np.rad2deg(rm_meas))
        U_pid_z = self.rate_z.step(self.pos_z.step(cmd_z - zm_meas) - vzm_meas)
        
        U_tot_r = np.clip(U_pid_r, -500, 500)
        U_tot_p = np.clip(U_pid_p, -500, 500)
        U_tot_y = np.clip(U_pid_y, -650, 650)
        Thr_out = np.clip(U_pid_z + HOVER_PWM, 2000, 4000)
        
        M1 = Thr_out + U_tot_p - U_tot_r - U_tot_y
        M2 = Thr_out - U_tot_p + U_tot_r - U_tot_y
        M3 = Thr_out + U_tot_p + U_tot_r + U_tot_y
        M4 = Thr_out - U_tot_p - U_tot_r + U_tot_y
        
        M1_c = np.clip(M1, 2000, 4000)
        M2_c = np.clip(M2, 2000, 4000)
        M3_c = np.clip(M3, 2000, 4000)
        M4_c = np.clip(M4, 2000, 4000)
        
        for j in range(SUB):
            self.buf1[self.bi] = M1_c
            self.buf2[self.bi] = M2_c
            self.buf3[self.bi] = M3_c
            self.buf4[self.bi] = M4_c
            self.bi = (self.bi + 1) % (self.d_steps + 1)
            
            self.M1_act += DT_P / TAU_M * (self.buf1[self.bi] - self.M1_act)
            self.M2_act += DT_P / TAU_M * (self.buf2[self.bi] - self.M2_act)
            self.M3_act += DT_P / TAU_M * (self.buf3[self.bi] - self.M3_act)
            self.M4_act += DT_P / TAU_M * (self.buf4[self.bi] - self.M4_act)
            
            tau_x = c_PR * (self.M2_act + self.M3_act - self.M1_act - self.M4_act)
            tau_y = c_PR * (self.M1_act + self.M3_act - self.M2_act - self.M4_act)
            tau_z = c_Y * (self.M3_act + self.M4_act - self.M1_act - self.M2_act)
            F_z = c_T * (self.M1_act + self.M2_act + self.M3_act + self.M4_act - 4*2000)
            
            self.p += DT_P * (tau_x - (Jz - Jy) * self.q * self.r) / Jx
            self.q += DT_P * (tau_y - (Jx - Jz) * self.p * self.r) / Jy
            self.r += DT_P * (tau_z - (Jy - Jx) * self.p * self.q) / Jz
            self.vz += DT_P * (F_z - M_mass * g) / M_mass
            
            self.phi += DT_P * self.p
            self.theta += DT_P * self.q
            self.psi += DT_P * self.r
            self.z += DT_P * self.vz

        return dict(
            phi=np.rad2deg(phm_meas),
            theta=np.rad2deg(thm_meas),
            psi=np.rad2deg(psm_meas),
            p=np.rad2deg(pm_meas),
            q=np.rad2deg(qm_meas),
            r=np.rad2deg(rm_meas)
        )

class MRACBaseController(FwPID):
    ctrl_type = None
    default_gamma = 1.0

    def __init__(self, B, params=None):
        # We need to add 'gamma' parameter, so we override PARAMS in subclasses
        super().__init__(B, params)
        self.p["ff_a"] = 0.0
        self.ref = RefModel(B)
        self.mrac_r = MRACBase(self.ctrl_type, B)
        self.mrac_p = MRACBase(self.ctrl_type, B)
        # Drop zp_ki and z_sumemax from FwPID PARAMS in our PARAMS to keep under 14 limit?
        # FwPID has 14 params. We add 1. We must drop 1 to keep <= 14.
        # Let's drop ff_a since it's 0.0 by default. Wait, the API says "dropping FwPID knobs only if needed (say which)".
        # Wait, Python classes have their own PARAMS attribute. We'll set it per class.
        
    def controller_update(self, o, u_nom, wd):
        # o['rpy'] is ZYX (yaw, pitch, roll) in deg. So roll=0, pitch=1.
        # gyro is p, q, r in dps.
        # wd is the desired rate in dps.
        # But wait! The reference model is driven by the attitude Des.
        # des is computed by xy_loop, stored in self.des.
        # self.des[:, 0] is roll des, self.des[:, 1] is pitch des.
        cmd_r = self.des[:, 0]
        cmd_p = self.des[:, 1]
        cmd_y = o['ref']['yaw'] # yaw target
        cmd_z = o['ref']['p'][:, 2] # z target from z_pos

        ref_state = self.ref.step(cmd_r, cmd_p, cmd_y, cmd_z)

        # MRAC step signature:
        # step(pm_meas, phm_meas, U_pid, pmk, phmk, q, r, gamma)
        # MRAC wants rad/s for pm_meas, rad for phm_meas, rad/s for pmk, rad for phmk
        
        phm_r = np.deg2rad(o['rpy'][:, 0])
        pm_r = np.deg2rad(o['gyro'][:, 0])
        qm = np.deg2rad(o['gyro'][:, 1])
        rm = np.deg2rad(o['gyro'][:, 2])
        
        phm_p = np.deg2rad(o['rpy'][:, 1])
        pm_p = qm # pitch rate
        
        p_r_ref = np.deg2rad(ref_state['p'])
        phi_r_ref = np.deg2rad(ref_state['phi'])
        
        p_p_ref = np.deg2rad(ref_state['q'])
        phi_p_ref = np.deg2rad(ref_state['theta'])
        
        # gamma is provided via self.p['gamma']
        if isinstance(self.p['gamma'], np.ndarray):
            gamma = self.p['gamma']
        else:
            gamma = np.full(self.B, self.p['gamma'])
            
        u_ad_r = self.mrac_r.step(pm_r, phm_r, u_nom[:, 0], p_r_ref, phi_r_ref, qm, rm, gamma)
        u_ad_p = self.mrac_p.step(pm_p, phm_p, u_nom[:, 1], p_p_ref, phi_p_ref, pm_r, rm, gamma)
        
        U = u_nom.copy()
        U[:, 0] += u_ad_r
        U[:, 1] += u_ad_p
        return U

def get_params(gamma_val):
    # FwPID has 14. We drop 'ff_a' (index 13) to add 'gamma'
    p = dict(FwPID.PARAMS)
    del p['ff_a']
    p['gamma'] = (gamma_val, 10**-1.5, 10**2, 'log')
    return p

class MRAC_S6(MRACBaseController):
    name = 'mrac_s6'
    ctrl_type = 'S6'
    PARAMS = get_params(0.31622776601683794)

class MRAC_S10(MRACBaseController):
    name = 'mrac_s10'
    ctrl_type = 'S10'
    PARAMS = get_params(0.1778279410038923)

class MRAC_RBF6(MRACBaseController):
    name = 'mrac_rbf6'
    ctrl_type = 'RBF6'
    PARAMS = get_params(0.31622776601683794)

class MRAC_RBF12(MRACBaseController):
    name = 'mrac_rbf12'
    ctrl_type = 'RBF12'
    PARAMS = get_params(0.31622776601683794)

class MRAC_RBF24(MRACBaseController):
    name = 'mrac_rbf24'
    ctrl_type = 'RBF24'
    PARAMS = get_params(0.1)
