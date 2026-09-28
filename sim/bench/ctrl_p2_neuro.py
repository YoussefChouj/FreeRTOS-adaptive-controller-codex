import numpy as np
import sys
import os

from ctrl_mrac import MRACBaseController, get_params

DT_C = 0.005
LAM = 4.0
OMEGA_U = 25.0
TH_MAX = 3.0
U_SCALE = 300.0
P_N, PHI_N, UN_N, ACC_N = 5.0, 0.5, 300.0, 50.0

def _get_params(gamma_val, extra=None):
    p = get_params(gamma_val)
    if extra:
        p.update(extra)
    return p

class CustomMRAC:
    def __init__(self, ctrl, B, p_dict):
        self.ctrl = ctrl
        self.B = B
        self.p_dict = p_dict
        
        self.acc_f = np.zeros(B)
        self.p_prev = np.zeros(B)
        self.u_ad = np.zeros(B)
        
        if ctrl.startswith('RBF'):
            if ctrl == 'RBF48':
                a, b = 8, 6
            elif ctrl == 'RBF96':
                a, b = 12, 8
            elif ctrl == 'RBF24':
                a, b = 6, 4
                
            c1, c2 = np.linspace(-1, 1, a), np.linspace(-1, 1, b)
            self.w1 = c1[1] - c1[0]
            self.w2 = c2[1] - c2[0]
            C1, C2 = np.meshgrid(c1, c2, indexing='ij')
            self.c1 = C1.ravel()
            self.c2 = C2.ravel()
            self.nf = a * b + 2
            self.Th = np.zeros((B, self.nf))
            
        elif ctrl == 'PhysRBF':
            # S6: 6 terms. RBF24 grid: 24 terms. Total: 30 terms.
            self.nf = 30
            c1, c2 = np.linspace(-1, 1, 6), np.linspace(-1, 1, 4)
            self.w1 = c1[1] - c1[0]
            self.w2 = c2[1] - c2[0]
            C1, C2 = np.meshgrid(c1, c2, indexing='ij')
            self.c1 = C1.ravel()
            self.c2 = C2.ravel()
            self.Th = np.zeros((B, self.nf))
            
        elif ctrl == 'Deep':
            # Phi = [MLP(pr, phr) (16), un, pmr]: same inputs and linear tail as the RBF regressor.
            self.nf = 18
            self.Th = np.zeros((B, self.nf))
            # 2-layer tanh MLP, one copy per batch member (rows must not share weights).
            # Identical fixed-seed init for every member; values are float32-representable.
            rng = np.random.default_rng(42)
            W1 = (rng.standard_normal((2, 16)) * 0.5).astype(np.float32)
            W2 = (rng.standard_normal((16, 16)) * 0.25).astype(np.float32)
            self.W1 = np.repeat(W1[None].astype(float), B, 0)
            self.b1 = np.zeros((B, 16))
            self.W2 = np.repeat(W2[None].astype(float), B, 0)
            self.b2 = np.zeros((B, 16))
            self.NB = 256
            self.buf_X = np.zeros((B, self.NB, 2))
            self.buf_Y = np.zeros((B, self.NB))
            self.buf_idx = 0
            self.buf_len = 0
            self.tick = 0
            
    def _mlp_forward(self, X):
        # X: (B, N, 2) -> h1, Phi: (B, N, 16), per-member weights
        h1 = np.tanh(np.einsum('bni,bij->bnj', X, self.W1) + self.b1[:, None])
        Phi = np.tanh(np.einsum('bnj,bjk->bnk', h1, self.W2) + self.b2[:, None])
        return Phi, h1

    def _sgd(self, mask, lr, n_steps=3):
        # DMRAC inner-layer step: fit Th . Phi(x_i) to the stored outer-layer output y_i,
        # with the outer layer Th frozen. Loss is in units of Th . Phi (raw / -U_SCALE).
        n = self.buf_len
        X, Y = self.buf_X[:, :n], self.buf_Y[:, :n]
        g = (mask * lr / n)[:, None, None]
        for _ in range(n_steps):
            Phi, h1 = self._mlp_forward(X)
            err = (Phi * self.Th[:, None, :16]).sum(2) - Y
            dz2 = err[:, :, None] * self.Th[:, None, :16] * (1 - Phi ** 2)
            dz1 = np.einsum('bnk,bjk->bnj', dz2, self.W2) * (1 - h1 ** 2)
            self.W2 -= g * np.einsum('bnj,bnk->bjk', h1, dz2)
            self.b2 -= g[:, 0] * dz2.sum(1)
            self.W1 -= g * np.einsum('bni,bnj->bij', X, dz1)
            self.b1 -= g[:, 0] * dz1.sum(1)
        
    def step(self, pm_meas, phm_meas, U_pid, pmk, phmk, q, r, gamma):
        B = self.B
        self.acc_f += DT_C * 2 * np.pi * 10 * ((pm_meas - self.p_prev) / DT_C - self.acc_f)
        
        pr = pm_meas / P_N
        phr = phm_meas / PHI_N
        un = U_pid / UN_N
        pmr = np.full_like(pm_meas, pmk / P_N)
        qr = q * r / 0.2
        accr = self.acc_f / ACC_N
        
        if self.ctrl.startswith('RBF'):
            g = np.exp(-0.5 * ((pr[:, None] - self.c1) / self.w1) ** 2 - 0.5 * ((phr[:, None] - self.c2) / self.w2) ** 2)
            Phi = np.concatenate([g, un[:, None], pmr[:, None]], axis=1)
            sigma = 0.01
            
        elif self.ctrl == 'PhysRBF':
            one = np.ones_like(pr)
            s6 = [one, pr, pr * np.tanh(pr), qr, un, pmr]
            s6_arr = np.stack(s6, axis=1)
            g = np.exp(-0.5 * ((pr[:, None] - self.c1) / self.w1) ** 2 - 0.5 * ((phr[:, None] - self.c2) / self.w2) ** 2)
            Phi = np.concatenate([s6_arr, g], axis=1)
            sigma = 0.01
            # gamma is handled below
            
        elif self.ctrl == 'Deep':
            X = np.stack([pr, phr], axis=1)
            Phi_n, _ = self._mlp_forward(X[:, None])
            Phi = np.concatenate([Phi_n[:, 0], un[:, None], pmr[:, None]], axis=1)
            sigma = self.p_dict.get('sigma', 0.01)
            if isinstance(sigma, np.ndarray):
                sigma = sigma[:, None]

        s = (pm_meas - pmk) + LAM * (phm_meas - phmk)
        den = 1.0 + (Phi * Phi).sum(1)
        
        if self.ctrl == 'PhysRBF':
            gamma_phys = self.p_dict['gamma_phys']
            gamma_rbf = self.p_dict['gamma_rbf']
            if not isinstance(gamma_phys, np.ndarray): gamma_phys = np.full(B, gamma_phys)
            if not isinstance(gamma_rbf, np.ndarray): gamma_rbf = np.full(B, gamma_rbf)
            gamma_arr = np.concatenate([np.tile(gamma_phys[:, None], (1, 6)), np.tile(gamma_rbf[:, None], (1, 24))], axis=1)
            self.Th += DT_C * (gamma_arr * Phi * (s / den)[:, None] - sigma * self.Th)
        else:
            if not isinstance(gamma, np.ndarray): gamma = np.full(B, gamma)
            self.Th += DT_C * (gamma[:, None] * Phi * (s / den)[:, None] - sigma * self.Th)
            
        nrm = np.sqrt((self.Th * self.Th).sum(1))
        self.Th *= np.minimum(1.0, TH_MAX / (nrm + 1e-12))[:, None]
        
        raw = -U_SCALE * (self.Th * Phi).sum(1)
        self.u_ad += DT_C * OMEGA_U * (raw - self.u_ad)
        self.p_prev = pm_meas
        
        if self.ctrl == 'Deep':
            self.tick += 1
            # ring buffer of (x, MLP part of the outer-layer output at storage time)
            self.buf_X[:, self.buf_idx] = X
            self.buf_Y[:, self.buf_idx] = (self.Th[:, :16] * Phi_n[:, 0]).sum(1)
            self.buf_idx = (self.buf_idx + 1) % self.NB
            self.buf_len = min(self.buf_len + 1, self.NB)
            N_upd = np.broadcast_to(np.round(self.p_dict.get('N_upd', 200)), (B,))
            mask = (self.tick % np.maximum(N_upd, 1) == 0).astype(float)
            if mask.any():
                self._sgd(mask, np.broadcast_to(self.p_dict.get('lr', 1e-3), (B,)))

        return self.u_ad

class MRAC_RBF48(MRACBaseController):
    """
    MRAC_RBF48: RBF grid of 48 centres (8x6).
    Source: Phase 2 Lit Review / Neuroadaptive Control.
    Flop estimate: ~3k flops per tick (2 axes x 48 Gaussians, exp counted as ~20 flops).
    """
    name = 'mrac_rbf48'
    ctrl_type = 'PID' # Dummy to pass super().__init__
    PARAMS = _get_params(0.1)
    
    def __init__(self, B, params=None):
        super().__init__(B, params)
        self.mrac_r = CustomMRAC('RBF48', B, self.p)
        self.mrac_p = CustomMRAC('RBF48', B, self.p)

class MRAC_RBF96(MRACBaseController):
    """
    MRAC_RBF96: RBF grid of 96 centres (12x8).
    Source: Phase 2 Lit Review / Neuroadaptive Control.
    Flop estimate: ~6k flops per tick (2 axes x 96 Gaussians, exp counted as ~20 flops).
    """
    name = 'mrac_rbf96'
    ctrl_type = 'PID' # Dummy to pass super().__init__
    PARAMS = _get_params(0.1)
    
    def __init__(self, B, params=None):
        super().__init__(B, params)
        self.mrac_r = CustomMRAC('RBF96', B, self.p)
        self.mrac_p = CustomMRAC('RBF96', B, self.p)

class MRAC_PhysRBF(MRACBaseController):
    """
    MRAC_PhysRBF: Composite regressor of S6 physics terms and RBF24 grid.
    Source: Phase 2 Lit Review / SINDy + Neuroadaptive Control.
    Flop estimate: ~1.7k flops per tick (2 axes x (24 Gaussians + 6 physics terms)).
    """
    name = 'mrac_physrbf'
    ctrl_type = 'PID' # Dummy to pass super().__init__
    PARAMS = dict(MRACBaseController.PARAMS)
    if 'ff_a' in PARAMS: del PARAMS['ff_a']
    PARAMS['gamma_phys'] = (0.3, 10**-1.5, 10**2, 'log')
    PARAMS['gamma_rbf'] = (0.1, 10**-1.5, 10**2, 'log')
    if 'gamma' in PARAMS: del PARAMS['gamma']
    
    def __init__(self, B, params=None):
        super().__init__(B, params)
        self.mrac_r = CustomMRAC('PhysRBF', B, self.p)
        self.mrac_p = CustomMRAC('PhysRBF', B, self.p)
        
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
        
        u_ad_r = self.mrac_r.step(pm_r, phm_r, u_nom[:, 0], p_r_ref, phi_r_ref, qm, rm, None)
        u_ad_p = self.mrac_p.step(pm_p, phm_p, u_nom[:, 1], p_p_ref, phi_p_ref, pm_r, rm, None)
        
        U = u_nom.copy()
        U[:, 0] += u_ad_r
        U[:, 1] += u_ad_p
        return U

class MRAC_Deep(MRACBaseController):
    """
    MRAC_Deep (DMRAC): Deep MRAC with 2-layer tanh MLP inner layers updated via SGD.
    Source: Deep Model Reference Adaptive Control (2019, Joshi & Chowdhary), arXiv:1909.08602
    Per member: 2 separate weight sets (roll, pitch); rows never share weights. Phi = [MLP(p, phi), u_nom, p_ref].
    Flop estimate: ~1.2k flops per tick (2 axes x (2x16 + 16x16 MLP + 18-term MRAC)); every N_upd ticks
    3 SGD passes over 256 samples ~ 3 x 256 x 3 x 16 x 16 x 2 = 1.2M flops (amortised ~6k/tick at N_upd=200).
    """
    name = 'mrac_deep'
    ctrl_type = 'PID' # Dummy to pass super().__init__
    PARAMS = dict(MRACBaseController.PARAMS)
    if 'ff_a' in PARAMS: del PARAMS['ff_a']
    PARAMS['gamma'] = (0.03, 1e-3, 10.0, 'log')
    PARAMS['lr'] = (0.1, 1e-4, 10.0, 'log')
    PARAMS['N_upd'] = (200, 10, 1000, 'lin')
    PARAMS['sigma'] = (0.01, 1e-4, 1.0, 'log')
    
    def __init__(self, B, params=None):
        super().__init__(B, params)
        self.mrac_r = CustomMRAC('Deep', B, self.p)
        self.mrac_p = CustomMRAC('Deep', B, self.p)

