"""Tests for ground_station.analysis.layer_ab."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from scipy import signal

from ground_station.analysis import layer_ab as lab


def test_synthetic_loop_recovers_gain_and_phase_margin_within_10_percent():
    """Spec sec 2b test: a synthetic log of a known loop gives back its gain and phase margin within 10 %.

    Plant/controller open-loop transfer function:
      L(s) = K / (s * (1 + s / w1) * (1 + s / w2))
    with w1 = 2*pi*4 rad/s, w2 = 2*pi*10 rad/s, K = 12.0 s^-1.
    Analytical phase crossover (-180 deg):
      w_pc = sqrt(w1 * w2) -> f_pc = sqrt(4 * 10) = 6.3246 Hz (inside [0.5, 15] Hz!)
      |L(j w_pc)| = K * (w1 * w2) / (w_pc * (w1 + w2) * w_pc) = K / (w1 + w2)
      True Gain Margin GM = (w1 + w2) / K.
    """
    w1 = 2.0 * math.pi * 4.0
    w2 = 2.0 * math.pi * 10.0
    K = 12.0

    # Analytical GM and PM
    true_gm = (w1 + w2) / K
    # Find analytical gain crossover |L(j w_gc)| = 1
    ws = np.linspace(0.5 * 2 * math.pi, 15.0 * 2 * math.pi, 20000)
    s_grid = 1j * ws
    L_grid = K / (s_grid * (1.0 + s_grid / w1) * (1.0 + s_grid / w2))
    idx_gc = int(np.argmin(np.abs(np.abs(L_grid) - 1.0)))
    true_pm_deg = 180.0 + math.degrees(cmath_phase := float(np.angle(L_grid[idx_gc])))

    # Simulate closed-loop multisine response over T = 20 s at fs = 200 Hz
    dt = 0.005
    T_s = 20.0
    t = np.arange(0.0, T_s, dt)
    excited_freqs = [k / T_s for k in range(10, 300, 2)]  # 0.5 Hz .. 14.9 Hz harmonic grid
    rng = np.random.default_rng(42)
    phases = rng.uniform(0.0, 2.0 * math.pi, size=len(excited_freqs))
    d = np.zeros_like(t)
    for fk, ph in zip(excited_freqs, phases):
        d += np.cos(2.0 * math.pi * fk * t + ph)

    # Closed loop with U = Uc + d and Uc = -Y, where Y = L(s) U:
    # U(s) = 1 / (1 + L(s)) D(s), Uc(s) = -L(s) / (1 + L(s)) D(s)
    # L(s) = K * w1 * w2 / (s^3 + (w1+w2) s^2 + w1*w2 s)
    den_ol = [1.0, w1 + w2, w1 * w2, 0.0]
    num_ol = [K * w1 * w2]
    den_cl = [1.0, w1 + w2, w1 * w2, K * w1 * w2]

    sys_u = signal.TransferFunction(den_ol, den_cl)
    sys_uc = signal.TransferFunction([-K * w1 * w2], den_cl)
    _, u_sim, _ = signal.lsim(sys_u, U=d, T=t)
    _, uc_sim, _ = signal.lsim(sys_uc, U=d, T=t)

    # Use last period (steady state after 5 s transient)
    keep = t >= 5.0
    res = lab.stability_margins_multisine(t[keep], uc_sim[keep], u_sim[keep], excited_freqs_hz=excited_freqs)
    assert res["gm_status"] == "ok"
    assert res["pm_status"] == "ok"
    assert abs(res["gain_margin"] - true_gm) / true_gm < 0.10
    assert abs(res["phase_margin_deg"] - true_pm_deg) / true_pm_deg < 0.10


def test_crossover_outside_the_band_is_reported():
    t = np.arange(0.0, 10.0, 0.005)
    # Constant small loop gain |L| = 0.2, phase = -45 deg -> no |L|=1 and no -180 deg crossover in band
    u = np.cos(2.0 * math.pi * 2.0 * t) + np.cos(2.0 * math.pi * 5.0 * t)
    uc = -0.2 * (np.cos(2.0 * math.pi * 2.0 * t - math.pi / 4) + np.cos(2.0 * math.pi * 5.0 * t - math.pi / 4))
    res = lab.stability_margins_multisine(t, uc, u, excited_freqs_hz=[2.0, 5.0])
    assert res["gm_status"] == "crossover outside the band"
    assert res["pm_status"] == "crossover outside the band"


def _write_arm_session(root: Path, name: str, err_scale: float) -> Path:
    sess = root / "logs" / "sessions" / name
    sess.mkdir(parents=True, exist_ok=True)
    t = np.arange(0.0, 6.0, 0.02)
    lines = ["received_ns,slot,key,value"]
    for tk in t:
        ns = int(round(tk * 1e9))
        des = 10.0 * math.sin(2.0 * math.pi * 0.5 * tk)
        fb = des - err_scale * math.sin(2.0 * math.pi * 0.5 * tk)
        lines.append(f"{ns},0,flight_phase,1")
        lines.append(f"{ns},0,DroneStatus.ARM_Status,1")
        lines.append(f"{ns},0,traj_status,1")
        lines.append(f"{ns},0,Ctrler.locxPID.Des,{des:.4f}")
        lines.append(f"{ns},0,Ctrler.locxPID.FB,{fb:.4f}")
    (sess / "telemetry.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sess


def test_compare_arms_reports_within_noise_and_real_improvement(tmp_path):
    # Reference arm has two repeats with err_scale 1.0 and 1.2 (spread ~0.1414)
    _write_arm_session(tmp_path, "20261024-100000-xy-layer-inner-1", 1.0)
    _write_arm_session(tmp_path, "20261024-100500-xy-layer-inner-2", 1.2)
    # Outer arm has 1.05 (change vs ref mean 1.1 is 0.05 < spread -> "within noise")
    _write_arm_session(tmp_path, "20261024-101000-xy-layer-outer-1", 1.05)
    # Both arm has 0.5 (change vs ref mean 1.1 is 0.6 > spread -> significant % reduction)
    _write_arm_session(tmp_path, "20261024-101500-xy-layer-both-1", 0.5)

    res = lab.compare_arms(
        "2026-10-24",
        ["xy-layer-inner-1", "xy-layer-inner-2", "xy-layer-outer-1", "xy-layer-both-1"],
        ref="xy-layer-inner",
        root=tmp_path,
    )
    outer_m = res["arms"]["xy-layer-outer"]["metrics"]["pos_err_rms_f8_x"]
    both_m = res["arms"]["xy-layer-both"]["metrics"]["pos_err_rms_f8_x"]
    assert outer_m["verdict"] == "within noise"
    assert both_m["verdict"].endswith("%") and both_m["pct_vs_ref"] < -40.0


def test_missing_day_or_arm_is_a_clear_error(tmp_path, capsys):
    rc = lab.main(["2026-10-99", "--arms", "xy-layer-inner", "--ref", "pid-alone-ref", "--root", str(tmp_path)])
    assert rc != 0
    err = capsys.readouterr().err
    assert "layer_ab: no session data found" in err
