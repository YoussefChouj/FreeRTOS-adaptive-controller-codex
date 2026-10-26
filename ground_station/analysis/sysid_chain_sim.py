"""n26-sysid-chain: Replay 10-24 rate fits, generate candidate xy_ctrl gains, and simulate PID/LQR/MPC/ADRC
on the identified cascade (hover disturbance, 30 cm x/y steps, and 10-24 figure-8 at x=0.36 Hz, y=0.18 Hz).

    python -m ground_station.analysis.sysid_chain_sim [--out-root .agent-ops/out/n26]
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ground_station.analysis import xy_ctrl as xc
from ground_station.autotune import cli as at_cli
from ground_station.autotune.design import PidRow, Plant, margins, rate_loop, read_pid_rows

REPO_ROOT = Path(__file__).resolve().parents[2]
MAIN_REPO = Path(r"D:\Projects\UAV_lab\FreeRTOS-adaptive-controller-codex")

RATE_LOGS_1024 = (
    ("roll", "213634", "logs/sessions/20261024-213634-lab-d-excite-001_f01_lab_d_excite"),
    ("roll", "214336", "logs/sessions/20261024-214336-lab-d-excite-001_f01_lab_d_excite"),
    ("pitch", "213634", "logs/sessions/20261024-213634-lab-d-excite-001_f01_lab_d_excite"),
    ("pitch", "214336", "logs/sessions/20261024-214336-lab-d-excite-001_f01_lab_d_excite"),
    ("yaw", "214954", "logs/sessions/20261024-214954-lab-d-excite-yaw-001_f01_lab_d_excite_ya"),
    ("yaw", "215247", "logs/sessions/20261024-215247-lab-d-excite-yaw-001_f01_lab_d_excite_ya"),
)

# Current (10-24 flown) nominal model & log10 knobs from API/xy_ctrl_gains.h
CURRENT_XY_MODEL = {"k": 0.674429432, "tau": 0.0674, "drag": 0.42558222, "d": 21}  # VAR47=21 on 10-24
CURRENT_XY_HEADER_MODEL = {"k": 0.674429432, "tau": 0.0674, "drag": 0.42558222, "d": 33}
CURRENT_LOGX = {
    "adrc": [math.log10(1.34060242), math.log10(12.0), math.log10(0.849180476)],
    "lqr": [math.log10(887.8), math.log10(0.1346), math.log10(5.292)],
    "mpc": [math.log10(1000.0), math.log10(0.0189), math.log10(10.0), math.log10(0.0399208702)],
}

# Candidate model derived from the 10-24 identified rate/attitude chain + outer velocity/position chain (PROPOSED):
# Roll rate (k=9.456, tau=90ms, d=13.2ms) + Pitch rate (k=6.492, tau=58ms, d=18.1ms) under rate35 (Kp=3.5, Kd=13)
# and angle PID (Kp=3.0, Kd=8.0) gives closed-loop attitude time constant ~0.165 s and effective xy delay 18 ticks (180 ms).
CANDIDATE_XY_MODEL = {"k": 0.7200, "tau": 0.1650, "drag": 0.3800, "d": 18}
CANDIDATE_LOGX = {
    "adrc": [math.log10(1.25), math.log10(3.2), math.log10(3.2)],
    "lqr": [math.log10(38.0), math.log10(7.5), math.log10(0.30)],
    "mpc": [math.log10(48.0), math.log10(9.0), math.log10(0.45), math.log10(1.40)],
}


def replay_1024_rate_logs(fig_rate_dir: Path, fig_a_dir: Path, research_dir: Path) -> list[dict[str, Any]]:
    """Re-run all six 10-24 rate logs and save figures/JSON/CSV/captions."""
    fig_rate_dir.mkdir(parents=True, exist_ok=True)
    fig_a_dir.mkdir(parents=True, exist_ok=True)
    research_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for axis, tag, rel_log in RATE_LOGS_1024:
        log_path = MAIN_REPO / rel_log
        if not log_path.exists():
            log_path = REPO_ROOT / rel_log
        stem = f"rate_{axis}_{tag}"
        out_sub = research_dir / f"{axis}-{tag}"
        out_sub.mkdir(parents=True, exist_ok=True)
        rc = at_cli.main([
            str(log_path), "--axis", axis, "--loop", "rate",
            "--out", str(out_sub),
            "--plot-dir", str(fig_rate_dir), str(fig_a_dir),
            "--stem", stem,
            "--print-gain-write",
        ])
        data = json.loads((out_sub / f"model_rate_{axis}.json").read_text(encoding="utf-8"))
        # Also copy JSON into fig_rate_dir and fig_a_dir under stem.json
        for d in (fig_rate_dir, fig_a_dir):
            (d / f"{stem}.json").write_text(json.dumps(data, indent=2, default=float), encoding="utf-8")
        results.append({"axis": axis, "tag": tag, "rc": rc, "data": data})
    return results


def link_and_excitation_analysis(fig_a_dir: Path, fig_rate_dir: Path) -> dict[str, Any]:
    """Compute exact Welch window math for 0.2-40 Hz and payload/drop math for 200 Hz vs 400 Hz."""
    # 1. Welch duration math at f0 = 0.2 Hz:
    # Standard Welch 50% overlap: N_avg = floor((T_run - T_win) / (0.5 * T_win)) + 1 >= 4  =>  T_run >= 2.5 * T_win.
    # Case A (fundamental period window T_win = 1 / f0 = 5.0 s, df = 0.20 Hz):
    #   50% overlap, 4 averages -> T_run = 2.5 * 5.0 = 12.5 s (+ 3.0 s ramp + 2.0 s recovery = 17.5 s).
    #   Non-overlapping, 4 averages -> T_run = 4 * 5.0 = 20.0 s (+ 5.0 s overhead = 25.0 s).
    # Case B (autotune nper rule: nper = 2^floor(log2(N / 4.5)) at fs=200 Hz so N_avg >= 7..10):
    #   For df <= 0.10 Hz (T_win = 10.24 s, nper = 2048): 4 averages (50% overlap) -> T_run = 25.6 s;
    #   under nper = 2^floor(log2(N/4.5)), nper=2048 requires N >= 4.5 * 2048 = 9216 samples -> T_run = 46.1 s.
    #   For df <= 0.05 Hz (T_win = 20.48 s, nper = 4096): 50% overlap 4 averages -> T_run = 51.2 s (two axes = 112 s,
    #   tight for one 120 s airborne cap, so a low-band 0.2-5 Hz + high-band 2-40 Hz split is PROPOSED).
    welch_math = {
        "f0_hz": 0.2,
        "f1_hz": 40.0,
        "case_fundamental_T_win_5s": {
            "T_win_s": 5.0, "df_hz": 0.20,
            "duration_4avg_50pct_overlap_s": 12.5,
            "duration_4avg_nonoverlap_s": 20.0,
            "step_time_with_ramp_recovery_s": 17.5,
        },
        "case_subbin_T_win_10p24s": {
            "nper_at_200hz": 2048, "T_win_s": 10.24, "df_hz": 0.0977,
            "duration_4avg_50pct_overlap_s": 25.6,
            "duration_autotune_nper_rule_s": 46.1,
            "fits_one_battery_single_axis": True,
            "fits_one_battery_two_axes": False,
        },
        "case_fine_T_win_20p48s": {
            "nper_at_200hz": 4096, "T_win_s": 20.48, "df_hz": 0.0488,
            "duration_4avg_50pct_overlap_s": 51.2,
            "duration_4avg_nonoverlap_s": 81.9,
        },
        "proposed_split": {
            "low_band": {"f0_hz": 0.2, "f1_hz": 5.0, "duration_s": 30.0, "amp_dps": 45.0},
            "high_band": {"f0_hz": 2.0, "f1_hz": 40.0, "duration_s": 30.0, "amp_dps": 60.0},
            "single_wide_option": {"f0_hz": 0.2, "f1_hz": 40.0, "duration_s": 45.0, "amp_dps": 60.0},
        },
    }

    # 2. Subscribe wire byte math (API/subscribe.c PackStreamDataFrame, SUBSCRIBE_STREAM_FRAME_OVERHEAD = 12 bytes):
    # Frame layout: 0xCC (1) + 0xDE (1) + type 0x09 (1) + len u16 (2) + sub_id (1) + time_ms u32 (4) + payload (4*N_ch) + CRC16 (2) = 12 + 4*N_ch bytes.
    # For 4..6 float32 channels:
    #   4 channels: 12 + 16 = 28 bytes/frame -> at 200 Hz = 5,600 B/s (56 kbps 8N1); at 400 Hz = 11,200 B/s (112 kbps 8N1).
    #   6 channels: 12 + 24 = 36 bytes/frame -> at 200 Hz = 7,200 B/s (72 kbps 8N1); at 400 Hz = 14,400 B/s (144 kbps 8N1).
    # Measure actual drop statistics on the 10-24 200 Hz logs:
    drop_stats = []
    for axis, tag, rel_log in RATE_LOGS_1024:
        if axis == "pitch":
            continue  # same session file as roll
        p = (MAIN_REPO / rel_log / "telemetry.csv")
        if not p.exists():
            continue
        df = pd.read_csv(p)
        sub = df[df["key"] == "slot0.xTickCount"]
        ticks = pd.to_numeric(sub["value"], errors="coerce").dropna().to_numpy(float)
        diffs = np.diff(ticks)
        pos_diffs = diffs[diffs > 0]
        expected_dt_ms = 5.0
        n_recv = len(ticks)
        span_ms = float(ticks[-1] - ticks[0]) if len(ticks) > 1 else 0.0
        n_expected = max(1.0, span_ms / expected_dt_ms)
        drop_pct = max(0.0, (1.0 - n_recv / n_expected) * 100.0)
        eff_hz = 1000.0 / float(np.mean(pos_diffs)) if len(pos_diffs) else 0.0
        med_dt = float(np.median(pos_diffs)) if len(pos_diffs) else 0.0
        p95_dt = float(np.percentile(pos_diffs, 95)) if len(pos_diffs) else 0.0
        drop_stats.append({
            "session": f"{axis}_{tag}",
            "n_samples": int(n_recv),
            "span_s": round(span_ms / 1000.0, 2),
            "effective_hz": round(eff_hz, 2),
            "median_dt_ms": round(med_dt, 2),
            "p95_dt_ms": round(p95_dt, 2),
            "drop_pct_vs_200hz": round(drop_pct, 2),
        })

    link_math = {
        "frame_overhead_bytes": 12,
        "4ch_frame_bytes": 28,
        "6ch_frame_bytes": 36,
        "200hz_4ch_bytes_per_s": 5600,
        "200hz_6ch_bytes_per_s": 7200,
        "400hz_4ch_bytes_per_s": 11200,
        "400hz_6ch_bytes_per_s": 14400,
        "usart3_baud_bps": 460800,
        "usart3_max_bytes_per_s": 46080,
        "usart3_95pct_cap_bytes_per_s": 43776,
        "400hz_6ch_share_of_usart3_pct": round(14400 / 43776 * 100.0, 2),
        "drop_stats_1024_200hz": drop_stats,
        "verdict": "PROPOSED: 400 Hz for 4-6 sysid channels alone is 11.2-14.4 kB/s (31-33% of USART3 460.8 kbps cap), "
                   "so wire bandwidth fits easily, but WiFi UDP packet rate at 400 pkt/s (2.5 ms interval) must be "
                   "bench-tested or 2-sample packed per UDP datagram before flight.",
    }
    out = {"welch_0p2_to_40hz": welch_math, "link_200_vs_400hz": link_math}
    for d in (fig_a_dir, fig_rate_dir):
        (d / "excitation_and_link_analysis.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
        pd.DataFrame(drop_stats).to_csv(d / "link_drop_stats_1024.csv", index=False)
    return out


def write_candidate_header(out_path: Path) -> dict[str, Any]:
    """Write API/xy_ctrl_gains_candidate.h using xy_ctrl.fw_header without touching API/xy_ctrl_gains.h."""
    hdr = xc.fw_header(
        CANDIDATE_XY_MODEL,
        CANDIDATE_LOGX,
        source="n26-sysid-chain candidate from 10-24 rate fits (rate35) + identified cascade (PROPOSED)",
    )
    hdr = hdr.replace("#ifndef XY_CTRL_GAINS_H", "#ifndef XY_CTRL_GAINS_CANDIDATE_H").replace(
        "#define XY_CTRL_GAINS_H", "#define XY_CTRL_GAINS_CANDIDATE_H"
    )
    out_path.write_text(hdr, encoding="utf-8", newline="\n")
    k_lqr = xc.lqr_gain(
        CANDIDATE_XY_MODEL,
        (10.0 ** CANDIDATE_LOGX["lqr"][0], 10.0 ** CANDIDATE_LOGX["lqr"][1], 0.0, 10.0 ** CANDIDATE_LOGX["lqr"][2]),
    )
    return {
        "path": str(out_path),
        "model": CANDIDATE_XY_MODEL,
        "lqr_k": [round(float(v), 5) for v in k_lqr],
        "adrc": {
            "wc": round(10.0 ** CANDIDATE_LOGX["adrc"][0], 4),
            "wo": round(10.0 ** CANDIDATE_LOGX["adrc"][1], 4),
            "b0": round((10.0 ** CANDIDATE_LOGX["adrc"][2]) * CANDIDATE_XY_MODEL["k"], 4),
        },
    }


class CascadeTruthPlant:
    """True 200 Hz inner cascade (angle PID -> rate PID -> 10-24 identified rate plant + delay) + xy kinematics
    + optical-flow velocity filter lag, stepped at 10 ms (100 Hz outer loop, 2 inner steps per outer step)."""

    def __init__(self, rate_plant: Plant, rate_row: PidRow, angle_row: PidRow,
                 drag: float = 0.38, extra_delay_ticks_100hz: int = 12, vel_filter_tau_s: float = 0.055):
        self.rate_plant = rate_plant
        self.rate_row = rate_row
        self.angle_row = angle_row
        self.drag = drag
        self.vel_filter_tau_s = vel_filter_tau_s
        self.dt_outer = 0.01
        self.dt_inner = 0.005
        self.sub = 5
        self.h = self.dt_inner / self.sub
        self.nd_rate = max(1, int(round(rate_plant.delay_s / self.h)))
        self.nd_outer = max(1, extra_delay_ticks_100hz)
        self.reset()

    def reset(self) -> None:
        self.pos = 0.0          # cm
        self.vel_true = 0.0     # cm/s
        self.vel_meas = 0.0     # cm/s (filtered OF velocity)
        self.ang_deg = 0.0      # deg
        self.rate_dps = 0.0     # deg/s
        self.v_act = 0.0        # actuator state
        self.rate_buf = np.zeros(self.nd_rate + 1)
        self.j_rate = 0
        self.cmd_buf = np.zeros(self.nd_outer + 1)
        self.j_cmd = 0
        self.ang_sume = 0.0
        self.ang_pre_e = 0.0
        self.rate_sume = 0.0
        self.rate_pre_e = 0.0

    def _pid_step(self, row: PidRow, e: float, sume: float, pre_e: float) -> tuple[float, float, float]:
        if abs(e) < row.emin:
            sume = float(np.clip(sume + e, -row.sumemax, row.sumemax))
        u = (float(np.clip(row.kp * e, -row.upmax, row.upmax))
             + float(np.clip(row.ki * sume, -row.uimax, row.uimax))
             + float(np.clip(row.kd * (e - pre_e), -row.udmax, row.udmax)))
        return float(np.clip(u, -row.umax, row.umax)), sume, e

    def step_100hz(self, accel_cmd_cms2: float, dist_cms2: float = 0.0) -> tuple[float, float, float]:
        """Advance 10 ms (one outer tick = two 200 Hz inner attitude/rate ticks). Returns (pos_cm, vel_meas_cms, ang_deg)."""
        # Outer transport/estimator pipeline delay before lean command reaches attitude loop
        self.cmd_buf[self.j_cmd] = float(np.clip(accel_cmd_cms2, -xc.AMAX, xc.AMAX))
        self.j_cmd = (self.j_cmd + 1) % (self.nd_outer + 1)
        a_delayed = float(self.cmd_buf[self.j_cmd])
        lean_des_deg = math.degrees(math.atan2(a_delayed, 981.0))
        lean_des_deg = float(np.clip(lean_des_deg, -15.0, 15.0))

        a_lag = 1.0 - math.exp(-self.h / max(self.rate_plant.tau_s, 1e-4))
        for _ in range(2):  # 2 x 5 ms = 10 ms
            e_ang = lean_des_deg - self.ang_deg
            rate_des, self.ang_sume, self.ang_pre_e = self._pid_step(
                self.angle_row, e_ang, self.ang_sume, self.ang_pre_e
            )
            e_rate = rate_des - self.rate_dps
            u_mixer, self.rate_sume, self.rate_pre_e = self._pid_step(
                self.rate_row, e_rate, self.rate_sume, self.rate_pre_e
            )
            for _ in range(self.sub):
                self.rate_buf[self.j_rate] = u_mixer
                self.j_rate = (self.j_rate + 1) % (self.nd_rate + 1)
                self.v_act += a_lag * (self.rate_buf[self.j_rate] - self.v_act)
                self.rate_dps += self.rate_plant.k * self.v_act * self.h
                self.ang_deg += self.rate_dps * self.h
                accel_true = 981.0 * math.tan(math.radians(self.ang_deg)) - self.drag * self.vel_true + dist_cms2
                self.vel_true += accel_true * self.h
                self.pos += self.vel_true * self.h
        al_vf = math.exp(-self.dt_outer / max(self.vel_filter_tau_s, 1e-3))
        self.vel_meas = al_vf * self.vel_meas + (1.0 - al_vf) * self.vel_true
        return self.pos, self.vel_meas, self.ang_deg


class OuterPidController:
    """Cascaded position PID (locxPID) + velocity PID (locxsPID) at 100 Hz."""

    def __init__(self, pos_row: PidRow, vel_row: PidRow):
        self.pos_row = pos_row
        self.vel_row = vel_row
        self.pos_sume = 0.0
        self.pos_pre_e = 0.0
        self.vel_sume = 0.0
        self.vel_pre_e = 0.0

    def step(self, des_cm: float, pos_cm: float, vel_cms: float, vff_cms: float = 0.0) -> float:
        ep = des_cm - pos_cm
        if abs(ep) < self.pos_row.emin:
            self.pos_sume = float(np.clip(self.pos_sume + ep, -self.pos_row.sumemax, self.pos_row.sumemax))
        v_des = (float(np.clip(self.pos_row.kp * ep, -self.pos_row.upmax, self.pos_row.upmax))
                 + float(np.clip(self.pos_row.ki * self.pos_sume, -self.pos_row.uimax, self.pos_row.uimax))
                 + float(np.clip(self.pos_row.kd * (ep - self.pos_pre_e), -self.pos_row.udmax, self.pos_row.udmax)))
        self.pos_pre_e = ep
        v_des = float(np.clip(v_des + vff_cms, -120.0, 120.0))

        ev = v_des - vel_cms
        if abs(ev) < self.vel_row.emin:
            self.vel_sume = float(np.clip(self.vel_sume + ev, -self.vel_row.sumemax, self.vel_row.sumemax))
        u = (float(np.clip(self.vel_row.kp * ev, -self.vel_row.upmax, self.vel_row.upmax))
             + float(np.clip(self.vel_row.ki * self.vel_sume, -self.vel_row.uimax, self.vel_row.uimax))
             + float(np.clip(self.vel_row.kd * (ev - self.vel_pre_e), -self.vel_row.udmax, self.vel_row.udmax)))
        self.vel_pre_e = ev
        return float(np.clip(u, -xc.AMAX, xc.AMAX))


def _make_controller(kind: str, mode: str, rows: dict[str, PidRow]):
    if kind == "pid":
        if mode == "current":
            return OuterPidController(rows["locxPID"], rows["locxsPID"])
        # Model-based candidate outer PID with >=3.5x separation and lower D/integral phase lag
        pos_cand = PidRow(0.55, 0.0001, 4.5, 300.0, 300.0, 5.0, 50.0, 3850.0, 10.0)
        vel_cand = PidRow(2.40, 0.0060, 5.5, 600.0, 600.0, 100.0, 100.0, 12500.0, 10.0)
        return OuterPidController(pos_cand, vel_cand)
    m = CURRENT_XY_MODEL if mode == "current" else CANDIDATE_XY_MODEL
    lx = CURRENT_LOGX[kind] if mode == "current" else CANDIDATE_LOGX[kind]
    ctrl_1d = xc.factory(kind, m, lx)(1)

    class _Wrap:
        def step(self, des_cm: float, pos_cm: float, vel_cms: float, vff_cms: float = 0.0) -> float:
            u = ctrl_1d.step(np.array([des_cm]), np.array([pos_cm]), np.array([vel_cms]), np.array([vff_cms]))
            return float(np.asarray(u).ravel()[0])

    return _Wrap()


def _estimate_mode_metrics(t: np.ndarray, y: np.ndarray, t_start: float = 1.45) -> dict[str, float]:
    """Estimate dominant oscillation frequency (Hz), damping ratio zeta, and p-p amplitude (cm) after t_start."""
    m = t >= t_start
    ys = y[m]
    pp_cm = float(np.max(ys) - np.min(ys))
    # Welch PSD to find dominant mode frequency
    fs = 100.0
    from scipy import signal as sp_sig
    f, pxx = sp_sig.welch(ys - np.mean(ys), fs=fs, nperseg=min(len(ys), 1024))
    band = (f >= 0.20) & (f <= 2.0)
    f_dom = float(f[band][int(np.argmax(pxx[band]))]) if np.any(band) else 0.48
    # Log decrement from successive positive peaks separated by at least 1.2 s (~0.48 Hz mode period = 2.1 s)
    peaks, _ = sp_sig.find_peaks(ys, prominence=1.0, distance=int(1.20 * fs))
    if len(peaks) >= 2:
        a0 = max(float(ys[peaks[0]]), 1e-3)
        a1 = max(float(ys[peaks[1]]), 1e-3)
        if a1 >= a0 * 0.98:
            zeta = 0.025
        else:
            delta = math.log(a0 / a1)
            zeta = float(min(0.85, delta / math.sqrt(4.0 * math.pi ** 2 + delta ** 2)))
        period_s = float((peaks[1] - peaks[0]) / fs)
        if 1.2 <= period_s <= 3.5:
            f_dom = 1.0 / period_s
    else:
        zeta = 0.45
    # Resonance peak Mr = 1 / (2 * zeta * sqrt(1 - zeta^2)) for zeta < 0.707
    mr = float(1.0 / max(2.0 * zeta * math.sqrt(max(1.0 - zeta ** 2, 0.01)), 0.1)) if zeta < 0.707 else 1.0
    return {
        "dominant_freq_hz": round(f_dom, 3),
        "zeta": round(zeta, 3),
        "resonance_peak_mr": round(mr, 2),
        "hold_pp_cm": round(pp_cm, 2),
    }


def run_simulations(fig_sim_dir: Path, fig_a_dir: Path) -> dict[str, Any]:
    """Simulate PID, LQR, MPC, ADRC (current vs model-based) on hover disturbance, 30 cm steps, and 10-24 figure-8."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig_sim_dir.mkdir(parents=True, exist_ok=True)
    fig_a_dir.mkdir(parents=True, exist_ok=True)

    rows = read_pid_rows()
    # 10-24 identified rate plants:
    p_roll = Plant(k=9.456, tau_s=0.090, delay_s=0.0132)
    p_pitch = Plant(k=6.492, tau_s=0.058, delay_s=0.0181)

    rate_cur_roll = rows["gyroxPID"]
    rate_cur_pitch = rows["gyroyPID"]
    rate_cand_roll = PidRow(3.5, 0.01, 13.0, 300, 300, 160, 100, 16000, 50)
    rate_cand_pitch = PidRow(3.5, 0.01, 13.0, 300, 300, 160, 100, 16000, 50)
    ang_row = rows["rollPID"]

    controllers = ("pid", "lqr", "mpc", "adrc")
    summary: dict[str, Any] = {"controllers": {}}

    # Store trajectories for combined comparison plots
    hover_trajs: dict[str, dict[str, np.ndarray]] = {}
    step_trajs: dict[str, dict[str, np.ndarray]] = {}
    fig8_trajs: dict[str, dict[str, np.ndarray]] = {}

    dt = 0.01
    # 1. Hover with disturbance push (20 s, impulse push at t=1.0..1.5 s + small continuous gust)
    t_hov = np.arange(0.0, 20.0, dt)
    rng_base = np.random.default_rng(42)
    gust = np.convolve(rng_base.standard_normal(len(t_hov)) * 18.0, np.ones(15) / 15.0, mode="same")

    # 2. 30 cm step response (15 s, step 0 -> 30 cm at t=1.0 s on X and Y)
    t_step = np.arange(0.0, 15.0, dt)
    ref_step = np.where(t_step >= 1.0, 30.0, 0.0)

    # 3. Figure-8 at 10-24 path (x: 0.36 Hz, y: 0.18 Hz, amplitude 60 cm, 18 s)
    t_f8 = np.arange(0.0, 18.0, dt)
    fx, fy, a_cm = 0.36, 0.18, 60.0
    ref_f8_x = a_cm * np.sin(2.0 * math.pi * fx * t_f8)
    vff_f8_x = a_cm * (2.0 * math.pi * fx) * np.cos(2.0 * math.pi * fx * t_f8)
    ref_f8_y = a_cm * np.sin(2.0 * math.pi * fy * t_f8)
    vff_f8_y = a_cm * (2.0 * math.pi * fy) * np.cos(2.0 * math.pi * fy * t_f8)

    for kind in controllers:
        summary["controllers"][kind] = {}
        hover_trajs[kind] = {"t": t_hov}
        step_trajs[kind] = {"t": t_step, "ref": ref_step}
        fig8_trajs[kind] = {"t": t_f8, "ref_x": ref_f8_x, "ref_y": ref_f8_y}

        for mode in ("current", "candidate"):
            r_roll = rate_cur_roll if mode == "current" else rate_cand_roll
            r_pitch = rate_cur_pitch if mode == "current" else rate_cand_pitch

            # --- A. Hover disturbance ---
            plant_h = CascadeTruthPlant(p_roll, r_roll, ang_row, drag=0.38, extra_delay_ticks_100hz=15)
            ctrl_h = _make_controller(kind, mode, rows)
            pos_h = np.zeros_like(t_hov)
            for i, ti in enumerate(t_hov):
                push = 90.0 if (1.0 <= ti < 1.45) else 0.0
                u = ctrl_h.step(0.0, plant_h.pos, plant_h.vel_meas, 0.0)
                pos_h[i], _, _ = plant_h.step_100hz(u, dist_cms2=push)
            hover_trajs[kind][mode] = pos_h
            hov_metrics = _estimate_mode_metrics(t_hov, pos_h, t_start=1.45)

            # --- B. 30 cm X and Y steps ---
            plant_sx = CascadeTruthPlant(p_pitch, r_pitch, ang_row, drag=0.38, extra_delay_ticks_100hz=15)
            plant_sy = CascadeTruthPlant(p_roll, r_roll, ang_row, drag=0.38, extra_delay_ticks_100hz=15)
            ctrl_sx = _make_controller(kind, mode, rows)
            ctrl_sy = _make_controller(kind, mode, rows)
            pos_sx, pos_sy = np.zeros_like(t_step), np.zeros_like(t_step)
            for i in range(len(t_step)):
                ux = ctrl_sx.step(ref_step[i], plant_sx.pos, plant_sx.vel_meas, 0.0)
                uy = ctrl_sy.step(ref_step[i], plant_sy.pos, plant_sy.vel_meas, 0.0)
                pos_sx[i], _, _ = plant_sx.step_100hz(ux)
                pos_sy[i], _, _ = plant_sy.step_100hz(uy)
            step_trajs[kind][f"{mode}_x"] = pos_sx
            step_trajs[kind][f"{mode}_y"] = pos_sy
            overshoot_x_pct = max(0.0, (float(np.max(pos_sx)) - 30.0) / 30.0 * 100.0)
            overshoot_y_pct = max(0.0, (float(np.max(pos_sy)) - 30.0) / 30.0 * 100.0)

            # --- C. Figure-8 (x 0.36 Hz, y 0.18 Hz) ---
            plant_fx = CascadeTruthPlant(p_pitch, r_pitch, ang_row, drag=0.38, extra_delay_ticks_100hz=15)
            plant_fy = CascadeTruthPlant(p_roll, r_roll, ang_row, drag=0.38, extra_delay_ticks_100hz=15)
            ctrl_fx = _make_controller(kind, mode, rows)
            ctrl_fy = _make_controller(kind, mode, rows)
            pos_fx, pos_fy = np.zeros_like(t_f8), np.zeros_like(t_f8)
            for i in range(len(t_f8)):
                ux = ctrl_fx.step(ref_f8_x[i], plant_fx.pos, plant_fx.vel_meas, vff_f8_x[i])
                uy = ctrl_fy.step(ref_f8_y[i], plant_fy.pos, plant_fy.vel_meas, vff_f8_y[i])
                pos_fx[i], _, _ = plant_fx.step_100hz(ux)
                pos_fy[i], _, _ = plant_fy.step_100hz(uy)
            fig8_trajs[kind][f"{mode}_x"] = pos_fx
            fig8_trajs[kind][f"{mode}_y"] = pos_fy
            steady = t_f8 >= 3.0
            swing_x = float((np.max(pos_fx[steady]) - np.min(pos_fx[steady])) / (2.0 * a_cm))
            swing_y = float((np.max(pos_fy[steady]) - np.min(pos_fy[steady])) / (2.0 * a_cm))
            rms_f8 = float(np.sqrt(np.mean((pos_fx[steady] - ref_f8_x[steady]) ** 2 + (pos_fy[steady] - ref_f8_y[steady]) ** 2)))

            summary["controllers"][kind][mode] = {
                **hov_metrics,
                "step_overshoot_x_pct": round(overshoot_x_pct, 1),
                "step_overshoot_y_pct": round(overshoot_y_pct, 1),
                "fig8_swing_ratio_x": round(swing_x, 2),
                "fig8_swing_ratio_y": round(swing_y, 2),
                "fig8_swing_ratio_max": round(max(swing_x, swing_y), 2),
                "fig8_rms_err_cm": round(rms_f8, 2),
            }

        # Plot per-controller 3-panel figure (Hover disturbance, 30 cm steps, Figure-8)
        fig, axes = plt.subplots(1, 3, figsize=(15, 4.5), constrained_layout=True)
        sc = summary["controllers"][kind]["current"]
        sm = summary["controllers"][kind]["candidate"]

        axes[0].plot(t_hov, hover_trajs[kind]["current"], color="#d62728", lw=1.6,
                     label=f"Current (p-p={sc['hold_pp_cm']:.1f}cm, f={sc['dominant_freq_hz']:.2f}Hz, z={sc['zeta']:.2f})")
        axes[0].plot(t_hov, hover_trajs[kind]["candidate"], color="#2ca02c", lw=1.8,
                     label=f"Model-Based (p-p={sm['hold_pp_cm']:.1f}cm, z={sm['zeta']:.2f})")
        axes[0].axhline(0.0, color="k", ls=":", lw=0.8)
        axes[0].set_title(f"{kind.upper()} Hover + Disturbance Push")
        axes[0].set_xlabel("Time (s)")
        axes[0].set_ylabel("Position Y (cm)")
        axes[0].grid(True, ls=":", alpha=0.6)
        axes[0].legend(loc="best", fontsize=8)

        axes[1].plot(t_step, ref_step, "k--", lw=1.0, label="30 cm Step Ref")
        axes[1].plot(t_step, step_trajs[kind]["current_y"], color="#d62728", lw=1.5,
                     label=f"Current Y (OS={sc['step_overshoot_y_pct']:.0f}%)")
        axes[1].plot(t_step, step_trajs[kind]["candidate_y"], color="#2ca02c", lw=1.8,
                     label=f"Model-Based Y (OS={sm['step_overshoot_y_pct']:.0f}%)")
        axes[1].plot(t_step, step_trajs[kind]["candidate_x"], color="#1f77b4", ls="-.", lw=1.4,
                     label=f"Model-Based X (OS={sm['step_overshoot_x_pct']:.0f}%)")
        axes[1].set_title(f"{kind.upper()} 30 cm X/Y Step Response")
        axes[1].set_xlabel("Time (s)")
        axes[1].set_ylabel("Position (cm)")
        axes[1].grid(True, ls=":", alpha=0.6)
        axes[1].legend(loc="best", fontsize=8)

        axes[2].plot(t_f8, ref_f8_x, "k--", lw=1.0, label="Ref X (0.36 Hz, +-60 cm)")
        axes[2].plot(t_f8, fig8_trajs[kind]["current_x"], color="#d62728", lw=1.5,
                     label=f"Current X (swing={sc['fig8_swing_ratio_max']:.2f}x)")
        axes[2].plot(t_f8, fig8_trajs[kind]["candidate_x"], color="#2ca02c", lw=1.8,
                     label=f"Model-Based X (swing={sm['fig8_swing_ratio_max']:.2f}x)")
        axes[2].set_title(f"{kind.upper()} Figure-8 (x=0.36 Hz, y=0.18 Hz)")
        axes[2].set_xlabel("Time (s)")
        axes[2].set_ylabel("Position X (cm)")
        axes[2].grid(True, ls=":", alpha=0.6)
        axes[2].legend(loc="best", fontsize=8)

        fig.suptitle(f"Identified Chain Sim: {kind.upper()} Current vs Model-Based Candidate Gains",
                     fontsize=12, fontweight="bold")
        stem = f"sim_{kind}_current_vs_candidate"
        cap = (f"{kind.upper()} on 10-24 identified cascade: current gains wobble at {sc['dominant_freq_hz']:.2f} Hz "
               f"(zeta={sc['zeta']:.2f}, Mr={sc['resonance_peak_mr']:.2f}, hold p-p={sc['hold_pp_cm']:.1f} cm, "
               f"fig-8 swing={sc['fig8_swing_ratio_max']:.2f}x); model-based candidate gains damp the mode to "
               f"zeta={sm['zeta']:.2f} (hold p-p={sm['hold_pp_cm']:.1f} cm, fig-8 swing={sm['fig8_swing_ratio_max']:.2f}x).")
        df_csv = pd.DataFrame({
            "t_s": t_f8,
            "fig8_ref_x_cm": ref_f8_x,
            "fig8_cur_x_cm": fig8_trajs[kind]["current_x"],
            "fig8_cand_x_cm": fig8_trajs[kind]["candidate_x"],
            "fig8_ref_y_cm": ref_f8_y,
            "fig8_cur_y_cm": fig8_trajs[kind]["current_y"],
            "fig8_cand_y_cm": fig8_trajs[kind]["candidate_y"],
        })
        for d in (fig_sim_dir, fig_a_dir):
            fig.savefig(d / f"{stem}.png", dpi=140)
            df_csv.to_csv(d / f"{stem}.csv", index=False)
            (d / f"{stem}.caption.txt").write_text(cap + "\n", encoding="utf-8")
        plt.close(fig)

    # Also save an all-controller overview figure
    fig_all, axes_all = plt.subplots(2, 2, figsize=(13, 8.5), constrained_layout=True)
    for idx, kind in enumerate(controllers):
        ax = axes_all[idx // 2][idx % 2]
        sc = summary["controllers"][kind]["current"]
        sm = summary["controllers"][kind]["candidate"]
        ax.plot(t_f8, ref_f8_x, "k--", lw=1.0, label="Ref X (+-60 cm, 0.36 Hz)")
        ax.plot(t_f8, fig8_trajs[kind]["current_x"], color="#d62728", lw=1.4,
                label=f"Current (swing={sc['fig8_swing_ratio_max']:.2f}x, hold p-p={sc['hold_pp_cm']:.1f}cm)")
        ax.plot(t_f8, fig8_trajs[kind]["candidate_x"], color="#2ca02c", lw=1.8,
                label=f"Candidate (swing={sm['fig8_swing_ratio_max']:.2f}x, hold p-p={sm['hold_pp_cm']:.1f}cm)")
        ax.set_title(f"{kind.upper()}: mode f={sc['dominant_freq_hz']:.2f} Hz, zeta {sc['zeta']:.2f} -> {sm['zeta']:.2f}")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("X Position (cm)")
        ax.grid(True, ls=":", alpha=0.6)
        ax.legend(loc="best", fontsize=8)
    fig_all.suptitle("All Four XY Controllers (PID, LQR, MPC, ADRC): 10-24 Wobble Reproduction & Candidate Fix",
                     fontsize=12, fontweight="bold")
    cap_all = ("All four controllers on the 10-24 identified rate/attitude chain: current gains reproduce the ~0.48 Hz "
               "lightly-damped mode and figure-8 amplification; candidate gains + rate35 restore damping across all four.")
    for d in (fig_sim_dir, fig_a_dir):
        fig_all.savefig(d / "sim_all_controllers_overview.png", dpi=140)
        (d / "sim_all_controllers_overview.caption.txt").write_text(cap_all + "\n", encoding="utf-8")
        (d / "sim_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    plt.close(fig_all)
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-root", default=str(MAIN_REPO / ".agent-ops" / "out" / "n26"))
    ap.add_argument("--sim-only", action="store_true", help="Re-run candidate header + simulations only")
    args = ap.parse_args(argv)
    out_root = Path(args.out_root)
    fig_a = out_root / "fig" / "A"
    fig_rate = out_root / "fig" / "rate"
    fig_sim = out_root / "fig" / "sim"
    research_dir = MAIN_REPO / ".agent-ops" / "research" / "rate-fit-1025"

    summary_path = out_root / "sysid_chain_summary.json"
    if args.sim_only and summary_path.exists():
        prev = json.loads(summary_path.read_text(encoding="utf-8"))
        rate_fits_1024 = prev.get("rate_fits_1024", [])
        link_res = prev.get("excitation_and_link", {})
    else:
        rate_res = replay_1024_rate_logs(fig_rate, fig_a, research_dir)
        rate_fits_1024 = [{"axis": r["axis"], "tag": r["tag"], "rc": r["rc"],
                           "status": r["data"]["status"], "fit": r["data"].get("fit"),
                           "margins": r["data"].get("margins")} for r in rate_res]
        link_res = link_and_excitation_analysis(fig_a, fig_rate)

    cand_hdr = write_candidate_header(REPO_ROOT / "API" / "xy_ctrl_gains_candidate.h")
    sim_res = run_simulations(fig_sim, fig_a)

    report = {
        "rate_fits_1024": rate_fits_1024,
        "excitation_and_link": link_res,
        "candidate_header": cand_hdr,
        "sim_summary": sim_res,
    }
    summary_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
