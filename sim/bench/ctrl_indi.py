import numpy as np
from scipy.signal import butter
from fwpid import FwPID, THR_BASE
from plant import B_RP, J0, B_YAW, DT_C, IIR, TAU_M, DELAY_TICKS

class INDI(FwPID):
    name = 'indi'
    PARAMS = {
        'pos_kp': (1.0, 0.25, 4.0, 'log'),
        'pos_kd': (1.0, 0.25, 4.0, 'log'),
        'vel_kp': (1.0, 0.25, 4.0, 'log'),
        'vel_kd': (1.0, 0.25, 4.0, 'log'),
        'ang_kp': (1.0, 0.5, 2.0, 'log'),
        'zp_kp': (1.0, 0.25, 4.0, 'log'),
        'zp_ki': (1.0, 0.25, 8.0, 'log'),
        'zr_kp': (1.0, 0.25, 4.0, 'log'),
        'z_sumemax': (1.0, 1.0, 30.0, 'log'),
        'thr_base': (THR_BASE, 2900.0, 3200.0, 'lin'),
        'indi_kxy': (15.0, 5.0, 40.0, 'lin'),
        'indi_kz': (10.0, 5.0, 30.0, 'lin'),
        'indi_f_hz': (15.0, 5.0, 40.0, 'lin'),
        'indi_g': (1.0, 0.5, 2.0, 'lin')
    }

    def __init__(self, B, params=None):
        temp_params = params.copy() if params else {}
        temp_params.setdefault('rate_kp', 0.0)
        temp_params.setdefault('rate_kd', 0.0)
        temp_params.setdefault('ff_v', 0.0)
        temp_params.setdefault('ff_a', 0.0)
        super().__init__(B, temp_params)
        p = self.p

        b, a = butter(2, np.clip(np.mean(p['indi_f_hz']) / 100.0, 0.01, 0.99))
        self.gyro_f = IIR(b, a, (B, 3))
        self.u_f = IIR(b, a, (B, 3))
        
        self.last_gyro = np.zeros((B, 3))
        self.u_hist = np.zeros((DELAY_TICKS, B, 3))
        self.u_motor = np.zeros((B, 3))
        self.alpha = np.exp(-DT_C / TAU_M)

    def rate_loop(self, o, wd):
        B = self.B
        k = o['k']
        
        # at k=0, avoid large derivative spike
        if k == 0:
            self.last_gyro = o['gyro'].copy()

        gyro_dot = (o['gyro'] - self.last_gyro) / DT_C
        self.last_gyro = o['gyro'].copy()
        
        nu_f = self.gyro_f(gyro_dot)
        
        kxy = np.atleast_1d(self.p['indi_kxy'])
        kz = np.atleast_1d(self.p['indi_kz'])
        k_rate = np.stack([kxy, kxy, kz], axis=1)
        k_rate = np.broadcast_to(k_rate, (B, 3))
            
        nu = k_rate * (wd - o['gyro'])
        
        u_delayed = self.u_hist[0]
        self.u_motor = self.alpha * self.u_motor + (1 - self.alpha) * u_delayed
        u_filt = self.u_f(self.u_motor)
        
        g_scale = self.p['indi_g']
        if np.isscalar(g_scale):
            G = np.array([B_RP, B_RP * J0[0] / J0[1], np.rad2deg(B_YAW)]) * g_scale
            G = np.broadcast_to(G, (B, 3))
        else:
            G = np.array([B_RP, B_RP * J0[0] / J0[1], np.rad2deg(B_YAW)]) * g_scale[:, None]
        
        U = u_filt + (nu - nu_f) / G
        
        self.u_hist = np.roll(self.u_hist, -1, axis=0)
        self.u_hist[-1] = U
        
        return U
