"""bench_v1 plant: batched 6-DOF quadrotor, firmware sensor/estimator chain and mixer.

Every controller sees only `obs` (estimated states at 200 Hz, firmware units) and
returns rate-loop-level outputs U=(roll, pitch, yaw) [firmware U units] plus an
absolute throttle PWM.  The plant applies the firmware mixer, PWM clamps, a 15 ms
command delay, motor lag, battery sag and per-motor mismatch.  Scoring uses the
true states returned by `run`.

Sources (see .agent-ops/out/night/STATE.md): mass/inertia/arm/thrust max from the
PREV mujoco model; hover PWM 3090 and yaw imbalance from logs (calib_v1.json);
b_roll = B_RP 8 dps2/U: replay near-best set (calib_replay_v1) without a 3-8 Hz limit cycle (calib_check_v1);
b_yaw 7.55 dps2/U from sim_coupled; filters, gains and rates from fw_inventory.md.
Frame: ZYX Euler, z up; +U_roll -> +p, +U_pitch -> +q (+x accel), +U_yaw -> +r.
"""
import numpy as np

G = 9.81
MASS = 1.2961
J0 = np.array([0.00839, 0.0093, 0.01485])
ARM = 0.2 / np.sqrt(2.0)                       # X frame moment arm [m]
PWM_MIN, PWM_MAX = 2000.0, 4000.0
V_NOM, HOVER_PWM, T_MAX = 15.4, 3090.0, 8.37
X_H, X_M = HOVER_PWM - PWM_MIN, PWM_MAX - PWM_MIN
U_IMB_NOM = 430.0                              # logged hover yaw U: motor pairs at X_H -/+ 430
X2_H = X_H ** 2 + U_IMB_NOM ** 2               # mean x^2 over the four motors in logged hover
A2 = (T_MAX - MASS * G / 4 * X_M / X_H) / (X_M ** 2 - X_M * X2_H / X_H)   # T = A1 x + A2 x^2
A1 = (MASS * G / 4 - A2 * X2_H) / X_H
DTDX_H = A1 + 2 * A2 * X_H
B_YAW = np.deg2rad(7.55)                       # rad/s^2 per U
KAPPA = B_YAW * J0[2] / (4 * DTDX_H)           # yaw torque per N of pair thrust difference
B_RP = 8.0                                     # dps2/U roll: largest replay-grid b in the near-best NRMSE set without a 3-8 Hz limit cycle (calib_check.py)
J_RP_SCALE = np.rad2deg(4 * ARM * DTDX_H / J0[0]) / B_RP
J0 = J0 * np.array([J_RP_SCALE, J_RP_SCALE, 1.0])   # effective roll/pitch inertia incl. unmodelled lag
B_ROLL_DPS = np.rad2deg(4 * ARM * DTDX_H / J0[0])
B_PITCH_DPS = np.rad2deg(4 * ARM * DTDX_H / J0[1])
TAU_M, DELAY_TICKS = 1.0 / 19.8, 3             # motor lag [s]; 15 ms = 3 control ticks
DT, DT_C, SUB = 0.001, 0.005, 5
DRAG_LIN, DRAG_ROT, R_PROP = 0.25, 0.002, 0.0635
KP_MAHONY = 0.5
TOF_EVERY, TOF_DELAY, OF_EVERY = 8, 4, 4       # ticks: ToF 25 Hz / 20 ms, OF 50 Hz
SIG_GYRO, SIG_ACC, SIG_OF, SIG_TOF = 12.0, 0.6, 0.04, 0.007   # 1 kHz dps, m/s2; OF m/s; ToF m
Q_KF = np.diag([1e-6, 2e-4, 5e-5])             # EkfOf Q (fw_inventory), per 5 ms step


class Controller:
    """Base class.  PARAMS = {name: (default, lo, hi, 'log'|'lin')} is what the tuner searches."""
    PARAMS = {}
    name = 'base'

    def __init__(self, B, params=None):
        self.B = B
        self.p = {k: v[0] for k, v in self.PARAMS.items()}
        if params:
            self.p.update(params)

    def step(self, obs):
        raise NotImplementedError


def ss_gain(n_pred, R, H, periods=4000):
    """Periodic steady-state Kalman gain for x=[p,v,b], input accel, measurement row H: the gain after `periods`
    Riccati periods. A period is a pure function of P, so once P repeats exactly (an ulp-level cycle, ~840 periods for
    the ToF gain) the last gain is read off the cycle: bit-identical to running every period, ~4x less import time."""
    F = np.array([[1, DT_C, -0.5 * DT_C ** 2], [0, 1, -DT_C], [0, 0, 1]])
    P = np.eye(3) * 1e-2
    seen, gains = {}, []
    for i in range(periods):
        for _ in range(n_pred):
            P = F @ P @ F.T + Q_KF
        S = H @ P @ H.T + R
        K = P @ H.T / S
        P = (np.eye(3) - K @ H) @ P
        gains.append(K[:, 0])
        j = seen.setdefault(P.tobytes(), i)
        if j != i:      # P_i == P_j: gains from j + 1 on repeat with period i - j
            return gains[j + 1 + (periods - 2 - j) % (i - j)]
    return gains[-1]


K_TOF = ss_gain(TOF_EVERY, SIG_TOF ** 2, np.array([[1.0, 0, 0]]))
K_OF = ss_gain(OF_EVERY, SIG_OF ** 2, np.array([[0, 1.0, 0]]))
# Butterworth IIRs as exact float64 literals of scipy.signal.butter (every bit checked by test_plant_filters_are_scipy_butter
# in sim/sil/test_sil.py): importing scipy.signal costs ~3 s, paid by every SIL/bench process when this module called
# butter at import.
BG_B = np.array([0.0028981946337214297, 0.00869458390116429, 0.00869458390116429, 0.0028981946337214297])
BG_A = np.array([1.0, -2.374094743709352, 1.929355669091215, -0.5320753683120918])  # butter(3, 50 / 500): gyro 50 Hz @ 1 kHz
BA_B = np.array([0.007820208033497191, 0.015640416066994383, 0.007820208033497191])
BA_A = np.array([1.0, -1.734725768809275, 0.7660066009432638])                       # butter(2, 30 / 500): accel 30 Hz @ 1 kHz


class IIR:
    """Direct-form-II-transposed IIR over the last axis of a (B, C) signal."""
    def __init__(self, b, a, shape, x0=0.0):
        self.b, self.a = b, a
        self._bf, self._af = [float(v) for v in b], [float(v) for v in a]   # same products, no numpy-scalar indexing
        self.z = np.zeros((len(b) - 1,) + shape)
        if np.any(x0):                              # start at steady state for input x0
            for _ in range(400):
                self(np.broadcast_to(x0, shape))

    def __call__(self, x):
        b, a, z = self._bf, self._af, self.z
        y = b[0] * x + z[0]
        n = len(z)
        for i in range(n - 1):                      # z[i] = b[i+1]*x + z[i+1] - a[i+1]*y, in place (same operations)
            zi = z[i]
            np.multiply(x, b[i + 1], out=zi)
            zi += z[i + 1]
            zi -= a[i + 1] * y
        np.multiply(x, b[n], out=z[n - 1])
        z[n - 1] -= a[n] * y
        return y


def rot(e):
    """Rows of R (body->world, ZYX) as columns b1,b2,b3 (each (B,3))."""
    sf, cf = np.sin(e[:, 0]), np.cos(e[:, 0])
    st, ct = np.sin(e[:, 1]), np.cos(e[:, 1])
    sp, cp = np.sin(e[:, 2]), np.cos(e[:, 2])
    b1 = np.stack([cp * ct, sp * ct, -st], 1)
    b2 = np.stack([cp * st * sf - sp * cf, sp * st * sf + cp * cf, ct * sf], 1)
    b3 = np.stack([cp * st * cf + sp * sf, sp * st * cf - cp * sf, ct * cf], 1)
    return b1, b2, b3


def euler_rates(e, w):
    sf, cf, tt, ct = np.sin(e[:, 0]), np.cos(e[:, 0]), np.tan(e[:, 1]), np.cos(e[:, 1])
    a = w[:, 1] * sf + w[:, 2] * cf
    return np.stack([w[:, 0] + a * tt, w[:, 1] * cf - w[:, 2] * sf, a / ct], 1)


def run(ctrl, ref, sp, seed=0, div_err=2.0, tile=1):
    """Simulate B rows.  ref: dict p (B,N,3) m, v, a, yaw (B,N) deg.  sp: scenario params
    (see scen.py).  tile>1: rows are `tile` stacked copies of one row set (population batch);
    noise is drawn for one copy and repeated, so every copy sees identical noise (CRN).
    Returns true-state log at 200 Hz and flags."""
    B, N = ref['p'].shape[0], ref['p'].shape[1]
    rng = np.random.default_rng(seed); B0 = B // tile

    def nrm(*s):
        x = rng.standard_normal((B0,) + s)
        return np.tile(x, (tile,) + (1,) * len(s))
    ns = sp['noise_scale']
    p = ref['p'][:, 0].copy(); v = np.zeros((B, 3)); e = np.zeros((B, 3))
    e[:, 2] = np.deg2rad(ref['yaw'][:, 0]); w = np.zeros((B, 3))
    m = sp['mass']; J = sp['J']
    x_h = (-A1 + np.sqrt(A1 ** 2 + 4 * A2 * m * G / 4 / (sp['V0'] / V_NOM) ** 2)) / (2 * A2)
    mot = np.repeat((PWM_MIN + x_h)[:, None], 4, 1)
    cmd_hist = [mot.copy() for _ in range(DELAY_TICKS + 1)]
    f0 = np.zeros((B, 3)); f0[:, 2] = G
    gyro_f = IIR(BG_B, BG_A, (B, 3)); acc_f = IIR(BA_B, BA_A, (B, 3), f0 + sp['acc_bias'])
    ehat = e.copy(); xkf = np.zeros((B, 3, 3)); xkf[:, :, 0] = p
    zhist = [p[:, 2].copy() for _ in range(TOF_DELAY + 1)]
    vhist = [v[:, :2].copy() for _ in range(17)]
    tz_imb = -sp['u_imb'] * B_YAW * J0[2]
    turb = np.zeros((B, 3))
    alive = np.ones(B, bool)
    L = dict(p=np.zeros((B, N, 3), np.float32), e=np.zeros((B, N, 3), np.float32),
             mot=np.zeros((B, N, 4), np.float32), phat=np.zeros((B, N, 3), np.float32),
             div_k=np.full(B, -1))
    g_meas = np.zeros((B, 3)); a_meas = f0 + sp['acc_bias']
    for k in range(N):
        t = k * DT_C
        # ---- observation (estimated only) and control at 200 Hz ----
        cthat = np.cos(ehat[:, 1]); cfhat = np.cos(ehat[:, 0])
        obs = dict(k=k, t=t, rpy=np.rad2deg(ehat), gyro=g_meas, acc=a_meas,
                   pos=np.stack([xkf[:, 0, 0], xkf[:, 1, 0], xkf[:, 2, 0]], 1),
                   vel=np.stack([xkf[:, 0, 1], xkf[:, 1, 1], xkf[:, 2, 1]], 1),
                   vbat=sp['V0'] - sp['vsag'] * t - sp['vstep'] * (t > sp['vstep_t']),
                   mot=cmd_hist[-1],
                   ref=dict(p=ref['p'][:, k], v=ref['v'][:, k], a=ref['a'][:, k], yaw=ref['yaw'][:, k]),
                   preview=lambda n, k=k: dict(p=ref['p'][:, min(k + n, N - 1)], v=ref['v'][:, min(k + n, N - 1)],
                                               a=ref['a'][:, min(k + n, N - 1)], yaw=ref['yaw'][:, min(k + n, N - 1)]))
        out = ctrl.step(obs)
        U = np.nan_to_num(out['U']); thr = np.clip(np.nan_to_num(out['thr'], nan=PWM_MIN), PWM_MIN, PWM_MAX)
        gx, gy, gz = U[:, 0], U[:, 1], U[:, 2]
        M = np.clip(np.stack([thr - gy - gx - gz, thr + gy + gx - gz, thr - gy + gx + gz, thr + gy - gx + gz], 1),
                    PWM_MIN, PWM_MAX)
        cmd_hist.append(M); cmd_hist.pop(0); Md = cmd_hist[0]
        # ---- per-tick environment ----
        V = obs['vbat']
        gain = sp['mgain'] * ((V / V_NOM) ** 2)[:, None]
        lost = (t >= sp['mloss_t'])
        gain[np.arange(B), sp['mloss_idx']] *= np.where(lost, sp['mloss_eff'], 1.0)
        ge = 1.0 / (1.0 - np.minimum((R_PROP / (4 * np.maximum(p[:, 2], 0.03))) ** 2, 0.25))
        gain *= ge[:, None]
        turb += DT_C / 1.0 * (-turb) + sp['dryden_sigma'][:, None] * np.sqrt(2 * DT_C / 1.0) * nrm(3)
        wind = sp['wind'] + turb
        for j in range(sp['gust_t'].shape[1]):
            tau = (t - sp['gust_t'][:, j]) / sp['gust_dur'][:, j]
            on = (tau >= 0) & (tau <= 1)
            wind = wind + (on * 0.5 * (1 - np.cos(2 * np.pi * np.clip(tau, 0, 1))))[:, None] * sp['gust_vec'][:, j]
        # ---- 1 kHz plant + IMU ----
        for s in range(SUB):
            mot += DT / TAU_M * (Md - mot)
            x = mot - PWM_MIN
            T = (A1 * x + A2 * x * x) * gain
            Ts = T.sum(1)
            tau = np.stack([ARM * (T[:, 1] + T[:, 2] - T[:, 0] - T[:, 3]) - sp['cog'][:, 1] * Ts,
                            ARM * (T[:, 1] + T[:, 3] - T[:, 0] - T[:, 2]) + sp['cog'][:, 0] * Ts,
                            KAPPA * (T[:, 2] + T[:, 3] - T[:, 0] - T[:, 1]) + tz_imb], 1)
            b1, b2, b3 = rot(e)
            fw = (Ts[:, None] * b3 - DRAG_LIN * (v - wind)) / m[:, None]
            v += DT * (fw - np.array([0, 0, G])) * alive[:, None]
            p += DT * v
            Jw = J * w
            wd = (tau - DRAG_ROT * w - np.cross(w, Jw)) / J
            w += DT * wd * alive[:, None]
            e += DT * euler_rates(e, w)
            fb = np.stack([(b1 * fw).sum(1), (b2 * fw).sum(1), (b3 * fw).sum(1)], 1)
            g_raw = np.rad2deg(w) + sp['gyro_bias'] + SIG_GYRO * ns[:, None] * nrm(3)
            a_raw = fb + sp['acc_bias'] + SIG_ACC * ns[:, None] * nrm(3)
            g_meas = gyro_f(g_raw); a_meas = acc_f(a_raw)
        # ---- attitude estimate (Mahony-lite, no magnetometer) ----
        gr = np.deg2rad(g_meas)
        ea = np.stack([np.arctan2(a_meas[:, 1], a_meas[:, 2]),
                       np.arctan2(-a_meas[:, 0], np.hypot(a_meas[:, 1], a_meas[:, 2]))], 1)
        ed = euler_rates(ehat, gr)
        ehat[:, :2] += DT_C * (ed[:, :2] + KP_MAHONY * (ea - ehat[:, :2]))
        ehat[:, 2] += DT_C * ed[:, 2]
        # ---- position/velocity KFs (EkfOf approximation, steady-state gains) ----
        h1, h2, h3 = rot(ehat)
        aw = h1 * a_meas[:, :1] + h2 * a_meas[:, 1:2] + h3 * a_meas[:, 2:3]
        aw[:, 2] -= G
        for ax in range(3):
            xa = xkf[:, ax]
            u = aw[:, ax] - xa[:, 2]
            xa[:, 0] += DT_C * xa[:, 1] + 0.5 * DT_C ** 2 * u
            xa[:, 1] += DT_C * u
        zhist.append(p[:, 2].copy()); zhist.pop(0)
        vhist.append(v[:, :2].copy()); vhist.pop(0)
        if k % TOF_EVERY == 0:
            ct, cf = np.cos(e[:, 1]), np.cos(e[:, 0])
            rng_m = zhist[0] / (ct * cf) + SIG_TOF * ns * nrm() + sp['tof_spike'][:, k // TOF_EVERY]
            ok = ~sp['tof_drop'][:, k // TOF_EVERY]
            inn = (rng_m * cthat * cfhat - xkf[:, 2, 0]) * ok
            xkf[:, 2] += inn[:, None] * K_TOF
        if k % OF_EVERY == 0:
            i = k // OF_EVERY
            ok = ~sp['of_drop'][:, i]
            vd = np.stack(vhist, 0)[-1 - sp['of_delay'], np.arange(B)]
            vm = vd * (1 + sp['of_scale'])[:, None] + SIG_OF * ns[:, None] * nrm(2)
            for ax in range(2):
                inn = (vm[:, ax] - xkf[:, ax, 1]) * ok
                xkf[:, ax] += inn[:, None] * K_OF
        # ---- divergence / log ----
        err = np.linalg.norm(p - ref['p'][:, k], axis=1)
        bad = alive & ((err > div_err) | (np.abs(e[:, :2]).max(1) > np.deg2rad(60)) | (p[:, 2] < 0.01)
                       | ~np.isfinite(err))
        L['div_k'][bad] = k
        alive &= ~bad
        v[~alive] = 0; w[~alive] = 0
        L['p'][:, k] = p; L['e'][:, k] = e; L['mot'][:, k] = M; L['phat'][:, k] = obs['pos']
    L['diverged'] = L['div_k'] >= 0
    return L
