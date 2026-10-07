import numpy as np

DEFAULTS = {
    'q_pos': 1e-6,
    'q_acc': 1e-3,
    'q_bof': 1e-5,  # firmware since 2026-10-07 two-channel EKF (0 from 2026-10-03 to 10-07: bof frozen)
    'q_ba': 1e-6,
    'R_of': 1e-4,
    'R_zupt': 1e-4,
    'of_gate': 5.0,  # sigmas, mirrors EkfOf_t.of_gate (0 = off)
    'R_of1': 1e-3,   # of1 (raw FLOW_HEIGHT) 2nd velocity meas, h=[0,1,0,0]; 0 = channel off (2026-10-07)
    'of1_gate': 5.0,
}

VEL_P0 = 0.1      # mirrors EKF_OF_VEL_P0 (ekf_of.c)
REJ_RELEASE = 5   # mirrors EKF_OF_REJ_RELEASE (ekf_of.h): gate-lockout recovery

class EkfOfModel:
    def __init__(self, batch_size, dtype=np.float64, **kwargs):
        self.B = batch_size
        self.dtype = dtype
        self.x = np.zeros((self.B, 8), dtype=dtype)
        self.P = np.zeros((self.B, 2, 4, 4), dtype=dtype)
        
        # Initial P
        self.P[:, :, 0, 0] = 1.0
        self.P[:, :, 1, 1] = VEL_P0
        self.P[:, :, 2, 2] = 0.01
        self.P[:, :, 3, 3] = 0.25
        
        params = DEFAULTS.copy()
        params.update(kwargs)
        self.q_pos = np.asarray(params['q_pos'], dtype=dtype)
        self.q_acc = np.asarray(params['q_acc'], dtype=dtype)
        self.q_bof = np.asarray(params['q_bof'], dtype=dtype)
        self.q_ba  = np.asarray(params['q_ba'], dtype=dtype)
        self.R_of  = np.asarray(params['R_of'], dtype=dtype)
        self.R_zupt = np.asarray(params['R_zupt'], dtype=dtype)
        self.of_gate = np.asarray(params['of_gate'], dtype=dtype)
        self.R_of1 = np.asarray(params['R_of1'], dtype=dtype)
        self.of1_gate = np.asarray(params['of1_gate'], dtype=dtype)
        self.rej_x = np.zeros(self.B, dtype=np.int64)
        self.rej_y = np.zeros(self.B, dtype=np.int64)
        self.rej_run = np.zeros((2, self.B), dtype=np.int64)  # consecutive rejections per axis

    def predict(self, dt, ax, ay):
        ax = np.asarray(ax, dtype=self.dtype)
        ay = np.asarray(ay, dtype=self.dtype)
        
        # x[0..7] = [px, vx, bof_x, py, vy, bof_y, ba_x, ba_y]
        
        # X axis
        ux = ax - self.x[:, 6]
        self.x[:, 0] += self.x[:, 1] * dt + 0.5 * ux * dt * dt
        self.x[:, 1] += ux * dt
        
        # Y axis
        uy = ay - self.x[:, 7]
        self.x[:, 3] += self.x[:, 4] * dt + 0.5 * uy * dt * dt
        self.x[:, 4] += uy * dt
        
        dt2 = dt * dt
        F = np.zeros((self.B, 4, 4), dtype=self.dtype)
        F[:, 0, 0] = 1.0
        F[:, 0, 1] = dt
        F[:, 0, 3] = -0.5 * dt2
        F[:, 1, 1] = 1.0
        F[:, 1, 3] = -dt
        F[:, 2, 2] = 1.0
        F[:, 3, 3] = 1.0
        
        Q = np.zeros((self.B, 4, 4), dtype=self.dtype)
        Q[:, 0, 0] = self.q_pos * dt
        Q[:, 1, 1] = self.q_acc * dt
        Q[:, 2, 2] = self.q_bof * dt
        Q[:, 3, 3] = self.q_ba * dt
        
        # P = F @ P @ F.T + Q
        for axis in range(2):
            self.P[:, axis] = np.matmul(F, np.matmul(self.P[:, axis], np.transpose(F, (0, 2, 1)))) + Q

    def _update_one(self, axis, h, z, R, mask, gate=0.0):
        # axis 0: indices [0, 1, 2, 6]
        # axis 1: indices [3, 4, 5, 7]
        idx = [0, 1, 2, 6] if axis == 0 else [3, 4, 5, 7]
        
        h = np.asarray(h, dtype=self.dtype).reshape(4, 1)
        z = np.asarray(z, dtype=self.dtype)
        
        P = self.P[:, axis]  # (B, 4, 4)
        
        # S = h.T @ P @ h + R
        PHt = np.matmul(P, h)  # (B, 4, 1)
        S = np.matmul(h.T, PHt).reshape(-1) + R  # (B,)
        S = np.maximum(S, 1e-8)
        
        hx = (self.x[:, idx[0]]*h[0] + self.x[:, idx[1]]*h[1] + 
              self.x[:, idx[2]]*h[2] + self.x[:, idx[3]]*h[3]).reshape(-1)
        y = z - hx  # (B,)

        # innovation gate (firmware ekf_of_update_one): skip when y^2 > gate^2 * S
        gate = np.broadcast_to(np.asarray(gate, dtype=self.dtype), y.shape)
        rejected = (gate > 0) & (y * y > gate * gate * S)
        if rejected.any():
            keep = (~rejected).astype(self.dtype)
            mask = keep if mask is None else mask * keep
        self._last_rejected = rejected

        K = PHt / S[:, None, None]  # (B, 4, 1)
        
        if mask is not None:
            K = K * mask[:, None, None]
            
        dx = K.reshape(-1, 4) * y[:, None]
        self.x[:, idx[0]] += dx[:, 0]
        self.x[:, idx[1]] += dx[:, 1]
        self.x[:, idx[2]] += dx[:, 2]
        self.x[:, idx[3]] += dx[:, 3]
        
        # Joseph form: P = (I - K H) P (I - K H).T + K R K.T
        # Implemented as P = P - K PHt.T - PHt K.T + K S K.T
        K_PHt_T = np.matmul(K, np.transpose(PHt, (0, 2, 1)))
        PHt_K_T = np.matmul(PHt, np.transpose(K, (0, 2, 1)))
        K_S_K_T = np.matmul(K * S[:, None, None], np.transpose(K, (0, 2, 1)))
        
        dP = -K_PHt_T - PHt_K_T + K_S_K_T
        if mask is not None:
            dP = dP * mask[:, None, None]
            
        self.P[:, axis] = P + dP
        # enforce symmetry
        self.P[:, axis] = 0.5 * (self.P[:, axis] + np.transpose(self.P[:, axis], (0, 2, 1)))
        
        return y, S

    def _update_of_axis(self, axis, z, mask, live):
        # firmware ekf_of_update_axis: REJ_RELEASE consecutive rejections reset P_vv and re-apply
        h = [0.0, 1.0, 1.0, 0.0]
        y, S = self._update_one(axis, h, z, self.R_of, mask, self.of_gate)
        rej = self._last_rejected & live
        run = self.rej_run[axis]
        run[:] = np.where(rej, run + 1, np.where(live, 0, run))
        release = run >= REJ_RELEASE
        if release.any():
            run[release] = 0
            self.P[release, axis, 1, :] = 0.0
            self.P[release, axis, :, 1] = 0.0
            self.P[release, axis, 1, 1] = VEL_P0
            self._update_one(axis, h, z, self.R_of, release.astype(self.dtype), 0.0)
        return y, S, rej

    def update_of(self, of_x, of_y, mask=None):
        live = np.ones(self.B, dtype=bool) if mask is None else np.asarray(mask) > 0
        y_x, S_x, rx = self._update_of_axis(0, of_x, mask, live)
        y_y, S_y, ry = self._update_of_axis(1, of_y, mask, live)
        self.rej_x += rx & live
        self.rej_y += ry & live
        self.innov_x = y_x
        self.innov_y = y_y
        return (y_x, y_y), (S_x, S_y)

    def update_raw(self, of1_x, of1_y, mask=None):
        # firmware EkfOf_UpdateRaw: of1 is bias-free velocity, h=[0,1,0,0], gated; R_of1 <= 0 skips it
        on = (np.broadcast_to(self.R_of1, (self.B,)) > 0).astype(self.dtype)
        mask = on if mask is None else np.asarray(mask, dtype=self.dtype) * on
        if not mask.any():
            return
        R = np.where(self.R_of1 > 0, self.R_of1, 1.0)
        h = [0.0, 1.0, 0.0, 0.0]
        self._update_one(0, h, of1_x, R, mask, self.of1_gate)
        self._update_one(1, h, of1_y, R, mask, self.of1_gate)

    def update_zero_vel(self, mask=None):
        h = [0.0, 1.0, 0.0, 0.0]
        self._update_one(0, h, 0.0, self.R_zupt, mask)
        self._update_one(1, h, 0.0, self.R_zupt, mask)

    def reset_pos(self):
        self.x[:, 0] = 0.0
        self.x[:, 3] = 0.0

    def reset_bias(self, var):
        # firmware EkfOf_ResetBias: bof = 0, variance var, no cross-covariance
        self.x[:, 2] = 0.0
        self.x[:, 5] = 0.0
        self.P[:, :, 2, :] = 0.0
        self.P[:, :, :, 2] = 0.0
        self.P[:, :, 2, 2] = var

class OldEkfOf6:
    def __init__(self, batch_size, dtype=np.float64):
        self.B = batch_size
        self.dtype = dtype
        self.x = np.zeros((self.B, 6), dtype=dtype)
        self.P = np.zeros((self.B, 6, 6), dtype=dtype)
        
        self.P[:, 0, 0] = 1.0
        self.P[:, 1, 1] = 0.1
        self.P[:, 2, 2] = 0.01
        self.P[:, 3, 3] = 1.0
        self.P[:, 4, 4] = 0.1
        self.P[:, 5, 5] = 0.01
        
        self.Q_pos = 1e-6
        self.Q_vel = 2e-4
        self.Q_bias = 5e-5
        self.R_of = 6.16e-4

    def predict(self, dt, ax=None, ay=None):
        self.x[:, 0] += self.x[:, 1] * dt
        self.x[:, 3] += self.x[:, 4] * dt
        
        for i in range(self.B):
            P01 = self.P[i, 0, 1]
            P11 = self.P[i, 1, 1]
            self.P[i, 0, 0] += 2.0 * dt * P01 + dt * dt * P11 + self.Q_pos * dt
            self.P[i, 0, 1] += dt * P11
            self.P[i, 0, 2] += dt * self.P[i, 1, 2]
            self.P[i, 1, 1] += self.Q_vel * dt
            self.P[i, 2, 2] += self.Q_bias * dt
            
            self.P[i, 1, 0] = self.P[i, 0, 1]
            self.P[i, 2, 0] = self.P[i, 0, 2]
            self.P[i, 2, 1] = self.P[i, 1, 2]
            
            P34 = self.P[i, 3, 4]
            P44 = self.P[i, 4, 4]
            self.P[i, 3, 3] += 2.0 * dt * P34 + dt * dt * P44 + self.Q_pos * dt
            self.P[i, 3, 4] += dt * P44
            self.P[i, 3, 5] += dt * self.P[i, 4, 5]
            self.P[i, 4, 4] += self.Q_vel * dt
            self.P[i, 5, 5] += self.Q_bias * dt
            
            self.P[i, 4, 3] = self.P[i, 3, 4]
            self.P[i, 5, 3] = self.P[i, 3, 5]
            self.P[i, 5, 4] = self.P[i, 4, 5]

    def _update_one(self, vel_idx, bias_idx, of_meas, mask):
        y = of_meas - (self.x[:, vel_idx] - self.x[:, bias_idx])
        S = self.P[:, vel_idx, vel_idx] - 2.0 * self.P[:, vel_idx, bias_idx] + self.P[:, bias_idx, bias_idx] + self.R_of
        S = np.maximum(S, 1e-8)
        
        PHt = self.P[:, :, vel_idx] - self.P[:, :, bias_idx]
        K = PHt / S[:, None]
        
        if mask is not None:
            K = K * mask[:, None]
            
        self.x += K * y[:, None]
        
        K_PHt_T = K[:, :, None] * PHt[:, None, :]
        PHt_K_T = PHt[:, :, None] * K[:, None, :]
        K_S_K_T = (K * S[:, None])[:, :, None] * K[:, None, :]
        
        dP = -K_PHt_T - PHt_K_T + K_S_K_T
        if mask is not None:
            dP = dP * mask[:, None, None]
            
        self.P += dP
        self.P = 0.5 * (self.P + np.transpose(self.P, (0, 2, 1)))
        
        return y, S

    def update_of(self, of_x, of_y, mask=None):
        y_x, S_x = self._update_one(1, 2, of_x, mask)
        y_y, S_y = self._update_one(4, 5, of_y, mask)
        self.innov_x = y_x
        self.innov_y = y_y
        return (y_x, y_y), (S_x, S_y)

    def update_zero_vel(self, mask=None):
        pass

    def reset_pos(self):
        self.x[:, 0] = 0.0
        self.x[:, 3] = 0.0
