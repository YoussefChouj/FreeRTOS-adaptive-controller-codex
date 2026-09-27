import sys, os
import numpy as np
import bench, plant, scen
from ctrl_mrac3l import MRAC3L, MRAC3L_Unrouted, MRAC3L_Reactive, MRAC3L_Predictive, MRAC3L_Both

def check_a():
    print("--- (a) Gate finds known tones ---")
    ctrl = MRAC3L_Both(1)
    t = np.arange(200) * 0.005
    for freq, expected_band in [(1.0, 1), (4.0, 2)]:
        sig = np.sin(2 * np.pi * freq * t)
        E = np.ones((2, 1, 4)) * 1e-6
        y = np.zeros((2, 1, 4))
        for k in range(200):
            s = np.zeros((2, 1))
            s[0, 0] = sig[k]
            E = ctrl._band_energies(s, y, E)
        logits = np.log(np.maximum(E, 1e-12)) / ctrl.p['T']
        logits -= np.max(logits, axis=-1, keepdims=True)
        ex = np.exp(logits)
        gate = ex / np.sum(ex, axis=-1, keepdims=True)
        band = np.argmax(gate[0, 0])
        print(f"Freq {freq} Hz -> Band {band}, Expected {expected_band}: {'PASS' if band == expected_band else 'FAIL'}")

def check_b():
    print("--- (b) Reference model vs plant (noise-free) ---")
    class MLog(MRAC3L_Unrouted):
        def __init__(self, B, p=None):
            super().__init__(B, p)
            self.p['gamma'] = 0.0
            self.log_rate_m = []
            self.log_w = []
        def controller_update(self, o, u_nom, wd):
            u_out = super().controller_update(o, u_nom, wd)
            self.log_rate_m.append(self.rate_m.copy())
            self.log_w.append(o['gyro'][:, :2].T.copy())
            return u_out
    
    ref, sp = scen.build([('zigzag_1.0', 'nominal', 0)])
    sp['noise_scale'] = np.array([0.0])
    sp['gyro_bias'] = np.array([[0.0, 0.0, 0.0]])
    sp['acc_bias'] = np.array([[0.0, 0.0, 0.0]])
    ctrl = MLog(1)
    L = plant.run(ctrl, ref, sp, seed=0)
    
    # rate_m logged is (2, 1) per step -> (N, 2)
    rate_m = np.array([x[:, 0] for x in ctrl.log_rate_m]) # (N, 2)
    
    # Calculate true rate from L['e']
    e = L['e'][0] # (N, 3) in rad
    dt = 0.005
    w_true = np.zeros((e.shape[0], 2))
    for k in range(1, e.shape[0]-1):
        edot = (e[k+1] - e[k-1]) / (2 * dt)
        phi, theta, psi = e[k]
        p = edot[0] - edot[2] * np.sin(theta)
        q = edot[1] * np.cos(phi) + edot[2] * np.sin(phi) * np.cos(theta)
        w_true[k] = np.rad2deg([p, q])
    w_true[0] = w_true[1]; w_true[-1] = w_true[-2]
    
    k0 = int(scen.T_HOLD / 0.005)
    rate_m_align = rate_m[k0:]
    w_true_align = w_true[k0:]
    
    rmse = np.sqrt(np.mean((rate_m_align - w_true_align)**2))
    rms_plant = np.sqrt(np.mean(w_true_align**2))
    pct = rmse / (rms_plant + 1e-6) * 100
    
    # Cross-correlation for lag
    lags = []
    for axis in range(2):
        cc = np.correlate(rate_m_align[:, axis], w_true_align[:, axis], mode='full')
        lag = np.argmax(cc) - (len(w_true_align) - 1)
        lags.append(lag * 0.005 * 1000) # ms
    
    mean_lag = np.mean(lags)
    print(f"RMSE = {pct:.1f}% of plant RMS (target <= 10%): {'PASS' if pct <= 10 else 'FAIL'}")
    print(f"Lag = {mean_lag:.1f} ms (target <= 50 ms): {'PASS' if abs(mean_lag) <= 50 else 'FAIL'}")

def check_c():
    print("--- (c) Boundedness (steps payload) ---")
    res = {}
    for M in [MRAC3L_Unrouted, MRAC3L_Reactive, MRAC3L_Predictive, MRAC3L_Both]:
        class MCheck(M):
            def __init__(self, B, p=None):
                super().__init__(B, p)
                self.max_norm = 0.0
            def controller_update(self, o, u_nom, wd):
                u_out = super().controller_update(o, u_nom, wd)
                nrm = np.sqrt(np.sum(self.W**2, axis=-1)).max()
                self.max_norm = max(self.max_norm, nrm)
                return u_out
        ctrl = MCheck(1)
        ref, sp = scen.build([('steps', 'payload', 1)])
        L = plant.run(ctrl, ref, sp, seed=0)
        print(f"{M.MODE} max norm: {ctrl.max_norm:.2f} <= 3.0: {'PASS' if ctrl.max_norm <= 3.01 else 'FAIL'}")

def check_d():
    print("--- (d) Predictive lead ---")
    class MLead(MRAC3L_Both):
        def __init__(self, B, p=None):
            super().__init__(B, p)
            self.p['horizon'] = 80.0
            self.log_g_R = []
            self.log_g_P = []
        def controller_update(self, o, u_nom, wd):
            self.log_g_R.append(self.E_R.copy())
            self.log_g_P.append(self.E_P.copy())
            return super().controller_update(o, u_nom, wd)
            
    ctrl = MLead(1)
    ref, sp = scen.build([('zigzag_1.0', 'nominal', 0)])
    L = plant.run(ctrl, ref, sp, seed=0)
    
    # Find when Band 2 (high freq) overtakes Band 1 in energy for the first corner
    # Zigzag has a sharp corner around 5.0 seconds
    g_R = np.array([x[0,0] for x in ctrl.log_g_R]) # (N, 4)
    g_P = np.array([x[0,0] for x in ctrl.log_g_P]) # (N, 4)
    
    # Find first index where band 2 > band 1 after T_HOLD
    k0 = int(scen.T_HOLD / 0.005)
    shift_R = shift_P = -1
    for k in range(k0, len(g_R)):
        if g_R[k, 1] > g_R[k, 0] and shift_R == -1: shift_R = k
        if g_P[k, 1] > g_P[k, 0] and shift_P == -1: shift_P = k
        if shift_R != -1 and shift_P != -1: break
    
    lead_ms = (shift_R - shift_P) * 5.0
    print(f"R shifts at {shift_R*0.005:.3f}s, P shifts at {shift_P*0.005:.3f}s")
    print(f"Lead = {lead_ms:.1f} ms (>0): {'PASS' if lead_ms > 0 else 'FAIL'}")

if __name__ == '__main__':
    check_a()
    check_b()
    check_c()
    check_d()
