import numpy as np
from fwpid import FwPID, THR_BASE
from plant import B_RP, J0, B_YAW, DT_C

class L1(FwPID):
    name = 'l1'
    PARAMS = {
        'pos_kp': (1.0, 0.25, 4.0, 'log'),
        'pos_kd': (1.0, 0.25, 4.0, 'log'),
        'vel_kp': (1.0, 0.25, 4.0, 'log'),
        'vel_kd': (1.0, 0.25, 4.0, 'log'),
        'ang_kp': (1.0, 0.5, 2.0, 'log'),
        'rate_kp': (1.0, 0.5, 2.0, 'log'),
        'rate_kd': (1.0, 0.5, 2.0, 'log'),
        'zp_kp': (1.0, 0.25, 4.0, 'log'),
        'zp_ki': (1.0, 0.25, 8.0, 'log'),
        'zr_kp': (1.0, 0.25, 4.0, 'log'),
        'thr_base': (THR_BASE, 2900.0, 3200.0, 'lin'),
        'l1_am': (10.0, 2.0, 50.0, 'log'),
        'l1_f_hz': (2.0, 0.5, 10.0, 'log'),
        'l1_clip': (50.0, 10.0, 200.0, 'lin')
    }

    def __init__(self, B, params=None):
        temp_params = params.copy() if params else {}
        temp_params.setdefault('z_sumemax', 1.0)
        temp_params.setdefault('ff_v', 0.0)
        temp_params.setdefault('ff_a', 0.0)
        super().__init__(B, temp_params)
        
        self.w_hat = np.zeros((B, 3))
        self.u_ad = np.zeros((B, 3))
        
        p = self.p
        self.b_vec = np.array([B_RP, B_RP * J0[0] / J0[1], np.rad2deg(B_YAW)])
        
        l1_am = np.atleast_1d(p['l1_am'])
        Am = -np.broadcast_to(l1_am[:, None] if len(l1_am) > 1 else l1_am, (B, 3))
        self.exp_Am_Ts = np.exp(Am * DT_C)
        self.Phi_Ts = (self.exp_Am_Ts - 1.0) / Am
        
        l1_wc = np.atleast_1d(p['l1_f_hz']) * 2 * np.pi
        wc = np.broadcast_to(l1_wc[:, None] if len(l1_wc) > 1 else l1_wc, (B, 3))
        self.alpha = np.exp(-wc * DT_C)
        
        clip_val = np.atleast_1d(p['l1_clip'])
        self.clip_arr = np.broadcast_to(clip_val[:, None] if len(clip_val) > 1 else clip_val, (B, 3))

    def controller_update(self, o, u_nom, wd):
        k = o['k']
        w = o['gyro']

        if k == 0:
            self.w_hat = w.copy()

        sigma_hat = - (1.0 / self.b_vec) * (1.0 / self.Phi_Ts) * self.exp_Am_Ts * (self.w_hat - w)
        self.u_ad = self.alpha * self.u_ad + (1 - self.alpha) * (-sigma_hat)
        self.u_ad = np.clip(self.u_ad, -self.clip_arr, self.clip_arr)
        
        self.w_hat = w + self.exp_Am_Ts * (self.w_hat - w) + self.Phi_Ts * self.b_vec * (u_nom + self.u_ad + sigma_hat)
        
        return u_nom + self.u_ad
