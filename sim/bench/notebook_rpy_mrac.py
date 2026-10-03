"""Headless port of the operator's P3 notebook simulation (WP-30).

Source: Roll_Pitch_Yaw_Adaptive_Control_Direct_MRAC_FFcontroller_Projection_operator v2.ipynb
(extract: docs/analysis/notebooks/p3_rpy_mrac_ff_proj_v2.py). Cell numbers below are 0-based
indices in that notebook. The port keeps the notebook's default Config (cell 8): RBF regressor
extended with [un, v], low-frequency learning, normalisation, projection, no sigma-mod, no barrier,
L1-like performance recovery, motor lag + quad-X mixer, 'hard' reference and uncertainty.
Forward Euler at dt = 6 ms, exactly as the notebook loop (cell 24).

The non-adaptive baseline is the notebook's own Config.ADAPTATION_ON = False switch
(tuning guide, cell 17 "Phase 1: Establish Baseline"). With performance recovery on, that
baseline still has the L1-like v term, so compare on x - xm (the notebook's error, cell 33);
--ablation adds the runs with performance recovery off (pure K1/K2 nominal vs MRAC).

    python sim/bench/notebook_rpy_mrac.py [--ft 100] [--ablation] [--plot]
"""
from __future__ import annotations

import argparse
import time

import numpy as np
from scipy.linalg import solve_continuous_lyapunov

# --- cell 1: physical parameters ---
DRONE_MASS = 0.650
ARM_LENGTH = 0.225
MAX_THRUST_PER_MOTOR_N = DRONE_MASS * 4.3 * 9.81 / 4.0
U_MAX_PR = 2 * MAX_THRUST_PER_MOTOR_N * ARM_LENGTH * np.sin(np.pi / 4)   # 2.18 N·m
U_MAX_YAW = 1.5
J = np.array([0.015, 0.015, 0.025])          # pitch, roll, yaw [kg m^2]
U_MAX = np.array([U_MAX_PR, U_MAX_PR, U_MAX_YAW])
MOTOR_TAU = 0.05
HOVER_THROTTLE = 0.23
DT = 0.006
FT = 100.0

# --- cell 3: reference model ---
WN, ZETA = 3.5, 0.707
AM1 = np.array([[0.0, 1.0], [-WN ** 2, -2 * ZETA * WN]])
BM1 = np.array([0.0, WN ** 2])

# --- cell 4: model-matching gains K1 = J [wn^2, 2 zeta wn], K2 = J wn^2; P from Am^T P + P Am = -I ---
K1 = J[:, None] * np.array([WN ** 2, 2 * ZETA * WN])
K2 = J * WN ** 2
P = solve_continuous_lyapunov(AM1.T, -np.eye(2))
PB = P[:, 1]                                  # P @ B with B = [0, 1]^T

# --- cell 9: adaptive parameters ---
MAX_ADAPTIVE_TORQUE = 0.3 * U_MAX
GAMMA = np.array([350.0, 350.0, 300.0])
PROJ_LIM, PROJ_TOL = 100.0, 5.0
SIGMA = np.array([0.8, 0.8, 1.0])
SIGMA_LF_F = np.array([0.8, 0.8, 1.0])
GAM_F = 16.0
N_RBF = 6
RBF_ANG = (-np.pi, np.pi)
RBF_RATE = (-np.pi / 3, np.pi / 3)

# --- cell 10: performance recovery ---
LAM = 100.0
TAU_V = np.array([2.0, 2.0, 1.5])

MAX_ANGULAR_RATE = 50.0
UNC_MAX = 0.6 * HOVER_THROTTLE * U_MAX        # cell 24: disturbance capped at 60 % of hover authority


def _rbf(val, lo, hi):
    c = np.linspace(lo, hi, N_RBF)
    width = 1.0 / ((hi - lo) / N_RBF) ** 2
    return np.exp(-width * (val - c) ** 2)


def basis(angle, rate):
    """cell 12 compute_basis, simple-RBF mode: 6 angle + 6 rate Gaussians."""
    return np.concatenate([_rbf(angle, *RBF_ANG), _rbf(rate, *RBF_RATE)])


def true_weights(t, pitch_rate):
    """cell 13 compute_time_varying_uncertainty, level='hard', simple RBF. Rows: pitch, roll, yaw."""
    k = np.arange(N_RBF) / (N_RBF - 1)
    vibe = 0.03 * np.sin(15 * t)
    pa = 0.10 + 0.20 * k + vibe
    pa[N_RBF // 2] += 0.2 if abs(pitch_rate) > 0.35 else 0.0
    rate = 0.25 - 0.10 * k
    return np.array([np.concatenate([pa, rate]),
                     np.concatenate([0.10 + 0.20 * k + vibe, rate]),
                     np.concatenate([0.08 + 0.12 * k, 0.20 - 0.10 * k])])


def wrap(a):
    return np.arctan2(np.sin(a), np.cos(a))


def reference(t):
    """cell 13 compute_reference_trajectory, level='hard': [pitch, roll, yaw] in rad."""
    d = np.pi / 180
    pitch = (15 * np.sin(0.3 * 2 * np.pi * t) + 8 * np.sin(0.05 * 2 * np.pi * t)) * d
    roll = 18 * (1 + 0.3 * np.sin(0.08 * 2 * np.pi * t)) * np.sin(0.25 * 2 * np.pi * t) * d
    yaw = (30 + 20 * np.sin(0.1 * 2 * np.pi * t)) * t * d
    if int(t) % 15 == 0 and (t % 1.0) < 0.5:
        pitch += 30 * d * np.sin(10 * t)
    return wrap(np.array([pitch, roll, yaw]))


def projection(W, grad):
    """cell 24 Projection_operator: scale outward gradient inside the PROJ_TOL boundary layer."""
    hi = (W > PROJ_LIM - PROJ_TOL) & (grad > 0)
    lo = (W < -PROJ_LIM + PROJ_TOL) & (grad < 0)
    out = grad.copy()
    out[hi] = (PROJ_LIM - W[hi]) / PROJ_TOL * grad[hi]
    out[lo] = (-PROJ_LIM - W[lo]) / PROJ_TOL * grad[lo]
    return out


def mixer_roundtrip(u, motors):
    """cell 24 step 6: torque -> quad-X motor commands -> first-order lag -> applied torque."""
    n = u / U_MAX * HOVER_THROTTLE
    cmd = HOVER_THROTTLE + np.array([-n[0] + n[1] - n[2], n[0] + n[1] + n[2],
                                     -n[0] - n[1] + n[2], n[0] - n[1] - n[2]])
    motors += (np.clip(cmd, 0.0, 1.0) - motors) * (DT / MOTOR_TAU)
    m = motors
    diff = np.array([(m[1] + m[3]) - (m[0] + m[2]),
                     (m[0] + m[1]) - (m[2] + m[3]),
                     (m[1] + m[2]) - (m[0] + m[3])])
    return diff / (4.0 * HOVER_THROTTLE) * U_MAX


def simulate(adaptive=True, ft=FT, perf_recovery=True, sigma_mod=False, proj=True):
    """Run the cell 24 loop. Returns dict of logs (rad, N·m) keyed like the notebook's `log`."""
    nb = 2 * N_RBF + 2
    x, xm, xr, f = (np.zeros((3, 2)) for _ in range(4))   # rows: axis, cols: angle, rate
    W, Wf = np.zeros((3, nb)), np.zeros((3, nb))
    r_f, v, un_prev, v_prev = (np.zeros(3) for _ in range(4))
    motors = np.full(4, HOVER_THROTTLE)
    sig = SIGMA if sigma_mod else np.zeros(3)
    steps = np.arange(0, ft, DT)
    log = {k: np.zeros((len(steps), 3)) for k in ("x", "xm", "xr", "r", "u", "ua", "u_applied", "unc")}
    for k, t in enumerate(steps):
        r = reference(t)
        r_f += DT * 4.0 * (r - r_f)
        Wt = true_weights(t, x[0, 1])
        base = np.array([basis(x[i, 0], x[i, 1]) for i in range(3)])
        theta = np.hstack([base, un_prev[:, None], v_prev[:, None]])
        ua = np.clip(-np.sum(W * theta, axis=1), -MAX_ADAPTIVE_TORQUE, MAX_ADAPTIVE_TORQUE) \
            if adaptive else np.zeros(3)
        if perf_recovery:
            e = x - xm
            f += DT * (-LAM * (f - e))
            g_rate = LAM * f[:, 1] + (e @ AM1.T)[:, 1] - LAM * e[:, 1]   # rate rows of lam f + (Am - lam I) e
            v = v + DT * (g_rate - v) / TAU_V
        un = -np.sum(K1 * x, axis=1) + K2 * r_f + v
        u = np.clip(un + ua, -U_MAX, U_MAX)
        u_app = mixer_roundtrip(u, motors)
        un_prev, v_prev = un, v.copy()
        unc = np.clip(np.sum(Wt * base, axis=1), -UNC_MAX, UNC_MAX)
        acc = (u_app + unc) / J
        x = x + DT * np.column_stack([x[:, 1], acc])
        x[:, 1] = np.clip(x[:, 1], -MAX_ANGULAR_RATE, MAX_ANGULAR_RATE)
        hedge = u_app - u
        xm = xm + DT * (xm @ AM1.T + np.outer(r_f, BM1) + np.column_stack([np.zeros(3), hedge + v]))
        xr = xr + DT * (xr @ AM1.T + np.outer(r_f, BM1))
        if adaptive:
            e = x - xm
            norm = 1.0 + np.sum(theta * theta, axis=1, keepdims=True)
            grad = theta * (e @ PB)[:, None] / norm
            if proj:
                grad = projection(W, grad)
            W = W + DT * GAMMA[:, None] * (grad - SIGMA_LF_F[:, None] * (W - Wf) / norm
                                           - sig[:, None] * W / norm)
            Wf = Wf + DT * GAM_F * (W - Wf)
        for key, val in (("x", x[:, 0]), ("xm", xm[:, 0]), ("xr", xr[:, 0]), ("r", r_f), ("u", u),
                         ("ua", ua), ("u_applied", u_app), ("unc", unc)):
            log[key][k] = val
        if not np.all(np.isfinite(x)) or np.any(np.abs(x) > 1000):
            for key in log:
                log[key] = log[key][:k + 1]
            steps = steps[:k + 1]
            break
    log["t"] = steps
    log["W_final"] = W
    return log


def rms_deg(log, ref="xr"):
    """Per-axis RMS of angle error x - ref, in degrees (yaw wrapped)."""
    err = wrap(log["x"] - log[ref])
    return np.degrees(np.sqrt(np.mean(err ** 2, axis=0)))


# name -> (adaptive, perf_recovery). The first two are the notebook default and its
# ADAPTATION_ON = False baseline; the last two (--ablation) switch performance recovery off too.
CONFIGS = {"mrac+pr": (True, True), "pr_only": (False, True),
           "mrac": (True, False), "nominal": (False, False)}


def compare(ft=FT, names=("mrac+pr", "pr_only")):
    out = {}
    for name in names:
        adaptive, pr = CONFIGS[name]
        t0 = time.perf_counter()
        log = simulate(adaptive=adaptive, ft=ft, perf_recovery=pr)
        out[name] = {"log": log, "rms_xr": rms_deg(log, "xr"), "rms_xm": rms_deg(log, "xm"),
                     "seconds": time.perf_counter() - t0}
    return out


def _plot(res):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(3, 1, sharex=True, figsize=(10, 8))
    for i, name in enumerate(("pitch", "roll", "yaw")):
        for key, res_i in res.items():
            lg = res_i["log"]
            ax[i].plot(lg["t"], np.degrees(lg["x"][:, i]), label=key)
        ax[i].plot(lg["t"], np.degrees(lg["xr"][:, i]), "k--", label="ref model")
        ax[i].set_ylabel(f"{name} [deg]")
    ax[0].legend()
    ax[-1].set_xlabel("t [s]")
    plt.show()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ft", type=float, default=FT, help="simulated seconds (notebook: 100)")
    ap.add_argument("--plot", action="store_true")
    ap.add_argument("--ablation", action="store_true", help="also run without performance recovery")
    args = ap.parse_args(argv)
    res = compare(args.ft, tuple(CONFIGS) if args.ablation else ("mrac+pr", "pr_only"))
    print(f"ft={args.ft:g}s dt={DT}s  RMS angle error [deg] pitch/roll/yaw")
    for name, r in res.items():
        print(f"  {name:8s} vs xr {np.round(r['rms_xr'], 3)}  vs xm {np.round(r['rms_xm'], 3)}  "
              f"({r['seconds']:.1f}s wall, {len(r['log']['t'])} steps)")
    if args.plot:
        _plot(res)


if __name__ == "__main__":
    main()
