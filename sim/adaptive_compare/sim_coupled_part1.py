import numpy as np
from scipy.signal import lfilter

# Physical params
M_mass = 1.5
Jx = 0.0023
Jy = 0.0023
Jz = 0.0015
g = 9.81
HOVER_PWM = 2950
IDLE_PWM = 2000
MAX_PWM = 4000

# PWM to thrust/torque
c_T = (M_mass * g) / (4 * (HOVER_PWM - IDLE_PWM))
c_PR = 1.0 / (4 * 3162.16)
c_Y = 1.0 / (4 * 5059.46)

# PID configs
ANG_PR = dict(Kp=2.6, Ki=0.1, Kd=9.5, Umax=200, Upmax=200, Uimax=10, Udmax=10, SumEmax=120, EMin=3)
RATE_PR = dict(Kp=5, Ki=0.01, Kd=10, Umax=300, Upmax=300, Uimax=20, Udmax=100, SumEmax=1000, EMin=2)
ANG_Y = dict(Kp=6.5, Ki=0.04, Kd=1.5, Umax=160, Upmax=160, Uimax=2, Udmax=10, SumEmax=50, EMin=2)
RATE_Y = dict(Kp=4.0, Ki=0.005, Kd=2.0, Umax=650, Upmax=650, Uimax=500, Udmax=10, SumEmax=100000, EMin=1000)
POS_Z = dict(Kp=0.7, Ki=0.005, Kd=0.1, Umax=1.0, Upmax=0.9, Uimax=0.3, Udmax=0.3, SumEmax=30, EMin=0.3)
RATE_Z = dict(Kp=400, Ki=0.435, Kd=1.5, Umax=300, Upmax=300, Uimax=60, Udmax=60, SumEmax=30, EMin=0.1)

DT_C, DT_P = 0.005, 0.001
SUB = int(round(DT_C / DT_P))
TAU_M = 1.0 / 19.8
DELAY = 0.015

LAM = 4.0
SIGMA = 0.01
OMEGA_U = 25.0
TH_MAX = 3.0
U_SCALE = 300.0
P_N, PHI_N, UN_N, ACC_N = 5.0, 0.5, 300.0, 50.0

CONTROLLERS = ['PID', 'S6', 'S10', 'RBF6', 'RBF12', 'RBF24']
RBF_GRID = {'RBF6': (3, 2), 'RBF12': (4, 3), 'RBF24': (6, 4)}

def n_features(ctrl):
    if ctrl == 'PID': return 0
    if ctrl == 'S6': return 6
    if ctrl == 'S10': return 10
    a, b = RBF_GRID[ctrl]
    return a * b + 2

class PID:
    def __init__(self, g, B):
        self.g = g
        self.SumE = np.zeros(B)
        self.PreE = np.zeros(B)
        self.U = np.zeros(B)

    def step(self, E):
        g = self.g
        cond = (((self.U <= g['Umax']) & (E > 0)) | ((self.U >= -g['Umax']) & (E < 0))) & (np.abs(E) < g['EMin'])
        self.SumE = np.clip(np.where(cond, self.SumE + E, self.SumE), -g['SumEmax'], g['SumEmax'])
        Ui = np.clip(g['Ki'] * self.SumE, -g['Uimax'], g['Uimax'])
        Up = np.clip(g['Kp'] * E, -g['Upmax'], g['Upmax'])
        Ud = np.clip(g['Kd'] * (E - self.PreE), -g['Udmax'], g['Udmax'])
        self.U = np.clip(Up + Ui + Ud, -g['Umax'], g['Umax'])
        self.PreE = E
        return self.U

def rbf_centres(ctrl):
    a, b = RBF_GRID[ctrl]
    c1, c2 = np.linspace(-1, 1, a), np.linspace(-1, 1, b)
    w1 = c1[1] - c1[0]
    w2 = c2[1] - c2[0]
    C1, C2 = np.meshgrid(c1, c2, indexing='ij')
    return C1.ravel(), C2.ravel(), w1, w2

def features(ctrl, pr, phr, un, pmr, qr, accr, rbf):
    one = np.ones_like(pr)
    if ctrl in ('S6', 'S10'):
        f = [one, pr, pr * np.tanh(pr), qr, un, pmr]
        if ctrl == 'S10':
            f += [np.sin(phr * PHI_N), np.abs(pr) * un, un * np.abs(un), accr]
        return np.stack(f, 1)
    c1, c2, w1, w2 = rbf
    g = np.exp(-0.5 * ((pr[:, None] - c1) / w1) ** 2 - 0.5 * ((phr[:, None] - c2) / w2) ** 2)
    return np.concatenate([g, un[:, None], pmr[:, None]], 1)

class MRAC:
    def __init__(self, ctrl, B):
        self.ctrl = ctrl
        self.nf = n_features(ctrl)
        self.rbf = rbf_centres(ctrl) if ctrl.startswith('RBF') else None
        self.Th = np.zeros((B, self.nf))
        self.u_ad = np.zeros(B)
        self.acc_f = np.zeros(B)
        self.p_prev = np.zeros(B)
        
    def step(self, pm_meas, phm_meas, U_pid, pmk, phmk, q, r, gamma):
        if self.nf == 0:
            return np.zeros_like(pm_meas)
        self.acc_f += DT_C * 2 * np.pi * 10 * ((pm_meas - self.p_prev) / DT_C - self.acc_f)
        Phi = features(self.ctrl, pm_meas / P_N, phm_meas / PHI_N, U_pid / UN_N,
                       np.full_like(pm_meas, pmk / P_N), q * r / 0.2, self.acc_f / ACC_N, self.rbf)
        s = (pm_meas - pmk) + LAM * (phm_meas - phmk)
        den = 1.0 + (Phi * Phi).sum(1)
        self.Th += DT_C * (gamma[:, None] * Phi * (s / den)[:, None] - SIGMA * self.Th)
        nrm = np.sqrt((self.Th * self.Th).sum(1))
        self.Th *= np.minimum(1.0, TH_MAX / (nrm + 1e-12))[:, None]
        raw = -U_SCALE * (self.Th * Phi).sum(1)
        self.u_ad += DT_C * OMEGA_U * (raw - self.u_ad)
        self.p_prev = pm_meas
        return self.u_ad

def make_disturbance(B, n_p, gust_amp, seed):
    rng = np.random.default_rng(seed)
    w = rng.standard_normal((n_p, B))
    a = np.exp(-2 * np.pi * 2.0 * DT_P)
    g = lfilter([1 - a], [1, -a], w, axis=0)
    g /= g[2000:].std(0, keepdims=True) + 1e-12
    return g * gust_amp[None, :]

def reference_model(t_c, cmd_r, cmd_p, cmd_y, cmd_z):
    B = 1
    ang_r, rate_r = PID(ANG_PR, B), PID(RATE_PR, B)
    ang_p, rate_p = PID(ANG_PR, B), PID(RATE_PR, B)
    ang_y, rate_y = PID(ANG_Y, B), PID(RATE_Y, B)
    pos_z, rate_z = PID(POS_Z, B), PID(RATE_Z, B)

    n_c = len(t_c)
    n_p = n_c * SUB
    
    phi, theta, psi, z = np.zeros(B), np.zeros(B), np.zeros(B), np.zeros(B)
    p, q, r, vz = np.zeros(B), np.zeros(B), np.zeros(B), np.zeros(B)
    
    L = {k: np.zeros((n_c, B)) for k in ('phi', 'theta', 'psi', 'z', 'p', 'q', 'r', 'vz')}
    
    for k in range(n_c):
        rate_des_r = ang_r.step(cmd_r[k] - np.rad2deg(phi))
        U_pid_r = rate_r.step(rate_des_r - np.rad2deg(p))
        rate_des_p = ang_p.step(cmd_p[k] - np.rad2deg(theta))
        U_pid_p = rate_p.step(rate_des_p - np.rad2deg(q))
        rate_des_y = ang_y.step(cmd_y[k] - np.rad2deg(psi))
        U_pid_y = rate_y.step(rate_des_y - np.rad2deg(r))
        
        vz_des = pos_z.step(cmd_z[k] - z)
        U_pid_z = rate_z.step(vz_des - vz)
        
        tau_x = U_pid_r / 3162.16
        tau_y = U_pid_p / 3162.16
        tau_z = U_pid_y / 5059.46
        F_z = U_pid_z / 222.0 + M_mass * g
        
        for j in range(SUB):
            p += DT_P * (tau_x - (Jz - Jy) * q * r) / Jx
            q += DT_P * (tau_y - (Jx - Jz) * p * r) / Jy
            r += DT_P * (tau_z - (Jy - Jx) * p * q) / Jz
            vz += DT_P * (F_z - M_mass * g) / M_mass
            phi += DT_P * p
            theta += DT_P * q
            psi += DT_P * r
            z += DT_P * vz
            
        L['phi'][k], L['theta'][k], L['psi'][k], L['z'][k] = phi, theta, psi, z
        L['p'][k], L['q'][k], L['r'][k], L['vz'][k] = p, q, r, vz
        
    return L
