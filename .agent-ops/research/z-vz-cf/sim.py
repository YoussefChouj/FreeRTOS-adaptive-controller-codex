"""S1 Simulation (Open-Loop Flight Replay + Closed-Loop Z Cascade Simulation) for z-vz-cf.

1. Open-loop estimator replay on smoothed real height from flight 20261024-213634.
2. Closed-loop 1-D vertical double-integrator + exact firmware Z_posPID -> Z_ratePID cascade:
   - Plant: dh/dt = vz, dvz/dt = b_u * U + d_ext, dt = 0.005 s (200 Hz).
   - Physical U-to-accel gain b_u = 4 * DTDX_H / MASS = 0.013868 (m/s^2)/CCR (from sim/bench/plant.py,
     cross-checked against HOVER_THR_FREE = 3050 CCR and flight 20261024-213634 regression 0.01487).
   - Sensor model: 20 Hz ToF (every 10 ticks = 50 ms) with 1 cm (0.01 m) quantization;
     IMU a_meas = a_true + bias + N(0, sigma^2), bias = 0.1 m/s^2, sigma in {0.3, 1.0, 3.0} m/s^2,
     clamped to [-AccMax, +AccMax] with AccMax = 20.0 m/s^2.
"""
import os
import shutil
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt
from zlog import load

# ---- Constants & Firmware Parameters (API/pid.c, TASK/StabilizerTask.c, sim/bench/plant.py) ----
DT = 0.005                  # STAB_DT_S = 0.005 s (200 Hz)
POS_LOOP_DIV = 2            # Z_posPID runs every 2nd tick (100 Hz)
TOF_DIV = 10                # 20 Hz ToF (every 10 ticks = 50 ms)
TOF_QUANT_M = 0.01          # 1 cm ToF quantization

# Physical U-to-vertical-acceleration gain b_u ((m/s^2) / CCR unit):
# Calibrated SIL/bench plant (sim/bench/plant.py): DTDX_H = 0.004493468 N/CCR per motor, MASS = 1.2961 kg
DTDX_H = 0.004493467973977695
MASS_KG = 1.2961
GRAVITY_MSS = 9.80665
HOVER_THR_FREE = 3050       # CCR (PWM_MIN = 2000 CCR -> 1050 CCR hover delta)
B_U = 4.0 * DTDX_H / MASS_KG  # 0.01386766 (m/s^2) / CCR

# Proposed CF parameters (PROPOSED)
K1_PROP = 0.21
K2_PROP = 0.45
K3_PROP = 0.10
ACC_MAX_PROP = 20.0


class FirmwarePID:
    """Exact replica of ComputePID (AW_LEGACY default) in API/pid.c:213-311."""
    def __init__(self, Kp, Ki, Kd, UMax, UpMax, UiMax, UdMax, SumEMax, EMin):
        self.Kp = float(Kp)
        self.Ki = float(Ki)
        self.Kd = float(Kd)
        self.UMax = float(UMax)
        self.UpMax = float(UpMax)
        self.UiMax = float(UiMax)
        self.UdMax = float(UdMax)
        self.SumEMax = float(SumEMax)
        self.EMin = float(EMin)
        self.reset()

    def reset(self):
        self.Des = 0.0
        self.FB = 0.0
        self.E = 0.0
        self.PreE = 0.0
        self.SumE = 0.0
        self.Up = 0.0
        self.Ui = 0.0
        self.Ud = 0.0
        self.U = 0.0

    def compute(self):
        self.E = self.Des - self.FB
        if (((self.U <= self.UMax and self.E > 0.0) or (self.U >= -self.UMax and self.E < 0.0))
                and abs(self.E) < self.EMin):
            self.SumE += self.E
        self.SumE = float(np.clip(self.SumE, -self.SumEMax, self.SumEMax))
        self.Ui = float(np.clip(self.Ki * self.SumE, -self.UiMax, self.UiMax))
        self.Up = float(np.clip(self.Kp * self.E, -self.UpMax, self.UpMax))
        self.Ud = float(np.clip(self.Kd * (self.E - self.PreE), -self.UdMax, self.UdMax))
        self.U = float(np.clip(self.Up + self.Ui + self.Ud, -self.UMax, self.UMax))
        self.PreE = self.E
        return self.U


def make_z_pos_pid():
    # API/pid.c:33: PID_ROW(0.7, 0.005, 0.1, 1.0, 0.9, 0.3, 0.3, 30, 0.3)
    return FirmwarePID(0.7, 0.005, 0.1, 1.0, 0.9, 0.3, 0.3, 30.0, 0.3)


def make_z_rate_pid():
    # API/pid.c:34: PID_ROW(400, 0.435, 0, 300, 300, 100, 60, 250, 10)
    return FirmwarePID(400.0, 0.435, 0.0, 300.0, 300.0, 100.0, 60.0, 250.0, 10.0)


def poles_detail(k1, k2, k3):
    """Discrete 3-state observer transition matrix M = (I - K C) A over one ToF period T = 10*DT = 0.05 s."""
    T = TOF_DIV * DT
    A = np.array([
        [1.0, T, -0.5 * T * T],
        [0.0, 1.0, -T],
        [0.0, 0.0, 1.0]
    ])
    K = np.array([[k1], [k2], [-k3]])
    C = np.array([[1.0, 0.0, 0.0]])
    M = (np.eye(3) - K @ C) @ A
    eigvals = np.linalg.eigvals(M)
    order = np.argsort(np.abs(eigvals))
    eigvals = eigvals[order]
    return eigvals, np.abs(eigvals)


def poles(k1, k2, k3):
    _, mag = poles_detail(k1, k2, k3)
    return mag


# ---- Part 1: Open-Loop Replay on Flight 20261024-213634 ----
LOG_PATH = 'logs/sessions/20261024-213634-lab-d-excite-001_f01_lab_d_excite/telemetry.csv'
if not os.path.exists(LOG_PATH):
    LOG_PATH = os.path.join('..', '..', LOG_PATH)
d = load(LOG_PATH)
ok = d['ok']
e_edge = np.r_[0, np.diff(ok.astype(int))]
st = np.where(e_edge == 1)[0]
en = np.where(e_edge == -1)[0]
seg = max(zip(st, en[en > st[0]][:len(st)]), key=lambda p: p[1] - p[0])
h0 = d['h'][seg[0]:seg[1]]
U_log = d['U'][seg[0]:seg[1]]
b_f, a_f = butter(2, 3.0 / 100.0, 'low')
ht = filtfilt(b_f, a_f, h0)
vt = np.gradient(ht, DT)
at = np.gradient(vt, DT)
N_ol = len(ht)

# Empirical U-to-accel check on flight log
U_log_clean = filtfilt(b_f, a_f, np.nan_to_num(U_log, nan=np.nanmean(U_log)))
b_u_log_slope, _ = np.polyfit(U_log_clean[200:-200], at[200:-200], 1)

print('truth segment %.1f s, vz range %.2f..%.2f, log b_u=%.5f vs plant b_u=%.5f' % (
    N_ol * DT, vt.min(), vt.max(), b_u_log_slope, B_U))

tof_ol = np.round(ht[(np.arange(N_ol) // TOF_DIV) * TOF_DIV] * 100.0) / 100.0
new_ol = (np.arange(N_ol) % TOF_DIV) == 0


def fd_open_loop():
    v = np.zeros(N_ol)
    f = 0.0
    hp = tof_ol[0]
    for i in range(N_ol):
        f = 0.9 * f + 0.1 * (tof_ol[i] - hp) / DT
        hp = tof_ol[i]
        v[i] = f
    return v


def cf_open_loop(k1, k2, k3, sig, bias=0.1, seed=1):
    r = np.random.default_rng(seed)
    am = at + bias + r.normal(0.0, sig, N_ol)
    h = tof_ol[0]
    v = vt[0]
    bz = 0.0
    out = np.zeros(N_ol)
    for i in range(N_ol):
        u = np.clip(am[i] - bz, -ACC_MAX_PROP, ACC_MAX_PROP)
        h += v * DT + 0.5 * u * DT * DT
        v += u * DT
        if new_ol[i]:
            er = tof_ol[i] - h
            h += k1 * er
            v += k2 * er
            bz -= k3 * er
        out[i] = v
    return out


def lag_ms(v):
    c = [np.mean((v[200 + k:N_ol - 200 + k] - v.mean()) * (vt[200:N_ol - 200] - vt.mean())) for k in range(0, 40)]
    return int(np.argmax(c)) * 5


skip_ol = 400
ol_rows = []
V_ol = {}
ol_configs = (
    [('today FD+LPF', fd_open_loop())]
    + [(f'CF k3=0, IMU noise {s} m/s2', cf_open_loop(K1_PROP, K2_PROP, 0.0, s)) for s in (0.3, 1.0, 3.0)]
    + [(f'CF k3={k}, IMU noise 1', cf_open_loop(K1_PROP, K2_PROP, k, 1.0)) for k in (0.02, 0.1)]
)
for name, v in ol_configs:
    dv = np.abs(np.diff(v[skip_ol:]))
    ol_rows.append((
        name,
        float(np.sqrt(np.mean((v - vt)[skip_ol:] ** 2))),
        float(np.mean((v - vt)[skip_ol:])),
        float(np.percentile(dv, 99)),
        float(400.0 * np.percentile(dv, 99)),
        float(400.0 * dv.max()),
        lag_ms(v)
    ))
    V_ol[name] = v


# ---- Part 2: Closed-Loop S1 Simulation ----
def sim_closed_loop_step(mode, k1=K1_PROP, k2=K2_PROP, k3=K3_PROP, sigma=1.0, bias=0.1, seed=1, noisy_step=False):
    """Closed-loop 0.5 m altitude step response (0.5 m -> 1.0 m after 20 s hover warmup)."""
    idx_step = 4000  # 20.0 s hover warmup so estimator & PID integrators are in steady state
    N_post = 2000    # 10.0 s post-step window
    N = idx_step + N_post
    rng = np.random.default_rng(seed)
    z_pos = make_z_pos_pid()
    z_rate = make_z_rate_pid()

    h_true = 0.5
    vz_true = 0.0
    h_tof = np.round(h_true * 100.0) / 100.0
    h_last = h_tof
    vz_fd = 0.0
    h_cf = h_tof
    vz_cf = 0.0
    b_az = 0.0
    u_cmd = 0.0

    H_true = np.zeros(N)
    V_true = np.zeros(N)
    H_des = np.zeros(N)
    H_est = np.zeros(N)
    V_est = np.zeros(N)
    U_out = np.zeros(N)
    TOF_series = np.zeros(N)

    eff_sigma = sigma if noisy_step else 0.0
    for i in range(N):
        h_des = 0.5 if i < idx_step else 1.0
        H_des[i] = h_des

        # Plant advances over [t_{i-1}, t_i] under previous tick's command u_cmd
        a_true = B_U * u_cmd
        if i > 0:
            h_true += vz_true * DT + 0.5 * a_true * DT * DT
            vz_true += a_true * DT

        # Sensor measurements at t_i
        if i % TOF_DIV == 0:
            h_tof = np.round(h_true * 100.0) / 100.0
        TOF_series[i] = h_tof
        a_meas = a_true + bias + (rng.normal(0.0, eff_sigma) if eff_sigma > 0.0 else 0.0)

        if mode == 'FD':
            raw_v = (h_tof - h_last) / DT
            h_last = h_tof
            vz_fd = 0.9 * vz_fd + 0.1 * raw_v
            z_pos.FB = h_tof
            z_rate.FB = vz_fd
        else:
            if i > 0:
                a_up = float(np.clip(a_meas - b_az, -ACC_MAX_PROP, ACC_MAX_PROP))
                h_cf += vz_cf * DT + 0.5 * a_up * DT * DT
                vz_cf += a_up * DT
            if i % TOF_DIV == 0:
                er = h_tof - h_cf
                h_cf += k1 * er
                vz_cf += k2 * er
                b_az -= k3 * er
            z_pos.FB = h_cf
            z_rate.FB = vz_cf

        H_true[i] = h_true
        V_true[i] = vz_true
        H_est[i] = z_pos.FB
        V_est[i] = z_rate.FB

        z_pos.Des = h_des
        if (i + 1) % POS_LOOP_DIV == 0:
            z_pos.compute()
        z_rate.Des = z_pos.U
        u_cmd = z_rate.compute()
        U_out[i] = u_cmd

    err_post = np.abs(H_true[idx_step:] - 1.0)
    win = 200  # 1.0 s sustained settling window

    def sustained_settle(err_arr, band):
        within = (err_arr <= band).astype(int)
        for k in range(len(within) - win):
            if np.all(within[k:k + win]):
                return k * DT
        out_idx = np.where(err_arr > band)[0]
        return float((out_idx[-1] + 1) * DT) if len(out_idx) else 0.0

    t_settle_5 = sustained_settle(err_post, 0.025)  # 5% of 0.5 m = 2.5 cm
    t_settle_2 = sustained_settle(err_post, 0.010)  # 2% of 0.5 m = 1.0 cm
    z_rms_step = float(np.sqrt(np.mean(err_post ** 2)))

    return dict(
        t=(np.arange(N) - idx_step) * DT,
        idx_step=idx_step,
        H_true=H_true, V_true=V_true, H_des=H_des,
        H_est=H_est, V_est=V_est, U_out=U_out, TOF=TOF_series,
        t_settle_5=t_settle_5, t_settle_2=t_settle_2, z_rms_step=z_rms_step
    )


def sim_closed_loop_tracking(mode, k1=K1_PROP, k2=K2_PROP, k3=K3_PROP, sigma=1.0, bias=0.1, seed=1):
    """Closed-loop active tracking & disturbance simulation (30 s at 200 Hz)."""
    N = 6000  # 30.0 s at 200 Hz
    rng = np.random.default_rng(seed)
    z_pos = make_z_pos_pid()
    z_rate = make_z_rate_pid()

    h_true = 1.0
    vz_true = 0.0
    h_tof = np.round(h_true * 100.0) / 100.0
    h_last = h_tof
    vz_fd = 0.0
    h_cf = h_tof
    vz_cf = 0.0
    b_az = 0.0
    u_cmd = 0.0

    H_true = np.zeros(N)
    V_true = np.zeros(N)
    H_des = np.zeros(N)
    H_est = np.zeros(N)
    V_est = np.zeros(N)
    U_out = np.zeros(N)
    TOF_series = np.zeros(N)

    for i in range(N):
        t = i * DT
        h_des = 1.0 + 0.35 * np.sin(2.0 * np.pi * 0.35 * t) + 0.15 * np.cos(2.0 * np.pi * 0.80 * t)
        H_des[i] = h_des

        d_ext = -0.20 + 1.25 * np.sin(2.0 * np.pi * 0.55 * t) + 0.80 * np.cos(2.0 * np.pi * 1.15 * t)
        a_true = B_U * u_cmd + d_ext
        if i > 0:
            h_true += vz_true * DT + 0.5 * a_true * DT * DT
            vz_true += a_true * DT

        if i % TOF_DIV == 0:
            h_tof = np.round(h_true * 100.0) / 100.0
        TOF_series[i] = h_tof

        a_meas = a_true + bias + rng.normal(0.0, sigma)

        if mode == 'FD':
            raw_v = (h_tof - h_last) / DT
            h_last = h_tof
            vz_fd = 0.9 * vz_fd + 0.1 * raw_v
            z_pos.FB = h_tof
            z_rate.FB = vz_fd
        else:
            if i > 0:
                a_up = float(np.clip(a_meas - b_az, -ACC_MAX_PROP, ACC_MAX_PROP))
                h_cf += vz_cf * DT + 0.5 * a_up * DT * DT
                vz_cf += a_up * DT
            if i % TOF_DIV == 0:
                er = h_tof - h_cf
                h_cf += k1 * er
                vz_cf += k2 * er
                b_az -= k3 * er
            z_pos.FB = h_cf
            z_rate.FB = vz_cf

        H_true[i] = h_true
        V_true[i] = vz_true
        H_est[i] = z_pos.FB
        V_est[i] = z_rate.FB

        z_pos.Des = h_des
        if (i + 1) % POS_LOOP_DIV == 0:
            z_pos.compute()
        z_rate.Des = z_pos.U
        u_cmd = z_rate.compute()
        U_out[i] = u_cmd

    skip = 400
    dU_all = np.abs(np.diff(U_out[skip:]))
    tof_ticks = np.arange(skip + TOF_DIV, N, TOF_DIV)
    dU_tof = np.abs(U_out[tof_ticks] - U_out[tof_ticks - 1])
    z_rms = float(np.sqrt(np.mean((H_true[skip:] - H_des[skip:]) ** 2)))
    vz_rms = float(np.sqrt(np.mean((V_est[skip:] - V_true[skip:]) ** 2)))

    return dict(
        t=np.arange(N) * DT,
        H_true=H_true, V_true=V_true, H_des=H_des,
        H_est=H_est, V_est=V_est, U_out=U_out, TOF=TOF_series,
        dU_p90=float(np.percentile(dU_all, 90)),
        dU_p99=float(np.percentile(dU_all, 99)),
        dU_max=float(dU_all.max()),
        dU_tof_p90=float(np.percentile(dU_tof, 90)),
        dU_tof_p99=float(np.percentile(dU_tof, 99)),
        z_rms=z_rms,
        vz_rms=vz_rms
    )


# Run closed-loop baseline FD and CF sweep
step_fd = sim_closed_loop_step('FD', noisy_step=False)
trk_fd = sim_closed_loop_tracking('FD', sigma=1.0, bias=0.1)

cl_sweep_configs = [
    ('Mode 0: FD + LPF (baseline)', 'FD', 0.0, 0.0, 0.0, 1.0),
    ('CF (k3=0.00, sigma=0.3 m/s^2)', 'CF', K1_PROP, K2_PROP, 0.00, 0.3),
    ('CF (k3=0.00, sigma=1.0 m/s^2)', 'CF', K1_PROP, K2_PROP, 0.00, 1.0),
    ('CF (k3=0.00, sigma=3.0 m/s^2)', 'CF', K1_PROP, K2_PROP, 0.00, 3.0),
    ('CF (k3=0.02, sigma=0.3 m/s^2)', 'CF', K1_PROP, K2_PROP, 0.02, 0.3),
    ('CF (k3=0.02, sigma=1.0 m/s^2)', 'CF', K1_PROP, K2_PROP, 0.02, 1.0),
    ('CF (k3=0.02, sigma=3.0 m/s^2)', 'CF', K1_PROP, K2_PROP, 0.02, 3.0),
    ('CF (k3=0.10, sigma=0.3 m/s^2)', 'CF', K1_PROP, K2_PROP, 0.10, 0.3),
    ('CF (k3=0.10, sigma=1.0 m/s^2) [NOMINAL]', 'CF', K1_PROP, K2_PROP, 0.10, 1.0),
    ('CF (k3=0.10, sigma=3.0 m/s^2)', 'CF', K1_PROP, K2_PROP, 0.10, 3.0),
]

cl_results = []
saved_runs = {}
for label, mode, k1, k2, k3, sig in cl_sweep_configs:
    if mode == 'FD':
        st_res = step_fd
        tr_res = trk_fd
    else:
        st_res = sim_closed_loop_step('CF', k1, k2, k3, sigma=sig, bias=0.1, noisy_step=False)
        tr_res = sim_closed_loop_tracking('CF', k1, k2, k3, sigma=sig, bias=0.1)
    saved_runs[label] = (st_res, tr_res)
    p90_ratio = trk_fd['dU_p90'] / tr_res['dU_p90']
    p99_ratio = trk_fd['dU_p99'] / tr_res['dU_p99']
    tof_p90_ratio = trk_fd['dU_tof_p90'] / tr_res['dU_tof_p90']
    tof_p99_ratio = trk_fd['dU_tof_p99'] / tr_res['dU_tof_p99']
    t5_diff_pct = 100.0 * (st_res['t_settle_5'] - step_fd['t_settle_5']) / step_fd['t_settle_5']
    t2_diff_pct = 100.0 * (st_res['t_settle_2'] - step_fd['t_settle_2']) / step_fd['t_settle_2']
    cl_results.append(dict(
        label=label, mode=mode, k1=k1, k2=k2, k3=k3, sigma=sig,
        dU_p90=tr_res['dU_p90'], p90_ratio=p90_ratio,
        dU_p99=tr_res['dU_p99'], p99_ratio=p99_ratio,
        dU_tof_p90=tr_res['dU_tof_p90'], tof_p90_ratio=tof_p90_ratio,
        dU_tof_p99=tr_res['dU_tof_p99'], tof_p99_ratio=tof_p99_ratio,
        t_settle_5=st_res['t_settle_5'], t5_diff_pct=t5_diff_pct,
        t_settle_2=st_res['t_settle_2'], t2_diff_pct=t2_diff_pct,
        z_rms_step=st_res['z_rms_step'],
        z_rms=tr_res['z_rms'], vz_rms=tr_res['vz_rms']
    ))

step_cf_nom_noisy = sim_closed_loop_step('CF', K1_PROP, K2_PROP, K3_PROP, sigma=1.0, bias=0.1, noisy_step=True)

# ---- Write .agent-ops/research/z-vz-cf/sim_metrics.txt ----
hdr_ol = '| estimator | vz RMS error m/s | vz mean error m/s | per-tick abs dvz p99 m/s | thrust-kick proxy p99 (400 x) | proxy max | lag ms |'
txt = [
    '# Open-Loop Replay on Flight 20261024-213634',
    hdr_ol,
    '|' + '---|' * 7
] + ['| %s | %.3f | %+.3f | %.4f | %.1f | %.0f | %d |' % r for r in ol_rows]

for k3_val in (0.0, 0.02, 0.10):
    eig, mag = poles_detail(K1_PROP, K2_PROP, k3_val)
    eig_str = ', '.join([f'{z.real:+.4f}{z.imag:+.4f}j' if abs(z.imag) > 1e-6 else f'{z.real:+.4f}' for z in eig])
    mag_str = ', '.join([f'{m:.4f}' for m in mag])
    txt.append(f'poles per ToF sample (k1={K1_PROP}, k2={K2_PROP}, k3={k3_val}): eigvals=[{eig_str}], |z|=[{mag_str}]')

txt += [
    '',
    '# Closed-Loop S1 Cascade Simulation (Z_posPID -> Z_ratePID, Kp=400, b_u=%.6f (m/s^2)/CCR)' % B_U,
    '| Configuration | abs dU p90 (all / ToF) | Ratio vs FD p90 (all / ToF) | abs dU p99 (all / ToF) | Ratio vs FD p99 (all / ToF) | Step Settle 5%% (s / vs FD) | Step Settle 2%% (s / vs FD) | Z RMS (step / trk m) | vz RMS (m/s) |',
    '|---|---|---|---|---|---|---|---|---|'
]
for r in cl_results:
    txt.append(
        f"| {r['label']} | {r['dU_p90']:.2f} / {r['dU_tof_p90']:.2f} | "
        f"{r['p90_ratio']:.2f}x / {r['tof_p90_ratio']:.1f}x | "
        f"{r['dU_p99']:.2f} / {r['dU_tof_p99']:.2f} | "
        f"{r['p99_ratio']:.1f}x / {r['tof_p99_ratio']:.1f}x | "
        f"{r['t_settle_5']:.3f}s ({r['t5_diff_pct']:+.1f}%) | "
        f"{r['t_settle_2']:.3f}s ({r['t2_diff_pct']:+.1f}%) | "
        f"{r['z_rms_step']:.4f} / {r['z_rms']:.4f} | {r['vz_rms']:.4f} |"
    )

os.makedirs('.agent-ops/research/z-vz-cf', exist_ok=True)
os.makedirs('.agent-ops/out/z-vz-cf', exist_ok=True)
with open('.agent-ops/research/z-vz-cf/sim_metrics.txt', 'w', encoding='utf-8') as fh:
    fh.write('\n'.join(txt) + '\n')
print('\n'.join(txt))

# ---- Figures: .agent-ops/research/z-vz-cf/sim.png and .agent-ops/out/z-vz-cf/s1_sim.png ----
w_ol = slice(1000, 1600)
tt_ol = np.arange(600) * DT
fdv_ol = V_ol['today FD+LPF']
cfv_ol = V_ol['CF k3=0.1, IMU noise 1']
fig_ol, ax_ol = plt.subplots(3, 1, figsize=(13, 10), sharex=True)
ax_ol[0].step(tt_ol, tof_ol[w_ol] * 100.0, where='post', color='k', label='simulated ToF (20 Hz, 1 cm)')
ax_ol[0].plot(tt_ol, ht[w_ol] * 100.0, color='tab:green', label='true height (smoothed real flight)')
ax_ol[0].set_ylabel('height (cm)')
ax_ol[0].legend(fontsize=8)
ax_ol[0].set_title('Estimator replay on flight lab-d-excite 21:36 height path (IMU noise 1 m/s2 + bias 0.1 m/s2 ASSUMED)')
ax_ol[1].plot(tt_ol, vt[w_ol], color='tab:green', lw=2, label='true vz')
ax_ol[1].plot(tt_ol, fdv_ol[w_ol], color='tab:blue', lw=1, label='today: FD + LPF')
ax_ol[1].plot(tt_ol, cfv_ol[w_ol], color='tab:purple', lw=1.5, label='new: complementary filter')
ax_ol[1].set_ylabel('vz (m/s)')
ax_ol[1].legend(fontsize=8)
ax_ol[2].plot(tt_ol[1:], 400.0 * np.diff(fdv_ol[w_ol]), color='tab:blue', lw=1, label='today')
ax_ol[2].plot(tt_ol[1:], 400.0 * np.diff(cfv_ol[w_ol]), color='tab:purple', lw=1.5, label='new')
ax_ol[2].set_ylabel('thrust kick per tick\n(Kp 400 x dvz)')
ax_ol[2].set_xlabel('time (s)')
ax_ol[2].legend(fontsize=8)
fig_ol.tight_layout()
fig_ol.savefig('.agent-ops/research/z-vz-cf/sim.png', dpi=110)
plt.close(fig_ol)

st_cf_nom, trk_cf_nom = saved_runs['CF (k3=0.10, sigma=1.0 m/s^2) [NOMINAL]']
fig, axes = plt.subplots(2, 2, figsize=(15, 10))

idx0 = step_fd['idx_step'] - 200
idx1 = step_fd['idx_step'] + 1600
w_step = slice(idx0, idx1)
t_s = step_fd['t'][w_step]
axes[0, 0].plot(t_s, step_fd['H_des'][w_step] * 100.0, 'k--', lw=1.2, label='Z_posPID.Des (50 -> 100 cm step)')
axes[0, 0].plot(t_s, step_fd['H_true'][w_step] * 100.0, color='tab:blue', lw=1.5,
                label=f"Mode 0 FD+LPF (settle 5%: {step_fd['t_settle_5']:.2f}s, 2%: {step_fd['t_settle_2']:.2f}s)")
axes[0, 0].plot(t_s, st_cf_nom['H_true'][w_step] * 100.0, color='tab:purple', lw=1.8,
                label=f"Mode 1 CF nominal (settle 5%: {st_cf_nom['t_settle_5']:.2f}s, 2%: {st_cf_nom['t_settle_2']:.2f}s)")
axes[0, 0].plot(t_s, step_cf_nom_noisy['H_true'][w_step] * 100.0, color='tab:orange', lw=1.0, alpha=0.8,
                label='Mode 1 CF with sigma=1.0 m/s2 noise')
axes[0, 0].axhspan(97.5, 102.5, color='gray', alpha=0.15, label='+-5% band (+-2.5 cm)')
axes[0, 0].set_title('Closed-Loop 0.5 m Altitude Step Response (Z_posPID -> Z_ratePID)')
axes[0, 0].set_xlabel('Time from Step Command (s)')
axes[0, 0].set_ylabel('Height (cm)')
axes[0, 0].legend(fontsize=8, loc='lower right')
axes[0, 0].grid(True, alpha=0.3)

w_trk = slice(1000, 2000)
t_t = trk_fd['t'][w_trk]
axes[0, 1].plot(t_t, trk_cf_nom['V_true'][w_trk], color='tab:green', lw=2.0, label='True vz (CF plant)')
axes[0, 1].plot(t_t, trk_fd['V_est'][w_trk], color='tab:blue', lw=1.0, alpha=0.85,
                label=f"Mode 0 FD+LPF vz_FB (RMS err {trk_fd['vz_rms']:.3f} m/s)")
axes[0, 1].plot(t_t, trk_cf_nom['V_est'][w_trk], color='tab:purple', lw=1.5,
                label=f"Mode 1 CF vz_FB (RMS err {trk_cf_nom['vz_rms']:.3f} m/s)")
axes[0, 1].set_title('Closed-Loop Climb-Rate Feedback (sigma=1.0 m/s2, bias=0.1 m/s2)')
axes[0, 1].set_xlabel('Time (s)')
axes[0, 1].set_ylabel('vz (m/s)')
axes[0, 1].legend(fontsize=8, loc='upper right')
axes[0, 1].grid(True, alpha=0.3)

dU_fd_win = np.diff(trk_fd['U_out'][w_trk])
dU_cf_win = np.diff(trk_cf_nom['U_out'][w_trk])
axes[1, 0].plot(t_t[1:], dU_fd_win, color='tab:blue', lw=1.0, alpha=0.85,
                label=f"Mode 0 FD+LPF dU (p90={trk_fd['dU_p90']:.1f}, p99={trk_fd['dU_p99']:.1f} CCR)")
axes[1, 0].plot(t_t[1:], dU_cf_win, color='tab:purple', lw=1.3,
                label=f"Mode 1 CF dU (p90={trk_cf_nom['dU_p90']:.1f}, p99={trk_cf_nom['dU_p99']:.1f} CCR)")
axes[1, 0].set_title('Closed-Loop Per-Tick Control Kick dU = diff(Z_ratePID.U) (Kp=400)')
axes[1, 0].set_xlabel('Time (s)')
axes[1, 0].set_ylabel('Per-tick dU (CCR units)')
axes[1, 0].legend(fontsize=8, loc='upper right')
axes[1, 0].grid(True, alpha=0.3)

axes[1, 1].plot(tt_ol[1:], 400.0 * np.diff(fdv_ol[w_ol]), color='tab:blue', lw=1.0, alpha=0.85,
                label='Flight 213634 Open-Loop: FD+LPF (p99=80.1 CCR)')
axes[1, 1].plot(tt_ol[1:], 400.0 * np.diff(cfv_ol[w_ol]), color='tab:purple', lw=1.4,
                label='Flight 213634 Open-Loop: CF k3=0.1, sigma=1.0 (p99=10.0 CCR)')
axes[1, 1].set_title('Open-Loop Flight 20261024-213634 Replay Thrust Kick (400 x dvz)')
axes[1, 1].set_xlabel('Window Time (s)')
axes[1, 1].set_ylabel('400 x dvz (CCR units)')
axes[1, 1].legend(fontsize=8, loc='upper right')
axes[1, 1].grid(True, alpha=0.3)

fig.tight_layout()
fig.savefig('.agent-ops/out/z-vz-cf/s1_sim.png', dpi=120)
plt.close(fig)

root_ops = os.path.abspath(os.path.join(os.getcwd(), '..', '..', '.agent-ops'))
if os.path.basename(os.path.dirname(os.getcwd())) == '.worktrees' and os.path.isdir(root_ops):
    os.makedirs(os.path.join(root_ops, 'research', 'z-vz-cf'), exist_ok=True)
    os.makedirs(os.path.join(root_ops, 'out', 'z-vz-cf'), exist_ok=True)
    shutil.copy2('.agent-ops/research/z-vz-cf/sim.py', os.path.join(root_ops, 'research', 'z-vz-cf', 'sim.py'))
    shutil.copy2('.agent-ops/research/z-vz-cf/sim_metrics.txt', os.path.join(root_ops, 'research', 'z-vz-cf', 'sim_metrics.txt'))
    shutil.copy2('.agent-ops/research/z-vz-cf/sim.png', os.path.join(root_ops, 'research', 'z-vz-cf', 'sim.png'))
    shutil.copy2('.agent-ops/out/z-vz-cf/s1_sim.png', os.path.join(root_ops, 'out', 'z-vz-cf', 's1_sim.png'))
    print('Synced sim outputs to', root_ops)
