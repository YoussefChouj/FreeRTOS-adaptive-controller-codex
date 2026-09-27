import numpy as np
from scipy.signal import lfilter

DT_C, DT_P = 0.005, 0.001
SUB = int(round(DT_C / DT_P))
T_END = 14.0

# ---- adaptive-layer constants ---------------------------------------------------------
SIGMA = 0.01         # sigma-modification (firmware)
P_N, PHI_N, UN_N, ACC_N = 5.0, 0.5, 300.0, 50.0   # feature normalisers

CONTROLLERS = ['PID', 'S6', 'S10', 'RBF6', 'RBF12', 'RBF24']
RBF_GRID = {'RBF6': (3, 2), 'RBF12': (4, 3), 'RBF24': (6, 4)}

AXES = {
    'pitch': dict(
        J0 = 0.0023,
        K_EFF = 1170.0 / 0.37,
        ANG = dict(Kp=2.6, Ki=0.1, Kd=9.5, Umax=200, Upmax=200, Uimax=10, Udmax=10, SumEmax=120, EMin=3),
        RATE = dict(Kp=5, Ki=0.01, Kd=10, Umax=300, Upmax=300, Uimax=20, Udmax=100, SumEmax=1000, EMin=2),
        OMEGA_U = 4.0, TH_MAX = 3.0, U_SCALE = 300.0, U_TOT_MAX = 500.0, LAM = 4.0,
        TAU_M = 1.0 / 19.8, DELAY = 0.015, wrap=False
    ),
    'yaw': dict(
        J0 = 0.0015,
        K_EFF = 1872.0 / 0.1,  # assumed G_EFF=0.1
        ANG = dict(Kp=6.5, Ki=0.04, Kd=1.5, Umax=160, Upmax=160, Uimax=2, Udmax=10, SumEmax=50, EMin=2),
        RATE = dict(Kp=4.0, Ki=0.005, Kd=2.0, Umax=650, Upmax=650, Uimax=500, Udmax=10, SumEmax=100000, EMin=1000),
        OMEGA_U = 4.0, TH_MAX = 3.0, U_SCALE = 300.0, U_TOT_MAX = 650.0, LAM = 4.0,
        TAU_M = 1.0 / 30.0, DELAY = 0.015, wrap=True
    ),
    'z': dict(
        J0 = 1.5,  # mass
        K_EFF = 222.0,
        ANG = dict(Kp=0.7, Ki=0.005, Kd=0.1, Umax=1.0, Upmax=0.9, Uimax=0.3, Udmax=0.3, SumEmax=30, EMin=0.3),
        RATE = dict(Kp=400, Ki=0.435, Kd=1.5, Umax=300, Upmax=300, Uimax=60, Udmax=60, SumEmax=30, EMin=0.1),
        OMEGA_U = 5.0, TH_MAX = 3.0, U_SCALE = 300.0, U_TOT_MAX = 300.0, LAM = 4.0,
        TAU_M = 0.05, DELAY = 0.015, wrap=False
    )
}

def n_features(ctrl):
    if ctrl == 'PID': return 0
    if ctrl == 'S6': return 6
    if ctrl == 'S10': return 10
    a, b = RBF_GRID[ctrl]
    return a * b + 2

class PID:
    def __init__(self, g, B, wrap=False, out_clamp=None):
        self.g = g
        self.B = B
        self.wrap = wrap
        self.out_clamp = out_clamp
        self.SumE = np.zeros(B)
        self.PreE = np.zeros(B)
        self.U = np.zeros(B)

    def step(self, E):
        if self.wrap:
            E = np.where(E >= 180, E - 360, E)
            E = np.where(E <= -180, E + 360, E)
            
        g = self.g
        cond = (((self.U <= g['Umax']) & (E > 0)) | ((self.U >= -g['Umax']) & (E < 0))) \
            & (np.abs(E) < g['EMin'])
        self.SumE = np.clip(np.where(cond, self.SumE + E, self.SumE), -g['SumEmax'], g['SumEmax'])
        Ui = np.clip(g['Ki'] * self.SumE, -g['Uimax'], g['Uimax'])
        Up = np.clip(g['Kp'] * E, -g['Upmax'], g['Upmax'])
        Ud = np.clip(g['Kd'] * (E - self.PreE), -g['Udmax'], g['Udmax'])
        self.U = np.clip(Up + Ui + Ud, -g['Umax'], g['Umax'])
        self.PreE = E
        out = self.U
        if self.out_clamp is not None:
            out = np.clip(out, -self.out_clamp, self.out_clamp)
        return out

def command(t, axis):
    c = np.zeros_like(t)
    if axis == 'pitch':
        c[(t >= 1) & (t < 3)] = 15.0
        c[(t >= 3) & (t < 5)] = -15.0
        m = (t >= 8) & (t < 12)
        c[m] = 10.0 * np.sin(2 * np.pi * 0.5 * (t[m] - 8))
    elif axis == 'yaw':
        c[(t >= 1) & (t < 3)] = 45.0
        c[(t >= 3) & (t < 5)] = -45.0
        c[(t >= 6) & (t < 7.5)] = 170.0
        c[(t >= 7.5) & (t < 9)] = -170.0
    elif axis == 'z':
        c[(t >= 1) & (t < 3)] = 1.0
        c[(t >= 3) & (t < 5)] = 0.5
        m = (t >= 8) & (t < 12)
        c[m] = 0.5 + 0.5 * np.sin(2 * np.pi * 0.5 * (t[m] - 8))
    return c

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

def make_disturbance(P, B, n_p, seed):
    rng = np.random.default_rng(seed)
    w = rng.standard_normal((n_p, B))
    a = np.exp(-2 * np.pi * 2.0 * DT_P)
    g = lfilter([1 - a], [1, -a], w, axis=0)
    g /= g[2000:].std(0, keepdims=True) + 1e-12
    return g * P['gust'][None, :]

def nominal_params(B):
    z = np.zeros(B)
    return dict(Jr=np.ones(B), bias0=z.copy(), bias_f=z.copy(), lam_f=np.ones(B),
                t_f=np.full(B, 99.0), drag=z.copy(), gust=z.copy(), noise_p=z.copy(),
                noise_phi=z.copy(), qr_amp=z.copy(), seed=0)

_REF = {}
def reference_model(t_c, axis):
    lg = simulate(axis, 'PID', nominal_params(1), np.zeros(1), log_ref=False)
    return lg['phi'][:, 0], lg['p'][:, 0]

def simulate(axis, ctrl, P, gamma, log_ref=True):
    B = len(P['Jr'])
    n_c = int(round(T_END / DT_C))
    n_p = n_c * SUB
    t_c = np.arange(n_c) * DT_C
    cmd = command(t_c, axis)
    
    if log_ref:
        k_ref = f'{axis}_ref'
        if k_ref not in _REF:
            _REF[k_ref] = reference_model(t_c, axis)
        phi_m, p_m = _REF[k_ref]
    else:
        phi_m = p_m = np.zeros(n_c)

    gust = make_disturbance(P, B, n_p, P['seed'])
    rng = np.random.default_rng(P['seed'] + 7)
    
    cfg = AXES[axis]
    if axis == 'z':
        n_pm = rng.standard_normal((n_c, B)) * P['noise_p'][None, :]
        n_phm = rng.standard_normal((n_c, B)) * P['noise_phi'][None, :]
    else:
        n_pm = rng.standard_normal((n_c, B)) * np.deg2rad(P['noise_p'])[None, :]
        n_phm = rng.standard_normal((n_c, B)) * np.deg2rad(P['noise_phi'])[None, :]
        
    Jt = cfg['J0'] * P['Jr']
    d_steps = int(round(cfg['DELAY'] / DT_P))

    out_clamp = 60.0 if axis == 'yaw' else None
    ang = PID(cfg['ANG'], B, wrap=cfg['wrap'], out_clamp=out_clamp)
    rate = PID(cfg['RATE'], B)
    
    nf = n_features(ctrl)
    rbf = rbf_centres(ctrl) if ctrl.startswith('RBF') else None
    Th = np.zeros((B, nf))
    u_ad = np.zeros(B)
    acc_f = np.zeros(B)
    p_prev = np.zeros(B)

    phi = np.zeros(B)
    p = np.zeros(B)
    tau = np.zeros(B)
    buf = np.zeros((d_steps + 1, B))
    bi = 0

    L = {k: np.zeros((n_c, B)) for k in ('phi', 'p', 'U_pid', 'u_ad', 'd_eq', 'th_norm')}
    diverged = np.zeros(B, bool)
    
    for k in range(n_c):
        tk = t_c[k]
        pm_meas = p + n_pm[k]
        phm_meas = phi + n_phm[k]
        
        if axis == 'z':
            rate_des = ang.step(cmd[k] - phm_meas)
            U_pid = rate.step(rate_des - pm_meas)
        else:
            rate_des = ang.step(cmd[k] - np.rad2deg(phm_meas))
            U_pid = rate.step(rate_des - np.rad2deg(pm_meas))

        if nf:
            acc_f += DT_C * 2 * np.pi * 10 * ((pm_meas - p_prev) / DT_C - acc_f)
            q = P['qr_amp'] * np.sin(2 * np.pi * 0.3 * tk)
            r = P['qr_amp'] * 0.35
            if axis == 'z':
                pmk, phmk = p_m[k], phi_m[k]
            else:
                pmk, phmk = np.deg2rad(p_m[k]), np.deg2rad(phi_m[k])
                
            Phi = features(ctrl, pm_meas / P_N, phm_meas / PHI_N, U_pid / UN_N,
                           np.full(B, pmk / P_N), q * r / 0.2, acc_f / ACC_N, rbf)
            
            if axis == 'yaw' and cfg['wrap']:
                err_phi = phm_meas - phmk
                err_phi = np.where(err_phi >= np.pi, err_phi - 2*np.pi, err_phi)
                err_phi = np.where(err_phi <= -np.pi, err_phi + 2*np.pi, err_phi)
                s = (pm_meas - pmk) + cfg['LAM'] * err_phi
            else:
                s = (pm_meas - pmk) + cfg['LAM'] * (phm_meas - phmk)
                
            den = 1.0 + (Phi * Phi).sum(1)
            Th += DT_C * (gamma[:, None] * Phi * (s / den)[:, None] - SIGMA * Th)
            nrm = np.sqrt((Th * Th).sum(1))
            Th *= np.minimum(1.0, cfg['TH_MAX'] / (nrm + 1e-12))[:, None]
            raw = -cfg['U_SCALE'] * (Th * Phi).sum(1)
            u_ad += DT_C * cfg['OMEGA_U'] * (raw - u_ad)
            L['th_norm'][k] = np.minimum(nrm, cfg['TH_MAX'])
            
        p_prev = pm_meas
        U_tot = np.clip(U_pid + u_ad, -cfg['U_TOT_MAX'], cfg['U_TOT_MAX'])
        tau_cmd = U_tot / cfg['K_EFF']

        faulted = tk >= P['t_f']
        lam = np.where(faulted, P['lam_f'], P.get('lam0', 1.0))
        bias = P['bias0'] + np.where(faulted, P['bias_f'], 0.0)
        
        for j in range(SUB):
            buf[bi] = tau_cmd
            bi = (bi + 1) % (d_steps + 1)
            tau_del = buf[bi]
            tau += DT_P / cfg['TAU_M'] * (lam * tau_del - tau)
            ip = k * SUB + j
            
            if axis == 'z':
                actual_thrust_N = lam * (cfg['J0'] * 9.81 + tau) + bias
                dist = gust[ip] - P['drag'] * p * np.abs(p)
                p += DT_P * ((actual_thrust_N - Jt * 9.81) / Jt + dist / Jt)
                phi += DT_P * p
            else:
                dist = bias + gust[ip] - P['drag'] * p * np.abs(p)
                p += DT_P * (tau + dist) / Jt
                phi += DT_P * p
                
        if axis == 'z':
            bad = np.abs(phi) > 10.0
            diverged |= bad
            phi = np.where(bad, np.sign(phi) * 10.0, phi)
            p = np.where(bad, 0.0, p)
            L['phi'][k] = phi
            L['p'][k] = p
        else:
            bad = np.abs(phi) > np.deg2rad(180)
            diverged |= bad
            phi = np.where(bad, np.sign(phi) * np.deg2rad(180), phi)
            p = np.where(bad, 0.0, p)
            if axis == 'yaw' and cfg['wrap']:
                phi_log = np.rad2deg(phi)
                phi_log = np.where(phi_log > 180, phi_log - 360, phi_log)
                phi_log = np.where(phi_log < -180, phi_log + 360, phi_log)
                L['phi'][k] = phi_log
            else:
                L['phi'][k] = np.rad2deg(phi)
            L['p'][k] = np.rad2deg(p)
            
        L['U_pid'][k] = U_pid
        L['u_ad'][k] = u_ad
        
        if axis == 'z':
            L['d_eq'][k] = cfg['K_EFF'] * (lam*(cfg['J0']*9.81 + tau_cmd) + bias + gust[k * SUB] - Jt*9.81 - tau_cmd)
        else:
            L['d_eq'][k] = cfg['K_EFF'] * ((cfg['J0'] / Jt) * (lam * tau_cmd + bias + gust[k * SUB]) - tau_cmd)
            
    L['t'] = t_c
    L['cmd'] = cmd
    L['phi_m'] = phi_m
    L['p_m'] = p_m
    L['diverged'] = diverged
    return L

def metrics(L, axis, t0=0.5):
    m = L['t'] >= t0
    e = L['phi'][m] - L['phi_m'][m, None]
    ec = L['phi'][m] - L['cmd'][m, None]
    
    if axis == 'yaw' and AXES[axis]['wrap']:
        e = np.where(e >= 180, e - 360, e)
        e = np.where(e <= -180, e + 360, e)
        ec = np.where(ec >= 180, ec - 360, ec)
        ec = np.where(ec <= -180, ec + 360, ec)
        
    u = L['U_pid'][m] + L['u_ad'][m]
    out = dict(rms_ref=np.sqrt((e ** 2).mean(0)), peak_ref=np.abs(e).max(0),
               rms_cmd=np.sqrt((ec ** 2).mean(0)), effort=np.sqrt((u ** 2).mean(0)),
               du=np.sqrt((np.diff(u, axis=0) ** 2).mean(0)))
    for k in out:
        out[k] = np.where(L['diverged'], np.inf, out[k])
    return out
