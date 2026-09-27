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

def nominal_params(B):
    z = np.zeros(B)
    return dict(Jr=np.ones(B), t_f=np.full(B, 99.0), lam_f=np.ones(B),
                bias_r=z.copy(), bias_p=z.copy(), bias_y=z.copy(),
                gust_r=z.copy(), gust_p=z.copy(), gust_y=z.copy(), drag=z.copy(),
                noise_p=z.copy(), noise_phi=z.copy(), noise_z=z.copy(), seed=0)

def reference_model(t_c, cmd_r, cmd_p, cmd_y, cmd_z):
    """Nominal firmware PID on the same nonlinear plant (mixer, lag, delay, clamp),
    no disturbance: the trajectory the adaptive layer should restore. MRAC works in rad."""
    n_c = len(t_c)
    dummy = {k: np.zeros((n_c, 1)) for k in ('phi', 'theta', 'psi', 'z', 'p', 'q', 'r', 'vz')}
    lg = simulate('PID', nominal_params(1), np.zeros(1), t_c, cmd_r, cmd_p, cmd_y, cmd_z, dummy)
    # logs hold the state after step k; the controller at step k sees the state before it
    sh = lambda a: np.vstack([np.zeros((1, a.shape[1])), a[:-1]])
    L = {k: sh(np.deg2rad(lg[k])) for k in ('phi', 'theta', 'psi', 'p', 'q', 'r')}
    L['z'], L['vz'] = sh(lg['z']), sh(lg['vz'])
    return L
def simulate(ctrl, P, gamma, t_c, cmd_r, cmd_p, cmd_y, cmd_z, ref_log):
    B = len(P['Jr'])
    n_c = len(t_c)
    n_p = n_c * SUB
    
    phi_m = ref_log['phi'][:, 0]
    p_m = ref_log['p'][:, 0]
    theta_m = ref_log['theta'][:, 0]
    q_m = ref_log['q'][:, 0]
    psi_m = ref_log['psi'][:, 0]
    r_m = ref_log['r'][:, 0]
    z_m = ref_log['z'][:, 0]
    vz_m = ref_log['vz'][:, 0]

    gust_r = make_disturbance(B, n_p, P['gust_r'], P['seed'])
    gust_p = make_disturbance(B, n_p, P['gust_p'], P['seed']+1)
    gust_y = make_disturbance(B, n_p, P['gust_y'], P['seed']+2)
    
    rng = np.random.default_rng(P['seed'] + 7)
    noise = lambda a: rng.standard_normal((n_c, B)) * np.deg2rad(a)[None, :]
    n_pm, n_qm, n_rm = noise(P['noise_p']), noise(P['noise_p']), noise(P['noise_p'])
    n_phm, n_thm, n_psm = noise(P['noise_phi']), noise(P['noise_phi']), noise(P['noise_phi'])
    n_zm = rng.standard_normal((n_c, B)) * P['noise_z'][None, :]
    
    Jxt = Jx * P['Jr']
    Jyt = Jy * P['Jr']
    Jzt = Jz * P['Jr']
    d_steps = int(round(DELAY / DT_P))

    ang_r, rate_r = PID(ANG_PR, B), PID(RATE_PR, B)
    ang_p, rate_p = PID(ANG_PR, B), PID(RATE_PR, B)
    ang_y, rate_y = PID(ANG_Y, B), PID(RATE_Y, B)
    pos_z, rate_z = PID(POS_Z, B), PID(RATE_Z, B)
    
    mrac_r = MRAC(ctrl, B)
    mrac_p = MRAC(ctrl, B)
    mrac_y = MRAC(ctrl, B)
    
    phi, theta, psi, z = np.zeros(B), np.zeros(B), np.zeros(B), np.zeros(B)
    p, q, r, vz = np.zeros(B), np.zeros(B), np.zeros(B), np.zeros(B)
    
    buf1 = np.ones((d_steps + 1, B)) * HOVER_PWM
    buf2 = np.ones((d_steps + 1, B)) * HOVER_PWM
    buf3 = np.ones((d_steps + 1, B)) * HOVER_PWM
    buf4 = np.ones((d_steps + 1, B)) * HOVER_PWM
    bi = 0
    
    M1_act, M2_act, M3_act, M4_act = (np.ones(B) * HOVER_PWM for _ in range(4))

    L = {k: np.zeros((n_c, B)) for k in ('phi', 'theta', 'psi', 'z', 'p', 'q', 'r', 'vz', 
                                         'sat', 'M1', 'M2', 'M3', 'M4', 'U_pid_r', 'U_pid_y')}
    diverged = np.zeros(B, bool)
    
    for k in range(n_c):
        tk = t_c[k]
        phm_meas = phi + n_phm[k]
        thm_meas = theta + n_thm[k]
        psm_meas = psi + n_psm[k]
        pm_meas = p + n_pm[k]
        qm_meas = q + n_qm[k]
        rm_meas = r + n_rm[k]
        zm_meas = z + n_zm[k]
        
        U_pid_r = rate_r.step(ang_r.step(cmd_r[k] - np.rad2deg(phm_meas)) - np.rad2deg(pm_meas))
        U_pid_p = rate_p.step(ang_p.step(cmd_p[k] - np.rad2deg(thm_meas)) - np.rad2deg(qm_meas))
        U_pid_y = rate_y.step(ang_y.step(cmd_y[k] - np.rad2deg(psm_meas)) - np.rad2deg(rm_meas))
        U_pid_z = rate_z.step(pos_z.step(cmd_z[k] - zm_meas) - vz)
        
        u_ad_r = mrac_r.step(pm_meas, phm_meas, U_pid_r, p_m[k], phi_m[k], qm_meas, rm_meas, gamma)
        u_ad_p = mrac_p.step(qm_meas, thm_meas, U_pid_p, q_m[k], theta_m[k], pm_meas, rm_meas, gamma)
        u_ad_y = mrac_y.step(rm_meas, psm_meas, U_pid_y, r_m[k], psi_m[k], pm_meas, qm_meas, gamma)
        
        U_tot_r = np.clip(U_pid_r + u_ad_r, -500, 500)
        U_tot_p = np.clip(U_pid_p + u_ad_p, -500, 500)
        U_tot_y = np.clip(U_pid_y + u_ad_y, -650, 650)
        Thr_out = np.clip(U_pid_z + 2950, 2000, 4000)
        
        M1 = Thr_out + U_tot_p - U_tot_r - U_tot_y
        M2 = Thr_out - U_tot_p + U_tot_r - U_tot_y
        M3 = Thr_out + U_tot_p + U_tot_r + U_tot_y
        M4 = Thr_out - U_tot_p - U_tot_r + U_tot_y
        
        M1_c = np.clip(M1, 2000, 4000)
        M2_c = np.clip(M2, 2000, 4000)
        M3_c = np.clip(M3, 2000, 4000)
        M4_c = np.clip(M4, 2000, 4000)
        
        sat = (M1_c != M1) | (M2_c != M2) | (M3_c != M3) | (M4_c != M4)
        L['sat'][k] = sat
        
        faulted = tk >= P['t_f']
        lam = np.where(faulted, P['lam_f'], 1.0)
        
        for j in range(SUB):
            buf1[bi] = M1_c
            buf2[bi] = M2_c
            buf3[bi] = M3_c
            buf4[bi] = M4_c
            bi = (bi + 1) % (d_steps + 1)
            
            M1_act += DT_P / TAU_M * (buf1[bi] - M1_act)
            M2_act += DT_P / TAU_M * (buf2[bi] - M2_act)
            M3_act += DT_P / TAU_M * (buf3[bi] - M3_act)
            M4_act += DT_P / TAU_M * (buf4[bi] - M4_act)
            
            # Apply fault (e.g. M1 loses thrust)
            M1_eff = 2000 + (M1_act - 2000) * lam
            M2_eff = 2000 + (M2_act - 2000)
            M3_eff = 2000 + (M3_act - 2000)
            M4_eff = 2000 + (M4_act - 2000)
            
            tau_x = c_PR * (M2_eff + M3_eff - M1_eff - M4_eff) + gust_r[k * SUB + j] + P['bias_r']
            tau_y = c_PR * (M1_eff + M3_eff - M2_eff - M4_eff) + gust_p[k * SUB + j] + P['bias_p']
            tau_z = c_Y * (M3_eff + M4_eff - M1_eff - M2_eff) + gust_y[k * SUB + j] + P['bias_y']
            F_z = c_T * (M1_eff + M2_eff + M3_eff + M4_eff - 4*2000)
            
            # Drag
            tau_x -= P['drag'] * p * np.abs(p)
            tau_y -= P['drag'] * q * np.abs(q)
            tau_z -= P['drag'] * r * np.abs(r)
            
            p += DT_P * (tau_x - (Jzt - Jyt) * q * r) / Jxt
            q += DT_P * (tau_y - (Jxt - Jzt) * p * r) / Jyt
            r += DT_P * (tau_z - (Jyt - Jxt) * p * q) / Jzt
            vz += DT_P * (F_z - M_mass * g) / M_mass
            phi += DT_P * p
            theta += DT_P * q
            psi += DT_P * r
            z += DT_P * vz
            
        bad = (np.abs(phi) > np.deg2rad(120)) | (np.abs(theta) > np.deg2rad(120))
        diverged |= bad
        phi = np.where(bad, np.sign(phi) * np.deg2rad(120), phi)
        theta = np.where(bad, np.sign(theta) * np.deg2rad(120), theta)
        
        L['phi'][k] = np.rad2deg(phi)
        L['theta'][k] = np.rad2deg(theta)
        L['psi'][k] = np.rad2deg(psi)
        L['z'][k] = z
        L['p'][k] = np.rad2deg(p)
        L['q'][k] = np.rad2deg(q)
        L['r'][k] = np.rad2deg(r)
        L['vz'][k] = vz
        L['M1'][k] = M1_c
        L['M2'][k] = M2_c
        L['M3'][k] = M3_c
        L['M4'][k] = M4_c
        L['U_pid_r'][k] = U_pid_r
        L['U_pid_y'][k] = U_pid_y
        
    L['diverged'] = diverged
    return L
