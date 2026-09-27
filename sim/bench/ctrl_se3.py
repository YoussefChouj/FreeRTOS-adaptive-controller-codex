import numpy as np
from plant import Controller, A1, A2, V_NOM, PWM_MIN, PWM_MAX, MASS, G, J0, B_RP, B_YAW, rot, DT_C

def veemap(R):
    return np.stack([R[:, 2, 1] - R[:, 1, 2], R[:, 0, 2] - R[:, 2, 0], R[:, 1, 0] - R[:, 0, 1]], 1) / 2.0

class SE3ESO(Controller):
    name = 'se3eso'
    PARAMS = {
        'kx': (5.0, 1.0, 20.0, 'log'),
        'kv': (2.0, 0.5, 10.0, 'log'),
        'kR': (0.8, 0.1, 5.0, 'log'),
        'kW': (0.3, 0.05, 2.0, 'log'),
        'wo': (10.0, 2.0, 50.0, 'log'),
        'yaw_kp': (1.65, 0.1, 10.0, 'log'),
        'yaw_ki': (0.01, 0.0, 1.0, 'lin'),
        'yaw_kd': (0.25, 0.01, 2.0, 'log'),
    }

    def __init__(self, B, params=None):
        super().__init__(B, params)
        self.p_hat = np.zeros((B, 3))
        self.v_hat = np.zeros((B, 3))
        self.d_hat = np.zeros((B, 3))
        self.M = MASS
        self.F_cmd = np.zeros((B, 3))
        self.yaw_ref = np.zeros(B)
        self.sum_ey = np.zeros(B)
        self.pre_ey = np.zeros(B)
        
    def step(self, o):
        B = self.B
        k = o['k']
        dt = DT_C
        
        pos = o['pos']
        vel = o['vel']
        
        if k == 0:
            self.p_hat = pos.copy()
            self.v_hat = vel.copy()
            
        rpy = np.deg2rad(o['rpy'])
        b1, b2, b3 = rot(rpy)
        R = np.stack([b1, b2, b3], 2)
        
        if k % 2 == 0:
            p_ref = o['ref']['p']
            v_ref = o['ref']['v']
            a_ref = o['ref']['a']
            
            ex = pos - p_ref
            ev = vel - v_ref
            
            kx = self.p['kx'][:, None] if isinstance(self.p['kx'], np.ndarray) else self.p['kx']
            kv = self.p['kv'][:, None] if isinstance(self.p['kv'], np.ndarray) else self.p['kv']
            
            self.F_cmd = -kx * ex - kv * ev + np.array([0, 0, self.M * G]) + self.M * a_ref - self.M * self.d_hat
            self.yaw_ref = np.deg2rad(o['ref']['yaw'])
            
        F = self.F_cmd
        normF = np.linalg.norm(F, axis=1, keepdims=True)
        z_d = F / (normF + 1e-12)
        
        yaw = self.yaw_ref
        x_c = np.stack([np.cos(yaw), np.sin(yaw), np.zeros(B)], 1)
        
        y_d_raw = np.cross(z_d, x_c)
        y_d = y_d_raw / (np.linalg.norm(y_d_raw, axis=1, keepdims=True) + 1e-12)
        x_d = np.cross(y_d, z_d)
        
        Rd = np.stack([x_d, y_d, z_d], 2)
        
        eR = veemap(np.matmul(Rd.transpose(0, 2, 1), R) - np.matmul(R.transpose(0, 2, 1), Rd))
        
        gyro = np.deg2rad(o['gyro'])
        eW = gyro
        
        kR = self.p['kR'][:, None] if isinstance(self.p['kR'], np.ndarray) else self.p['kR']
        kW = self.p['kW'][:, None] if isinstance(self.p['kW'], np.ndarray) else self.p['kW']
        
        tau = -kR * eR - kW * eW
        
        e_yaw = (self.yaw_ref - rpy[:, 2] + np.pi) % (2 * np.pi) - np.pi
        self.sum_ey = np.clip(self.sum_ey + e_yaw, -1.0, 1.0)
        # d_ey
        # pre
        
        tau_y_pid = (self.p['yaw_kp'] * e_yaw + self.p['yaw_ki'] * self.sum_ey - self.p['yaw_kd'] * gyro[:, 2])
        tau[:, 2] = tau_y_pid
        
        G_roll = np.deg2rad(B_RP)
        G_pitch = np.deg2rad(B_RP * J0[0] / J0[1])
        G_yaw = B_YAW
        
        U_roll = np.clip(tau[:, 0] / (J0[0] * G_roll), -500, 500)
        U_pitch = np.clip(tau[:, 1] / (J0[1] * G_pitch), -500, 500)
        U_yaw = np.clip(tau[:, 2] / (J0[2] * G_yaw), -650, 650)
        U = np.stack([U_roll, U_pitch, U_yaw], 1)
        
        T_eff_desired = np.sum(F * b3, axis=1) / 4.0
        vbat_scale = (o['vbat'] / V_NOM) ** 2
        T_scaled = T_eff_desired / vbat_scale
        det = A1**2 + 4 * A2 * T_scaled
        det = np.maximum(det, 0)
        x = (-A1 + np.sqrt(det)) / (2 * A2)
        thr = np.clip(x + PWM_MIN, PWM_MIN, PWM_MAX)
        
        M1 = thr - U_pitch - U_roll - U_yaw
        M2 = thr + U_pitch + U_roll - U_yaw
        M3 = thr - U_pitch + U_roll + U_yaw
        M4 = thr + U_pitch - U_roll + U_yaw
        M = np.clip(np.stack([M1, M2, M3, M4], 1), PWM_MIN, PWM_MAX)
        x_act = M - PWM_MIN
        T_act = (A1 * x_act + A2 * x_act**2) * vbat_scale[:, None]
        actual_u = np.sum(T_act, axis=1)[:, None] / self.M * b3
        
        wo = self.p['wo'][:, None] if isinstance(self.p['wo'], np.ndarray) else self.p['wo']
        p_err = self.p_hat - pos
        u_obs = actual_u - np.array([0, 0, G])
        
        self.p_hat += dt * (self.v_hat - 3 * wo * p_err)
        self.v_hat += dt * (u_obs + self.d_hat - 3 * (wo**2) * p_err)
        self.d_hat += dt * (- (wo**3) * p_err)
        
        return dict(U=U, thr=thr)
