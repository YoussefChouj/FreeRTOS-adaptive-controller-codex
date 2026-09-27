"""3-layer MRAC controller for bench_v1."""
import os, sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'adaptive_compare'))
from fwpid import FwPID, scaled
from sim_coupled import PID, ANG_PR, RATE_PR
from plant import B_RP, J0


def _col(v):
    """Per-row param (B,) -> (B, 1) so it broadcasts over (axis, row, k); scalars unchanged (fix 2026-09-28, P1 crash 2)."""
    v = np.asarray(v, dtype=float)
    return v[:, None] if v.ndim == 1 else v


def _preview(o, horizon):
    """o['preview'](n) takes one int; a tune batch carries one horizon per row (fix 2026-09-28, P1 crash)."""
    h = np.atleast_1d(np.asarray(horizon)).astype(int)
    if h.size == 1 or np.all(h == h[0]):
        return o['preview'](int(h[0]))
    out = None
    for n in np.unique(h):
        r = o['preview'](int(n)); m = h == n
        if out is None:
            out = {k: np.array(v, dtype=float) for k, v in r.items()}
        for k, v in r.items():
            out[k][m] = np.asarray(v)[m]
    return out

class MRAC3L(FwPID):
    MODE = 'None'
    PARAMS = dict(FwPID.PARAMS)
    PARAMS.update({
        'gamma': (1.0, 0.1, 10.0, 'log'),
        'lam': (4.0, 0.1, 20.0, 'log'),
        'T': (1.0, 0.1, 10.0, 'log'),
        'tau': (1.0, 0.1, 5.0, 'log'),
        'horizon': (20.0, 1.0, 100.0, 'lin'),
        'sigma': (0.01, 0.001, 0.1, 'log'),
    })

    def __init__(self, B, params=None):
        super().__init__(B, params)
        p = self.p
        self.B = B
        
        self.W = np.zeros((2, B, 6))
        self.y_R = np.zeros((2, B, 4))
        self.y_P = np.zeros((2, B, 4))
        self.E_R = np.ones((2, B, 4)) * 1e-6
        self.E_P = np.ones((2, B, 4)) * 1e-6
        self.g_smooth = np.ones((2, B, 4)) / 4.0
        self.u_ad_smooth = np.zeros((2, B))
        
        self.dt = 0.005
        self.alphas = np.array([2 * np.pi * fc * self.dt for fc in [0.5, 2.0, 5.0, 12.0]])
        self.alpha_E = 2 * np.pi * 1.0 * self.dt
        
        # Band 0: bias; Band 1: u_nom, xm; Band 2: rate, rate*tanh; Band 3: cross
        # Scales match firmware baseline weights
        self.A = np.zeros((4, 6))
        self.A[0, 0] = 1.5
        self.A[1, 4] = 0.1; self.A[1, 5] = 0.1
        self.A[2, 1] = 0.2; self.A[2, 2] = 0.05
        self.A[3, 3] = 0.05
        
        self.ref_ang = [[PID(scaled(ANG_PR, Kp=p['ang_kp']), B) for _ in range(2)] for _ in range(2)]
        self.ref_rate = [[PID(scaled(RATE_PR, Kp=p['rate_kp'], Kd=p['rate_kd']), B) for _ in range(2)] for _ in range(2)]
        self.G = np.array([B_RP, B_RP * J0[0] / J0[1]])
        self.tau_m = 0.050
        
        self.angle_m = np.zeros((2, B))
        self.rate_m = np.zeros((2, B))
        self.U_lag = np.zeros((2, B))
        self.U_buf = np.zeros((2, B, 4))
        self.buf_idx = 0
        
        self.angle_m_ahead = np.zeros((2, B))
        self.rate_m_ahead = np.zeros((2, B))
        self.U_lag_ahead = np.zeros((2, B))
        self.U_buf_ahead = np.zeros((2, B, 4))
        self.buf_idx_ahead = 0

    def _step_ref_model(self, m_idx, des_rp, angle_m, rate_m, U_lag, U_buf, buf_idx):
        wd = np.stack([self.ref_ang[m_idx][i].step(des_rp[i] - angle_m[i]) for i in range(2)])
        U = np.stack([self.ref_rate[m_idx][i].step(wd[i] - rate_m[i]) for i in range(2)])
        U_buf[:, :, buf_idx] = U
        buf_idx = (buf_idx + 1) % 4
        U_delayed = U_buf[:, :, buf_idx]
        U_lag += self.dt * (U_delayed - U_lag) / self.tau_m
        rate_m += self.dt * (self.G[:, None] * U_lag)
        angle_m += self.dt * rate_m
        return angle_m, rate_m, U_lag, buf_idx

    def _band_energies(self, sig, y, E):
        for i, a in enumerate(self.alphas):
            y[:, :, i] += a * (sig - y[:, :, i])
        b0 = y[:, :, 0]
        b1 = y[:, :, 1] - y[:, :, 0]
        b2 = y[:, :, 2] - y[:, :, 1]
        b3 = y[:, :, 3] - y[:, :, 2]
        bands = np.stack([b0, b1, b2, b3], axis=-1)
        E += self.alpha_E * (bands**2 - E)
        return E

    def controller_update(self, o, u_nom, wd):
        des_rp = self.des.T
        self.angle_m, self.rate_m, self.U_lag, self.buf_idx = self._step_ref_model(0, des_rp, self.angle_m, self.rate_m, self.U_lag, self.U_buf, self.buf_idx)
        
        r_ahead = _preview(o, self.p['horizon'])
        yaw_ahead = np.deg2rad(r_ahead['yaw'])
        c, s_ = np.cos(yaw_ahead), np.sin(yaw_ahead)
        af = c * r_ahead['a'][:, 0] + s_ * r_ahead['a'][:, 1]
        al = -s_ * r_ahead['a'][:, 0] + c * r_ahead['a'][:, 1]
        des_rp_ahead = np.stack([np.rad2deg(np.arctan(-al / 9.81)), np.rad2deg(np.arctan(af / 9.81))])
        self.angle_m_ahead, self.rate_m_ahead, self.U_lag_ahead, self.buf_idx_ahead = self._step_ref_model(1, des_rp_ahead, self.angle_m_ahead, self.rate_m_ahead, self.U_lag_ahead, self.U_buf_ahead, self.buf_idx_ahead)
        
        gyro_rad = np.deg2rad(o['gyro'][:, :2].T)
        gyro_yaw_rad = np.deg2rad(o['gyro'][:, 2])
        rate_m_rad = np.deg2rad(self.rate_m)
        angle_rad = np.deg2rad(o['rpy'][:, :2].T)
        angle_m_rad = np.deg2rad(self.angle_m)
        cross_rad = np.stack([gyro_rad[1] * gyro_yaw_rad, gyro_rad[0] * gyro_yaw_rad])
        
        phi = np.ones((2, self.B, 6))
        phi[:, :, 1] = gyro_rad / 5.0
        phi[:, :, 2] = phi[:, :, 1] * np.tanh(phi[:, :, 1])
        phi[:, :, 3] = cross_rad / 0.2
        phi[:, :, 4] = u_nom[:, :2].T / 300.0
        phi[:, :, 5] = rate_m_rad / 5.0
        
        s_err = (gyro_rad - rate_m_rad) + self.p['lam'] * (angle_rad - angle_m_rad)
        rate_m_rad_ahead = np.deg2rad(self.rate_m_ahead)
        
        E_R = self._band_energies(s_err, self.y_R, self.E_R)
        E_P = self._band_energies(rate_m_rad_ahead, self.y_P, self.E_P)
        
        def softmax_gate(E):
            logits = np.log(np.maximum(E, 1e-12)) / _col(self.p['T'])
            logits -= np.max(logits, axis=-1, keepdims=True)
            ex = np.exp(logits)
            return ex / np.sum(ex, axis=-1, keepdims=True)
            
        g_R = softmax_gate(E_R)
        g_P = softmax_gate(E_P)
        
        if self.MODE == 'Unrouted':
            g = np.ones_like(g_R) / 4.0
        elif self.MODE == 'Reactive':
            g = g_R
        elif self.MODE == 'Predictive':
            g = g_P
        elif self.MODE == 'Both':
            g = (g_R + g_P) / 2.0
            
        alpha_tau = self.dt / (_col(self.p['tau']) + self.dt)
        self.g_smooth += alpha_tau * (g - self.g_smooth)
        
        Gamma_t = _col(self.p['gamma']) * np.einsum('xbi,ij->xbj', self.g_smooth, self.A)
        Gamma_t = np.maximum(Gamma_t, 1e-3)
        
        denom = 1.0 + np.sum(phi**2, axis=-1, keepdims=True)
        dW = Gamma_t * phi * (s_err[:, :, None] / denom) - _col(self.p['sigma']) * self.W
        self.W += self.dt * dW
        nrm = np.sqrt(np.sum(self.W**2, axis=-1, keepdims=True))
        self.W *= np.minimum(1.0, 3.0 / (nrm + 1e-12))
        
        raw_u_ad = -np.sum(self.W * phi, axis=-1) * 300.0
        self.u_ad_smooth += self.dt * 25.0 * (raw_u_ad - self.u_ad_smooth)
        
        u_out = u_nom.copy()
        u_out[:, :2] = np.clip(u_out[:, :2] + self.u_ad_smooth.T, -500, 500)
        return u_out

class MRAC3L_Unrouted(MRAC3L): MODE = 'Unrouted'
class MRAC3L_Reactive(MRAC3L): MODE = 'Reactive'
class MRAC3L_Predictive(MRAC3L): MODE = 'Predictive'
class MRAC3L_Both(MRAC3L): MODE = 'Both'
