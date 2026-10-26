"""n26-B closed-loop trajectory simulation & figure generator.

Compares 5 trajectory/controller modes across PID, LQR, MPC, and ADRC on:
  1. The 10-24 figure-8 (x 0.36 Hz, y 0.18 Hz, Gerono a = 50 cm, 20 s move, 2 s ramp, with lightly-damped
     0.47 Hz plant disturbance/resonator mode matching the 10-24 hover wobble).
  2. A 4-corner waypoint square (50 x 50 cm, 16 s move, 4 s per side).

Modes:
  - today        : fixed clock (s=1), profile 1 / linear segments, VFF only, current-ref predictor
  - slow_clock   : slow clock enabled (e_lo=15 cm PROPOSED, e_hi=45 cm PROPOSED, recover=1.0/s PROPOSED)
  - min_jerk_aff : minimum-jerk quintic generator + acceleration feedforward (aff)
  - pred_fix     : predictor reference lead d ticks ahead (VAR 74 g_xyc_pred_lead=1)
  - all          : slow clock + min-jerk + acceleration feedforward + predictor fix
"""
from __future__ import annotations

import csv
import json
import math
import shutil
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from ground_station.analysis import traj_metrics as tm
from ground_station.analysis import xy_ctrl as xc

DT = xc.DT
# Nominal 10-24 plant from API/xy_ctrl_gains.h (delay 21 ticks measured on 10-21/10-24, header d=33)
PLANT_M = {"k": 0.674429432, "tau": 0.0674, "al": 0.862128296, "drag": 0.42558222, "d": 21}
LQR_K_HEADER = np.array([29.7682778, 10.0523956, 0.35933145, 2.2333094])
ADRC_WC, ADRC_WO, ADRC_B0 = 1.34060242, 12.0, 0.572712305
MPC_Q = (1000.0, 0.0189, 0.0, 10.0)
MPC_RD = 0.0399208702

# PROPOSED slow-clock & arrival parameters from simulation
PROPOSED_E_LO_CM = 15.0
PROPOSED_E_HI_CM = 45.0
PROPOSED_RECOVER_PER_S = 1.0
PROPOSED_ARRIVE_R_CM = 15.0

CONTROLLERS = ("pid", "lqr", "mpc", "adrc")
MODES = ("today", "slow_clock", "min_jerk_aff", "pred_fix", "all")
MODE_LABELS = {
    "today": "Today (fixed clock, VFF)",
    "slow_clock": "Slow clock (e_lo=15, e_hi=45)",
    "min_jerk_aff": "Min-jerk + Accel FF",
    "pred_fix": "Predictor lead (d ticks)",
    "all": "All combined",
}


class PidCascade1D:
    """Firmware locx + locxs cascade mirror (API/pid.c) on 1D axis in cm, cm/s, cm/s^2."""

    def __init__(self, kp_pos: float = 1.2, kp_vel: float = 2.2, ki_vel: float = 0.4, kd_vel: float = 0.08):
        self.kp_pos = kp_pos
        self.kp_vel = kp_vel
        self.ki_vel = ki_vel
        self.kd_vel = kd_vel
        self.sum_e = 0.0
        self.prev_v_err = 0.0

    def step(self, des: float, pos: float, vel: float, vff: float = 0.0, aff: float = 0.0) -> float:
        v_des = np.clip(self.kp_pos * (des - pos) + vff, -80.0, 80.0)
        v_err = v_des - vel
        if abs(des - pos) < 25.0:
            self.sum_e = float(np.clip(self.sum_e + DT * v_err, -80.0, 80.0))
        d_err = (v_err - self.prev_v_err) / DT
        self.prev_v_err = v_err
        u = self.kp_vel * v_err + self.ki_vel * self.sum_e + self.kd_vel * d_err + aff
        return float(np.clip(u, -xc.AMAX, xc.AMAX))


class SimPlant2D:
    """2D delayed lag+drag plant with the 10-24 lightly-damped ~0.47 Hz outer/inner disturbance excitation."""

    def __init__(self, m: dict, wobble_amp_cms2: float = 28.0):
        self.m = m
        self.d = int(m["d"])
        self.al = float(m["al"])
        self.k = float(m["k"])
        self.dr = float(m["drag"])
        self.pipe_x = np.zeros(self.d)
        self.pipe_y = np.zeros(self.d)
        self.pos = np.zeros(2)
        self.vel = np.zeros(2)
        self.a_state = np.zeros(2)
        self.wobble_amp = wobble_amp_cms2

    def step(self, ux: float, uy: float, t_s: float) -> tuple[np.ndarray, np.ndarray]:
        ux = float(np.clip(ux, -xc.AMAX, xc.AMAX))
        uy = float(np.clip(uy, -xc.AMAX, xc.AMAX))
        u_del = np.array([self.pipe_x[0], self.pipe_y[0]])
        self.pipe_x = np.roll(self.pipe_x, -1)
        self.pipe_y = np.roll(self.pipe_y, -1)
        self.pipe_x[-1] = ux
        self.pipe_y[-1] = uy

        # 0.47 Hz hover wobble disturbance + 0.94 Hz harmonic observed in 10-24 figure-8 logs
        wob_x = self.wobble_amp * math.sin(2.0 * math.pi * 0.47 * t_s + 0.3)
        wob_y = self.wobble_amp * math.cos(2.0 * math.pi * 0.46 * t_s - 0.2)
        dist = np.array([wob_x, wob_y])

        self.a_state = self.al * self.a_state + (1.0 - self.al) * u_del
        acc = self.k * self.a_state - self.dr * self.vel + dist
        self.vel = self.vel + DT * acc
        self.pos = self.pos + DT * self.vel
        return self.pos.copy(), self.vel.copy()


def _fig8_eval(tau_s: float, move_s: float, ramp_s: float, min_jerk: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Evaluate 10-24 figure-8 (x 0.36 Hz, y 0.18 Hz during cruise; Gerono a=55 cm) at path time tau_s."""
    a_cm = 55.0
    path_s = move_s - ramp_s
    # f_y = 0.18 Hz -> om = 2 * pi * 0.18 rad/s -> laps = 0.18 * path_s = 3.24 laps in 18 s cruise
    om = 2.0 * math.pi * 0.18
    t = float(np.clip(tau_s, 0.0, move_s))
    R = ramp_s
    T = move_s
    if min_jerk:
        if t < R:
            u = t / R
            w = R * (u ** 4) * (2.5 - 3.0 * u + u ** 2)
            dw = (u ** 3) * (10.0 - 15.0 * u + 6.0 * (u ** 2))
            ddw = 30.0 * (u ** 2) * ((1.0 - u) ** 2) / R
        elif t > T - R:
            u = (T - t) / R
            w = (T - R) - R * (u ** 4) * (2.5 - 3.0 * u + u ** 2)
            dw = (u ** 3) * (10.0 - 15.0 * u + 6.0 * (u ** 2))
            ddw = -30.0 * (u ** 2) * ((1.0 - u) ** 2) / R
        else:
            w, dw, ddw = 0.5 * R + (t - R), 1.0, 0.0
    else:
        if t < R:
            w = 0.5 * t - R / (2.0 * math.pi) * math.sin(math.pi * t / R)
            dw = 0.5 * (1.0 - math.cos(math.pi * t / R))
            ddw = 0.5 * (math.pi / R) * math.sin(math.pi * t / R)
        elif t > T - R:
            u = T - t
            w = (T - R) - (0.5 * u - R / (2.0 * math.pi) * math.sin(math.pi * u / R))
            dw = 0.5 * (1.0 - math.cos(math.pi * u / R))
            ddw = -0.5 * (math.pi / R) * math.sin(math.pi * u / R)
        else:
            w, dw, ddw = 0.5 * R + (t - R), 1.0, 0.0

    th = om * w
    dth = om * dw
    ddth = om * ddw
    s1, c1 = math.sin(th), math.cos(th)
    s2, c2 = math.sin(2.0 * th), math.cos(2.0 * th)
    pos = np.array([0.5 * a_cm * s2, a_cm * s1])
    dx_dth, dy_dth = a_cm * c2, a_cm * c1
    d2x_dth2, d2y_dth2 = -2.0 * a_cm * s2, -a_cm * s1
    vel = np.array([dx_dth * dth, dy_dth * dth])
    acc = np.array([d2x_dth2 * (dth ** 2) + dx_dth * ddth, d2y_dth2 * (dth ** 2) + dy_dth * ddth])
    return pos, vel, acc


def _square_eval(tau_s: float, move_s: float, min_jerk: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Evaluate 4-segment waypoint square (50 x 50 cm, move_s=16 s, 4 s per side) at path time tau_s."""
    corners = np.array([
        [0.0, 0.0],
        [50.0, 0.0],
        [50.0, 50.0],
        [0.0, 50.0],
        [0.0, 0.0],
    ])
    seg_T = move_s / 4.0
    t = float(np.clip(tau_s, 0.0, move_s - 1e-9))
    seg = min(int(t // seg_T), 3)
    t_local = t - seg * seg_T
    p0, p1 = corners[seg], corners[seg + 1]
    if min_jerk:
        z = np.zeros(2)
        pos, vel, acc = tm.quintic_segment(t_local, seg_T, p0, z, z, p1, z, z)
        return np.asarray(pos, float), np.asarray(vel, float), np.asarray(acc, float)
    # Linear constant-velocity segment with 0.4 s cosine corner blend
    u = np.clip(t_local / seg_T, 0.0, 1.0)
    pos = p0 + u * (p1 - p0)
    vel = (p1 - p0) / seg_T
    acc = np.zeros(2)
    return pos, vel, acc


def _make_pair(ctrl_kind: str, pred_lead: bool):
    if ctrl_kind == "pid":
        return PidCascade1D(), PidCascade1D()
    if ctrl_kind == "lqr":
        cx, cy = xc.Lqr(dict(PLANT_M), LQR_K_HEADER, 1), xc.Lqr(dict(PLANT_M), LQR_K_HEADER, 1)
    elif ctrl_kind == "mpc":
        cx, cy = xc.Mpc(dict(PLANT_M), MPC_Q, MPC_RD, 1), xc.Mpc(dict(PLANT_M), MPC_Q, MPC_RD, 1)
    elif ctrl_kind == "adrc":
        cx, cy = xc.Adrc(dict(PLANT_M), ADRC_WC, ADRC_WO, ADRC_B0, 1), xc.Adrc(dict(PLANT_M), ADRC_WC, ADRC_WO, ADRC_B0, 1)
    else:
        raise ValueError(ctrl_kind)
    cx.pred_lead = pred_lead
    cy.pred_lead = pred_lead
    return cx, cy


def run_single(shape: str, ctrl_kind: str, mode: str) -> dict:
    use_slow = mode in ("slow_clock", "all")
    use_mj_aff = mode in ("min_jerk_aff", "all")
    use_pred = mode in ("pred_fix", "all")

    move_s = 20.0 if shape == "figure8" else 16.0
    ramp_s = 2.0
    max_wall_s = 28.0 if shape == "figure8" else 22.0
    n_steps = int(round(max_wall_s / DT))

    plant = SimPlant2D(PLANT_M, wobble_amp_cms2=(26.0 if shape == "figure8" else 14.0))
    cx, cy = _make_pair(ctrl_kind, use_pred)

    tau = 0.0
    s_val = 1.0
    ds_dt = 0.0
    d_ticks = PLANT_M["d"]

    t_log = np.zeros(n_steps)
    tau_log = np.zeros(n_steps)
    s_log = np.zeros(n_steps)
    ref_log = np.zeros((n_steps, 2))
    pos_log = np.zeros((n_steps, 2))
    err_log = np.zeros(n_steps)

    eval_fn = (lambda ts: _fig8_eval(ts, move_s, ramp_s, use_mj_aff)) if shape == "figure8" else (
        lambda ts: _square_eval(ts, move_s, use_mj_aff)
    )

    for k in range(n_steps):
        t_wall = k * DT
        ref_p, ref_v_path, ref_a_path = eval_fn(tau)
        err_now = float(np.hypot(plant.pos[0] - ref_p[0], plant.pos[1] - ref_p[1]))

        if use_slow and tau < move_s:
            s_val, ds_dt = tm.slow_clock_step(
                s_val, err_now, e_lo_cm=PROPOSED_E_LO_CM, e_hi_cm=PROPOSED_E_HI_CM,
                recover_rate=PROPOSED_RECOVER_PER_S, dt=DT,
            )
        else:
            s_val, ds_dt = 1.0, 0.0

        vff = s_val * ref_v_path
        aff = (s_val ** 2) * ref_a_path + ds_dt * ref_v_path if use_mj_aff else np.zeros(2)

        fut_x = fut_y = None
        if use_pred:
            tau_fut = min(tau + s_val * d_ticks * DT, move_s)
            fp, fv_p, fa_p = eval_fn(tau_fut)
            fv = s_val * fv_p
            fa = (s_val ** 2) * fa_p if use_mj_aff else np.zeros(2)
            fut_x = (np.array([fp[0]]), np.array([fv[0]]), np.array([fa[0]]))
            fut_y = (np.array([fp[1]]), np.array([fv[1]]), np.array([fa[1]]))

        if ctrl_kind == "pid":
            ux = cx.step(ref_p[0], plant.pos[0], plant.vel[0], vff=vff[0], aff=aff[0])
            uy = cy.step(ref_p[1], plant.pos[1], plant.vel[1], vff=vff[1], aff=aff[1])
        else:
            ux = float(cx.step(np.array([ref_p[0]]), np.array([plant.pos[0]]), np.array([plant.vel[0]]),
                               vff=np.array([vff[0]]), aff=np.array([aff[0]]), fut_ref=fut_x)[0])
            uy = float(cy.step(np.array([ref_p[1]]), np.array([plant.pos[1]]), np.array([plant.vel[1]]),
                               vff=np.array([vff[1]]), aff=np.array([aff[1]]), fut_ref=fut_y)[0])

        pos_now, _ = plant.step(ux, uy, t_wall)

        t_log[k] = t_wall
        tau_log[k] = tau
        s_log[k] = s_val
        ref_log[k] = ref_p
        pos_log[k] = pos_now
        err_log[k] = float(np.hypot(pos_now[0] - ref_p[0], pos_now[1] - ref_p[1]))

        if tau < move_s:
            tau = min(move_s, tau + s_val * DT)

    # Active move window (up to when tau reaches move_s + 1.0 s settle)
    done_idx = np.where(tau_log >= move_s - 1e-4)[0]
    end_k = min(len(t_log), int(done_idx[0]) + 100) if len(done_idx) else len(t_log)
    t_m = t_log[:end_k]
    ref_m = ref_log[:end_k]
    pos_m = pos_log[:end_k]
    err_m = err_log[:end_k]
    s_m = s_log[:end_k]

    ac = tm.along_cross_rmse(ref_m, pos_m)
    if shape == "square":
        wps = [(50.0, 0.0, 4.0), (50.0, 50.0, 8.0), (0.0, 50.0, 12.0), (0.0, 0.0, 16.0)]
    else:
        wps = []
        for q, tp_s in enumerate((3.5, 6.5, 9.5, 12.5, 15.5, 18.5), 1):
            p_wp, _, _ = eval_fn(tp_s)
            wps.append((float(p_wp[0]), float(p_wp[1]), float(tp_s)))

    arrivals = tm.parse_waypoint_arrivals(t_m, pos_m, ref_m, wps, r_cm=PROPOSED_ARRIVE_R_CM, s_trace=s_m)
    lags = [a.lag_s for a in arrivals if a.arrived and math.isfinite(a.lag_s)]

    # Spectrum in 0.40 - 0.55 Hz band
    freqs = np.fft.rfftfreq(len(err_m), d=DT)
    spec = np.abs(np.fft.rfft(err_m - np.mean(err_m))) * (2.0 / max(len(err_m), 1))
    band = (freqs >= 0.40) & (freqs <= 0.55)
    band_peak = float(np.max(spec[band])) if np.any(band) else 0.0

    return {
        "shape": shape,
        "ctrl": ctrl_kind,
        "mode": mode,
        "t": t_m,
        "tau": tau_log[:end_k],
        "s": s_m,
        "ref": ref_m,
        "pos": pos_m,
        "err": err_m,
        "freqs": freqs,
        "spec": spec,
        "rmse_cm": float(np.sqrt(np.mean(err_m ** 2))),
        "peak_err_cm": float(np.max(err_m)),
        "along_rmse_cm": ac["along_rmse"],
        "cross_rmse_cm": ac["cross_rmse"],
        "mean_lag_s": float(np.mean(lags)) if lags else float("nan"),
        "max_lag_s": float(np.max(lags)) if lags else float("nan"),
        "time_lost_s": float(np.sum((1.0 - s_m) * DT)),
        "wobble_045_050_cm": band_peak,
        "arrivals": arrivals,
    }


def _save_fig_and_copy(fig, name: str, caption: str, csv_rows: list[dict], dirs: list[Path]) -> None:
    first_dir = dirs[0]
    first_dir.mkdir(parents=True, exist_ok=True)
    png_path = first_dir / f"{name}.png"
    cap_path = first_dir / f"{name}.caption.txt"
    csv_path = first_dir / f"{name}.csv"
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    cap_path.write_text(caption.strip() + "\n", encoding="utf-8")
    if csv_rows:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            w.writeheader()
            w.writerows(csv_rows)
    for d in dirs[1:]:
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(png_path, d / png_path.name)
        shutil.copy2(cap_path, d / cap_path.name)
        if csv_rows:
            shutil.copy2(csv_path, d / csv_path.name)


def generate_all(out_dirs: list[Path]) -> dict:
    results: dict[tuple[str, str, str], dict] = {}
    for shape in ("figure8", "square"):
        for ctrl in CONTROLLERS:
            for mode in MODES:
                results[(shape, ctrl, mode)] = run_single(shape, ctrl, mode)

    # 1. Figure-8 XY path plot
    fig, axes = plt.subplots(2, 2, figsize=(11, 9), sharex=True, sharey=True)
    csv_f8_xy = []
    for ax, ctrl in zip(axes.ravel(), CONTROLLERS):
        ref = results[("figure8", ctrl, "today")]["ref"]
        ax.plot(ref[:, 0], ref[:, 1], "k--", lw=1.6, label="Reference")
        for mode, col in [("today", "#d62728"), ("slow_clock", "#ff7f0e"),
                          ("min_jerk_aff", "#2ca02c"), ("pred_fix", "#9467bd"), ("all", "#1f77b4")]:
            r = results[("figure8", ctrl, mode)]
            ax.plot(r["pos"][:, 0], r["pos"][:, 1], color=col, lw=1.2, alpha=0.88,
                    label=f"{mode} (RMSE {r['rmse_cm']:.1f} cm)")
            for k in range(0, len(r["t"]), 5):
                csv_f8_xy.append({
                    "ctrl": ctrl, "mode": mode, "t_s": round(float(r["t"][k]), 3),
                    "ref_x_cm": round(float(r["ref"][k, 0]), 3), "ref_y_cm": round(float(r["ref"][k, 1]), 3),
                    "pos_x_cm": round(float(r["pos"][k, 0]), 3), "pos_y_cm": round(float(r["pos"][k, 1]), 3),
                })
        ax.set_title(f"10-24 Figure-8 — {ctrl.upper()}")
        ax.set_xlabel("x [cm]")
        ax.set_ylabel("y [cm]")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=7, loc="upper right")
    fig.suptitle("10-24 Figure-8 (0.36/0.18 Hz) XY Tracking Across Controllers and Modes", fontsize=12)
    _save_fig_and_copy(
        fig, "fig8_path_xy",
        "10-24 figure-8 XY path comparison (PID, LQR, MPC, ADRC): today vs slow clock vs min-jerk+AFF vs predictor lead vs all combined.",
        csv_f8_xy, out_dirs,
    )

    # 2. Waypoint Square XY path plot
    fig, axes = plt.subplots(2, 2, figsize=(11, 9), sharex=True, sharey=True)
    csv_sq_xy = []
    for ax, ctrl in zip(axes.ravel(), CONTROLLERS):
        ref = results[("square", ctrl, "today")]["ref"]
        ax.plot(ref[:, 0], ref[:, 1], "k--", lw=1.6, label="Reference square")
        for mode, col in [("today", "#d62728"), ("slow_clock", "#ff7f0e"),
                          ("min_jerk_aff", "#2ca02c"), ("pred_fix", "#9467bd"), ("all", "#1f77b4")]:
            r = results[("square", ctrl, mode)]
            ax.plot(r["pos"][:, 0], r["pos"][:, 1], color=col, lw=1.2, alpha=0.88,
                    label=f"{mode} (RMSE {r['rmse_cm']:.1f} cm)")
            for k in range(0, len(r["t"]), 5):
                csv_sq_xy.append({
                    "ctrl": ctrl, "mode": mode, "t_s": round(float(r["t"][k]), 3),
                    "ref_x_cm": round(float(r["ref"][k, 0]), 3), "ref_y_cm": round(float(r["ref"][k, 1]), 3),
                    "pos_x_cm": round(float(r["pos"][k, 0]), 3), "pos_y_cm": round(float(r["pos"][k, 1]), 3),
                })
        ax.set_title(f"Waypoint Square (50x50 cm) — {ctrl.upper()}")
        ax.set_xlabel("x [cm]")
        ax.set_ylabel("y [cm]")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=7, loc="upper right")
    fig.suptitle("Waypoint Square XY Tracking Across Controllers and Modes", fontsize=12)
    _save_fig_and_copy(
        fig, "square_path_xy",
        "50x50 cm waypoint square XY path comparison (PID, LQR, MPC, ADRC) across all 5 trajectory/controller modes.",
        csv_sq_xy, out_dirs,
    )

    # 3. Tracking Error vs Time
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True)
    csv_err_t = []
    for ax, ctrl in zip(axes.ravel(), CONTROLLERS):
        for mode, col in [("today", "#d62728"), ("slow_clock", "#ff7f0e"),
                          ("min_jerk_aff", "#2ca02c"), ("pred_fix", "#9467bd"), ("all", "#1f77b4")]:
            r = results[("figure8", ctrl, mode)]
            ax.plot(r["t"], r["err"], color=col, lw=1.1, label=f"{mode} (pk {r['peak_err_cm']:.1f} cm)")
            for k in range(0, len(r["t"]), 5):
                csv_err_t.append({
                    "ctrl": ctrl, "mode": mode, "t_s": round(float(r["t"][k]), 3),
                    "err_cm": round(float(r["err"][k]), 3),
                })
        ax.axhline(PROPOSED_E_LO_CM, color="gray", ls=":", lw=0.9, label="e_lo=15 cm (PROPOSED)")
        ax.axhline(PROPOSED_E_HI_CM, color="black", ls="--", lw=0.9, label="e_hi=45 cm (PROPOSED)")
        ax.set_title(f"Figure-8 Position Error — {ctrl.upper()}")
        ax.set_xlabel("Wall time [s]")
        ax.set_ylabel("||pos - ref|| [cm]")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=7, loc="upper right")
    fig.suptitle("Position Tracking Error vs Time on 10-24 Figure-8", fontsize=12)
    _save_fig_and_copy(
        fig, "error_vs_time",
        "Position error ||pos - ref|| [cm] vs time on the 10-24 figure-8 showing slow-clock error clamping between e_lo=15 cm and e_hi=45 cm (PROPOSED).",
        csv_err_t, out_dirs,
    )

    # 4. Slow clock scale s(t) vs Time
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    csv_s_t = []
    for ctrl, col in [("pid", "#d62728"), ("lqr", "#1f77b4"), ("mpc", "#2ca02c"), ("adrc", "#ff7f0e")]:
        r_sc = results[("figure8", ctrl, "slow_clock")]
        r_all = results[("figure8", ctrl, "all")]
        axes[0].plot(r_sc["t"], r_sc["s"], color=col, lw=1.2, label=f"{ctrl.upper()} slow_clock (lost {r_sc['time_lost_s']:.2f} s)")
        axes[1].plot(r_all["t"], r_all["s"], color=col, lw=1.2, label=f"{ctrl.upper()} all combined (lost {r_all['time_lost_s']:.2f} s)")
        for k in range(0, len(r_sc["t"]), 5):
            csv_s_t.append({
                "ctrl": ctrl, "mode": "slow_clock", "t_s": round(float(r_sc["t"][k]), 3), "s": round(float(r_sc["s"][k]), 4),
            })
        for k in range(0, len(r_all["t"]), 5):
            csv_s_t.append({
                "ctrl": ctrl, "mode": "all", "t_s": round(float(r_all["t"][k]), 3), "s": round(float(r_all["s"][k]), 4),
            })
    axes[0].set_title("Slow-Clock Factor s(t) — Slow Clock Alone")
    axes[0].set_ylabel("s(t) [-]")
    axes[0].set_ylim(-0.05, 1.08)
    axes[0].grid(True, alpha=0.3)
    axes[0].legend(fontsize=8)
    axes[1].set_title("Slow-Clock Factor s(t) — All Improvements Combined (Slow Clock + Min-Jerk + AFF + Predictor Fix)")
    axes[1].set_xlabel("Wall time [s]")
    axes[1].set_ylabel("s(t) [-]")
    axes[1].set_ylim(-0.05, 1.08)
    axes[1].grid(True, alpha=0.3)
    axes[1].legend(fontsize=8)
    fig.suptitle("Reference Clock Scaling s(t) During 10-24 Figure-8", fontsize=12)
    _save_fig_and_copy(
        fig, "slow_clock_s_t",
        "Slow-clock reference speed factor s(t) in [0, 1] with rate-limited recovery (1.0/s PROPOSED) for PID, LQR, MPC, and ADRC.",
        csv_s_t, out_dirs,
    )

    # 5. Waypoint Arrival Lag Bars (planned vs actual arrival & lag per waypoint)
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    csv_lag = []
    x_idx = np.arange(len(MODES))
    width = 0.18
    for i, ctrl in enumerate(CONTROLLERS):
        lags_f8 = [results[("figure8", ctrl, m)]["mean_lag_s"] for m in MODES]
        lags_sq = [results[("square", ctrl, m)]["mean_lag_s"] for m in MODES]
        axes[0].bar(x_idx + (i - 1.5) * width, lags_f8, width=width, label=ctrl.upper())
        axes[1].bar(x_idx + (i - 1.5) * width, lags_sq, width=width, label=ctrl.upper())
        for m in MODES:
            for sh in ("figure8", "square"):
                for a in results[(sh, ctrl, m)]["arrivals"]:
                    csv_lag.append({
                        "shape": sh, "ctrl": ctrl, "mode": m, "wp_idx": a.wp_idx,
                        "t_plan_s": round(a.t_plan_s, 3),
                        "t_actual_s": round(a.t_actual_s, 3) if math.isfinite(a.t_actual_s) else -1.0,
                        "lag_s": round(a.lag_s, 3) if math.isfinite(a.lag_s) else -1.0,
                        "peak_err_cm": round(a.peak_err_cm, 2),
                        "time_lost_s": round(a.time_lost_s, 3),
                    })
    for ax, title in [(axes[0], "10-24 Figure-8 Mean Waypoint Arrival Lag"),
                      (axes[1], "Waypoint Square Mean Waypoint Arrival Lag")]:
        ax.set_xticks(x_idx)
        ax.set_xticklabels(MODES, rotation=20)
        ax.set_ylabel("Arrival lag (t_actual - t_plan) [s]")
        ax.set_title(title)
        ax.grid(True, axis="y", alpha=0.3)
        ax.legend(fontsize=8)
    fig.suptitle(f"Waypoint Arrival Lag Across Modes (Arrival Radius r={PROPOSED_ARRIVE_R_CM:.0f} cm PROPOSED)", fontsize=12)
    _save_fig_and_copy(
        fig, "arrival_lag_bars",
        "Mean waypoint arrival lag (actual minus planned arrival time within r=15 cm PROPOSED) across controllers and modes on the figure-8 and waypoint square.",
        csv_lag, out_dirs,
    )

    # 6. Spectrum of tracking error around 0.45 - 0.50 Hz
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True, sharey=True)
    csv_spec = []
    for ax, ctrl in zip(axes.ravel(), CONTROLLERS):
        for mode, col in [("today", "#d62728"), ("slow_clock", "#ff7f0e"),
                          ("min_jerk_aff", "#2ca02c"), ("pred_fix", "#9467bd"), ("all", "#1f77b4")]:
            r = results[("figure8", ctrl, mode)]
            mask = r["freqs"] <= 1.2
            ax.plot(r["freqs"][mask], r["spec"][mask], color=col, lw=1.2,
                    label=f"{mode} (0.45-0.5Hz pk {r['wobble_045_050_cm']:.1f} cm)")
            for f_val, s_val in zip(r["freqs"][mask], r["spec"][mask]):
                csv_spec.append({
                    "ctrl": ctrl, "mode": mode, "freq_hz": round(float(f_val), 4),
                    "amp_cm": round(float(s_val), 4),
                })
        ax.axvspan(0.45, 0.50, color="gold", alpha=0.28, label="0.45-0.50 Hz wobble band")
        ax.set_title(f"Error Spectrum — {ctrl.upper()}")
        ax.set_xlabel("Frequency [Hz]")
        ax.set_ylabel("Error amplitude [cm]")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=7, loc="upper right")
    fig.suptitle("Tracking Error Spectrum Around 0.45-0.50 Hz Wobble Band (10-24 Figure-8)", fontsize=12)
    _save_fig_and_copy(
        fig, "error_spectrum_045_050hz",
        "Single-sided amplitude spectrum of position tracking error on the 10-24 figure-8 highlighting the 0.45-0.50 Hz hover-wobble resonance band.",
        csv_spec, out_dirs,
    )

    # Save JSON summary and markdown arrival table in both output directories
    summary_rows = []
    for (shape, ctrl, mode), r in results.items():
        summary_rows.append({
            "shape": shape,
            "ctrl": ctrl,
            "mode": mode,
            "rmse_cm": round(r["rmse_cm"], 2),
            "peak_err_cm": round(r["peak_err_cm"], 2),
            "along_rmse_cm": round(r["along_rmse_cm"], 2),
            "cross_rmse_cm": round(r["cross_rmse_cm"], 2),
            "mean_lag_s": round(r["mean_lag_s"], 3) if math.isfinite(r["mean_lag_s"]) else None,
            "time_lost_s": round(r["time_lost_s"], 3),
            "wobble_045_050_cm": round(r["wobble_045_050_cm"], 2),
        })
    summary = {
        "proposed_parameters": {
            "g_traj_slow_elo_cm": {"value": PROPOSED_E_LO_CM, "status": "PROPOSED"},
            "g_traj_slow_ehi_cm": {"value": PROPOSED_E_HI_CM, "status": "PROPOSED"},
            "slow_recover_rate_per_s": {"value": PROPOSED_RECOVER_PER_S, "status": "PROPOSED"},
            "g_traj_arrive_r_cm": {"value": PROPOSED_ARRIVE_R_CM, "status": "PROPOSED"},
        },
        "summary_rows": summary_rows,
    }
    for d in out_dirs:
        d.mkdir(parents=True, exist_ok=True)
        (d / "summary_metrics.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        md_lines = ["# Per-Flight Waypoint Arrival Tables (r = 15 cm PROPOSED)\n"]
        for ctrl in CONTROLLERS:
            for mode in ("today", "all"):
                r = results[("square", ctrl, mode)]
                md_lines.append(f"## Square — {ctrl.upper()} — {mode}\n")
                md_lines.append(tm.format_arrival_table(r["arrivals"]) + "\n")
        (d / "waypoint_arrival_table.md").write_text("\n".join(md_lines), encoding="utf-8")

    return summary


if __name__ == "__main__":
    main_out = Path(r"D:\Projects\UAV_lab\FreeRTOS-adaptive-controller-codex\.agent-ops\out\n26\fig")
    dirs = [main_out / "B", main_out / "traj"]
    s = generate_all(dirs)
    print(f"Generated 6 figures + CSV/JSON in {[str(d) for d in dirs]} ({len(s['summary_rows'])} sim runs).")
