"""Tests for `ground_station.analysis.adapt_gate` (`n28` W4 Acceptance Test A8)."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from ground_station.analysis.adapt_gate import (
    PROPOSED_THRESHOLDS,
    VERDICT_DISABLE,
    VERDICT_OK,
    VERDICT_TOO_FAST,
    VERDICT_TOO_SLOW,
    AdaptProposal,
    evaluate_flight_folder,
    evaluate_layer_traces,
    format_gate_table,
)


def _time_grid(duration_s: float = 40.0, fs_hz: float = 50.0) -> np.ndarray:
    return np.arange(0.0, duration_s, 1.0 / fs_hz, dtype=np.float64)


def test_a8_synthetic_ok_trace_converges_without_saturation_or_oscillation(tmp_path: Path) -> None:
    t = _time_grid(40.0, 50.0)
    # Fast exponential settling (tau = 4 s) settles well before the last 30% of the 40 s hover (t >= 28 s).
    w0 = 0.12 * (1.0 - np.exp(-t / 4.0))
    w1 = -0.04 * (1.0 - np.exp(-t / 4.0))
    w = np.column_stack([w0, w1])
    u_ad = -0.8 * w0
    u_nom = np.full_like(t, 0.5)
    e_with = 0.03 * np.exp(-t / 5.0)
    e_without = np.full_like(t, 0.05)

    plot_file = tmp_path / "plots" / "ok.png"
    res = evaluate_layer_traces(
        t,
        w,
        u_ad,
        u_nom=u_nom,
        e_with=e_with,
        e_without=e_without,
        layer="rate_roll",
        current_gamma=1.5,
        w_max=0.50,
        u_max=6.73863,
        bw_hz=4.0,
        plot_path=plot_file,
    )
    assert res.verdict == VERDICT_OK
    assert res.gamma_multiplier == 1.0
    assert res.proposed_gamma == pytest.approx(1.5)
    assert len(res.knobs) == 1
    assert res.knob == "cmd_0x1d.gamma_scale.roll"
    assert res.w_tail_drift <= PROPOSED_THRESHOLDS["w_tail_drift_max"]
    assert res.u_sat_frac == 0.0 and res.w_sat_frac == 0.0
    assert 0.0 < res.adaptive_share < 1.0
    assert res.err_ratio is not None and res.err_ratio < 1.0
    assert plot_file.is_file()


def test_a8_synthetic_too_slow_trace_proposes_gamma_times_2() -> None:
    t = _time_grid(40.0, 50.0)
    # Very slow adaptation (linear creep across the entire 40 s hover -> large drift in last 30% [28..40 s]).
    w = 0.25 * (t / t[-1])
    u_ad = -0.4 * w
    u_nom = np.full_like(t, 0.6)
    e_with = np.full_like(t, 0.04)
    e_without = np.full_like(t, 0.05)

    res = evaluate_layer_traces(
        t,
        w,
        u_ad,
        u_nom=u_nom,
        e_with=e_with,
        e_without=e_without,
        layer="pos_xy",
        current_gamma=1.0,
        w_max=0.50,
        u_max=0.30,
        bw_hz=0.5,
    )
    assert res.verdict == VERDICT_TOO_SLOW
    assert res.gamma_multiplier == pytest.approx(2.0)
    assert res.proposed_gamma == pytest.approx(2.0)
    assert len(res.knobs) == 1
    assert res.knob == "mrac_pos_cfg.gamma"
    assert res.w_tail_drift > PROPOSED_THRESHOLDS["w_tail_drift_max"]


def test_a8_synthetic_too_fast_oscillation_above_half_bw_proposes_gamma_half() -> None:
    t = _time_grid(40.0, 50.0)
    # Loop bandwidth = 2.0 Hz -> 0.5x loop bandwidth cutoff = 1.0 Hz.
    # Inject a 3.5 Hz oscillation in W and u_ad (strictly above 1.0 Hz cutoff), below saturation.
    w = 0.08 * np.sin(2.0 * np.pi * 3.5 * t)
    u_ad = 0.15 * np.sin(2.0 * np.pi * 3.5 * t)
    u_nom = np.full_like(t, 0.3)
    e_with = np.full_like(t, 0.03)

    res = evaluate_layer_traces(
        t,
        w,
        u_ad,
        u_nom=u_nom,
        e_with=e_with,
        layer="vel_xy",
        current_gamma=2.0,
        w_max=0.25,
        u_max=1.00,
        bw_hz=2.0,
    )
    assert res.verdict == VERDICT_TOO_FAST
    assert res.gamma_multiplier == pytest.approx(0.5)
    assert res.proposed_gamma == pytest.approx(1.0)
    assert len(res.knobs) == 1
    assert res.knob == "cmd_0x1d.gamma_scale.x"
    assert res.osc_w_norm >= PROPOSED_THRESHOLDS["osc_w_max"] or res.osc_u_norm >= PROPOSED_THRESHOLDS["osc_u_max"]


def test_a8_synthetic_too_fast_saturation_proposes_gamma_half() -> None:
    t = _time_grid(40.0, 50.0)
    w = np.clip(0.8 * (1.0 - np.exp(-t / 1.5)), -0.50, 0.50)
    u_ad = np.clip(1.5 * w, -0.30, 0.30)

    res = evaluate_layer_traces(
        t,
        w,
        u_ad,
        layer="pos_xy",
        current_gamma=1.0,
        w_max=0.50,
        u_max=0.30,
        bw_hz=1.0,
    )
    assert res.verdict == VERDICT_TOO_FAST
    assert res.gamma_multiplier == pytest.approx(0.5)
    assert res.proposed_gamma == pytest.approx(0.5)
    assert len(res.knobs) == 1
    assert res.u_sat_frac >= PROPOSED_THRESHOLDS["u_sat_max"] or res.w_sat_frac >= PROPOSED_THRESHOLDS["w_sat_max"]


def test_a8_synthetic_disable_when_live_tracking_error_worse_than_shadow() -> None:
    t = _time_grid(40.0, 50.0)
    w = 0.05 * (1.0 - np.exp(-t / 3.0))
    u_ad = -0.05 * np.ones_like(t)
    u_nom = 0.20 * np.ones_like(t)
    e_without = np.full_like(t, 0.04)
    e_with = np.full_like(t, 0.09)  # >2x worse tracking error with the layer active

    res = evaluate_layer_traces(
        t,
        w,
        u_ad,
        u_nom=u_nom,
        e_with=e_with,
        e_without=e_without,
        layer="angle_att",
        current_gamma=1.0,
        w_max=2.0,
        u_max=30.0,
        bw_hz=1.5,
    )
    assert res.verdict == VERDICT_DISABLE
    assert res.gamma_multiplier == pytest.approx(0.0)
    assert res.proposed_gamma == pytest.approx(0.0)
    assert len(res.knobs) == 1
    assert res.knob == "cmd_0x1d.lam_ang.roll"
    assert res.err_ratio is not None and res.err_ratio > PROPOSED_THRESHOLDS["disable_err_ratio"]


def test_one_knob_one_layer_per_proposal_guard() -> None:
    with pytest.raises(ValueError, match="Exactly one knob"):
        AdaptProposal(
            layer="rate_roll",
            knob="gamma_roll, gamma_pitch",
            verdict=VERDICT_OK,
            gamma_multiplier=1.0,
            current_value=1.5,
            proposed_value=1.5,
        )


def test_evaluate_flight_folder_writes_plots_and_table(tmp_path: Path) -> None:
    flight_dir = tmp_path / "01_lab-l-adapt-vel-live"
    flight_dir.mkdir(parents=True)
    t = _time_grid(20.0, 25.0)
    w0 = 0.08 * (1.0 - np.exp(-t / 2.5))
    csv_path = flight_dir / "live_5hz.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "t",
            "g_wfb_status.prim_state",
            "mrac_state.roll.Theta[0]",
            "mrac_state.roll.u_ad",
            "mrac_state.roll.u_nom",
            "mrac_state.roll.e",
        ])
        for ti, wi in zip(t, w0):
            writer.writerow([f"{ti:.3f}", "2", f"{wi:.5f}", f"{-0.5 * wi:.5f}", "0.4", "0.02"])

    results = evaluate_flight_folder(flight_dir)
    assert len(results) == 1
    r = results[0]
    assert r.layer == "rate_roll"
    assert r.verdict == VERDICT_OK
    assert len(r.knobs) == 1
    assert (flight_dir / "plots" / "adapt_gate_rate_roll.png").is_file()
    table = format_gate_table(results)
    assert "rate_roll" in table and "OK" in table
