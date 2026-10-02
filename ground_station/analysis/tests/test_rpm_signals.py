"""Tests for ground_station.analysis.rpm_signals on synthetic data.

These tests verify that hover_thrust_id correctly recovers known k_T,
mass, CW share and torque from synthetic RPM data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ground_station.analysis.rpm_signals import (
    hover_thrust_id,
    period_cyc_to_rpm,
    mask_outliers_ch3,
    SYSTEM_CORE_CLOCK,
)

# RPM for period_cyc = 60 * 168e6 / period


def test_period_cyc_to_rpm_basic():
    """Known period -> known RPM."""
    # At 168 MHz: RPM = 60 * 168e6 / period
    # For 6000 RPM: period = 60*168e6/6000 = 1_680_000
    period = int(60 * SYSTEM_CORE_CLOCK / 6000)
    rpm = period_cyc_to_rpm(np.array([float(period)], dtype=np.float64))
    assert np.isclose(rpm[0], 6000.0, rtol=1e-4)


def test_period_cyc_to_rpm_capped():
    """RPM is capped at 65535."""
    tiny_period = np.array([1.0], dtype=np.float64)  # would be 10 GHz RPM
    rpm = period_cyc_to_rpm(tiny_period)
    assert rpm[0] == 65535.0


def test_period_cyc_to_rpm_zero_period():
    """Zero period -> 0 RPM (not inf)."""
    zero = np.array([0.0], dtype=np.float64)
    rpm = period_cyc_to_rpm(zero)
    assert rpm[0] == 0.0


# ---- mask_outliers_ch3 ----


def test_mask_outliers_ch3_no_outliers():
    """All values similar -> no masking."""
    s = np.full(1000, 5000.0)
    good = mask_outliers_ch3(s, window=100, threshold_sigma=3.0)
    assert good.all()


def test_mask_outliers_ch3_detects_half():
    """One value at 0.5x median should be masked."""
    s = np.full(1000, 6000.0)
    s[500] = 3000.0  # outlier
    good = mask_outliers_ch3(s, window=200, threshold_sigma=3.0)
    assert not good[500]  # the outlier is masked


def test_mask_outliers_ch3_detects_double():
    """One value at 2x median should be masked."""
    s = np.full(1000, 6000.0)
    s[500] = 12000.0  # outlier
    good = mask_outliers_ch3(s, window=200, threshold_sigma=3.0)
    assert not good[500]


def test_mask_outliers_ch3_zeros_masked():
    """Zero values are masked (stalled frames)."""
    s = np.full(1000, 6000.0)
    s[100] = 0.0
    good = mask_outliers_ch3(s, window=200, threshold_sigma=3.0)
    assert not good[100]


# ---- helper to build synthetic hover DataFrame ----


def _make_hover_df(
    n: int = 500,
    rpm_ch0: float = 5343.0,
    rpm_ch1: float = 5951.0,
    rpm_ch2: float = 5381.0,
    rpm_ch3: float = 6106.0,
    ch3_outlier_pct: float = 0.0,
    acc_z_offset: float = 0.0,
) -> pd.DataFrame:
    """Create a synthetic hover DataFrame.

    Generates raw period_cyc values (as stored in the real CSV slot2)
    corresponding to the specified RPM values.
    """
    np.random.seed(42)

    def rpm_to_period(rpm_val: float) -> float:
        return (60.0 * SYSTEM_CORE_CLOCK) / rpm_val

    noise = 0.01  # 1% std on RPM

    data = {
        "DroneStatus.ARM_Status": np.ones(n),
        "flight_phase": np.ones(n),
        "ano_of.of_alt_cm": np.full(n, 100.0),
        "real_voltage": np.full(n, 15.5),
        "Lin_Acc_Z_body": np.full(n, 0.0 + acc_z_offset),
        "t_src_ms": np.arange(n) * 10,
    }

    def make_period_series(base_rpm: float) -> np.ndarray:
        period = rpm_to_period(base_rpm)
        return period + period * noise * np.random.randn(n)

    data["rpm_dbg_period_cyc[0]"] = make_period_series(rpm_ch0)
    data["rpm_dbg_period_cyc[1]"] = make_period_series(rpm_ch1)
    data["rpm_dbg_period_cyc[2]"] = make_period_series(rpm_ch2)

    # ch3: add outliers
    p3 = make_period_series(rpm_ch3)
    if ch3_outlier_pct > 0:
        n_out = int(n * ch3_outlier_pct)
        idx = np.random.choice(n, n_out, replace=False)
        for k in idx:
            if np.random.rand() < 0.5:
                p3[k] *= 2.0  # missed marks -> longer period -> lower RPM
            else:
                p3[k] *= 0.5  # extra edges -> shorter period -> higher RPM
    data["rpm_dbg_period_cyc[3]"] = p3

    return pd.DataFrame(data)


# ---- hover_thrust_id (synthetic) ----


def test_mass_hat_recovered_at_hover():
    """At hover (acc_z=0), mass_hat should equal assumed mass."""
    df = _make_hover_df(n=500, acc_z_offset=0.0)
    result = hover_thrust_id(df, mass_kg=1.0, arm_m=0.1414)

    assert "error" not in result
    assert abs(result["mass_hat_kg"] - 1.0) < 0.08


def test_cw_share_in_range():
    """CW share (ch0+ch1)/(total) should be between 0.4 and 0.6 for near-balanced hover."""
    w0, w1, w2, w3 = [
        r * 2.0 * np.pi / 60.0 for r in [5300, 5900, 5400, 6100]
    ]
    expected_share = (w0**2 + w1**2) / (w0**2 + w1**2 + w2**2 + w3**2)

    df = _make_hover_df(n=500, rpm_ch0=5300, rpm_ch1=5900, rpm_ch2=5400, rpm_ch3=6100)
    result = hover_thrust_id(df, mass_kg=1.0, arm_m=0.1414)

    assert "error" not in result
    assert abs(result["cw_share"] - expected_share) < 0.02


def test_ch3_outliers_masked():
    """With 20% ch3 outliers, the estimator should still produce reasonable results."""
    df = _make_hover_df(n=1000, ch3_outlier_pct=0.20)
    result = hover_thrust_id(df, mass_kg=1.0, arm_m=0.1414, ch3_sigma=3.0)

    assert "error" not in result
    # mass_hat should still be close to 1.0 despite ch3 outliers
    assert abs(result["mass_hat_kg"] - 1.0) < 0.10


def test_no_hover_frames():
    """Empty hover mask -> error."""
    df = pd.DataFrame({
        "DroneStatus.ARM_Status": [0.0],
        "flight_phase": [0.0],
        "ano_of.of_alt_cm": [0.0],
        "real_voltage": [12.0],
    })
    result = hover_thrust_id(df, mass_kg=1.0)
    assert "error" in result


def test_sum_w2_positive():
    """sum_w2 should be positive and in a reasonable range."""
    df = _make_hover_df(n=500)
    result = hover_thrust_id(df, mass_kg=1.0)

    assert result["sum_w2"] > 0
    # sum_w2 at ~5300-6100 RPM: ~1.4e6 rad^2/s^2
    assert result["sum_w2"] < 3e6


def test_kT_in_reasonable_range():
    """k_T should be in the expected range (~6.8e-6)."""
    df = _make_hover_df(n=500)
    result = hover_thrust_id(df, mass_kg=1.0)

    # k_T for 1 kg: m*g/sum_w2 = 9.81 / 1.4e6 = 7.0e-6
    assert result["k_T"] > 5e-6
    assert result["k_T"] < 1e-5


def test_torque_nonzero_when_asymmetric():
    """If CW and CCW RPM are very different, roll torque should be non-zero."""
    # Extreme imbalance: ch0=3000, ch1=3000, ch2=8000, ch3=8000
    df = _make_hover_df(n=500, rpm_ch0=3000, rpm_ch1=3000, rpm_ch2=8000, rpm_ch3=8000)
    result = hover_thrust_id(df, mass_kg=1.0, arm_m=0.1414)

    assert "error" not in result
    # The CW/CCW imbalance produces roll torque
    assert abs(result["torque_roll_Nm"]) > 0.001


def test_rpm_mean_reasonable():
    """RPM mean should be close to the specified RPMs (within 2%)."""
    df = _make_hover_df(n=500, rpm_ch0=5343, rpm_ch1=5951, rpm_ch2=5381, rpm_ch3=6106)
    result = hover_thrust_id(df, mass_kg=1.0)

    assert "error" not in result
    target = [5343, 5951, 5381]
    for i, t in enumerate(target):
        assert abs(result["rpm_mean"][i] - t) < t * 0.02

# ---- WP-16 tests ----
import tempfile
import json
from pathlib import Path
from ground_station.analysis.rpm_signals import load_slots, hover_mask, main

def test_load_slots_merge_by_time():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        
        # 20 Hz slot (fastest) - 50ms interval
        df1 = pd.DataFrame({
            "t_src_ms": [100, 150, 200, 250, 300],
            "val1": [1, 2, 3, 4, 5]
        })
        # 10 Hz slot (slower) - 100ms interval, offset by 10ms
        df2 = pd.DataFrame({
            "t_src_ms": [110, 210, 310],
            "val2": [10, 20, 30]
        })
        
        df1.to_csv(tmp / "test_merge.slot1.csv", index=False)
        df2.to_csv(tmp / "test_merge.slot2.csv", index=False)
        
        res = load_slots(tmp, "test_merge")
        
        # Should have 5 rows (from fastest slot1)
        assert len(res) == 5
        # row at 100 merged with 110 (nearest) -> 10
        # row at 200 merged with 210 -> 20
        # row at 300 merged with 310 -> 30
        assert res.loc[res["t_src_ms"] == 100, "val2"].iloc[0] == 10
        assert res.loc[res["t_src_ms"] == 200, "val2"].iloc[0] == 20
        assert res.loc[res["t_src_ms"] == 300, "val2"].iloc[0] == 30

def test_load_slots_empty_slot0():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        
        # Empty slot0 (header only)
        pd.DataFrame(columns=["t_src_ms", "ARM_Status"]).to_csv(tmp / "test_empty.slot0.csv", index=False)
        
        # Populated slot1
        df1 = pd.DataFrame({
            "t_src_ms": [100, 200],
            "val1": [1, 2]
        })
        df1.to_csv(tmp / "test_empty.slot1.csv", index=False)
        
        res = load_slots(tmp, "test_empty")
        assert len(res) == 2
        assert "val1" in res.columns
        # Empty slot shouldn't cause merge error or empty result

def test_flat_layout_discovery(capsys, monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        
        # Create dummy meta json files for flat layout
        (tmp / "f17_hover_abc.meta.json").write_text("{}")
        (tmp / "f17_hover_xyz.meta.json").write_text("{}")
        
        # Need at least one populated log so it doesn't just error out immediately
        df = pd.DataFrame({
            "t_src_ms": [100]*250,
            "rpm_dbg_period_cyc[0]": [1000]*250,
            "flight_phase": [1.0]*250,
            "ano_of.of_alt_cm": [50.0]*250,
            "real_voltage": [15.0]*250,
            "Lin_Acc_Z_body": [0.0]*250,
        })
        df.to_csv(tmp / "f17_hover_abc.slot1.csv", index=False)
        
        # Monkeypatch print_kt_table to capture its arguments
        captured_flights = []
        def mock_print_kt_table(flights, mass_kg):
            captured_flights.extend(flights)
        monkeypatch.setattr("ground_station.analysis.rpm_signals.print_kt_table", mock_print_kt_table)
        
        main(["--logs", str(tmp)])
        
        prefixes = [p[0] for p in captured_flights]
        assert "f17_hover_abc" in prefixes
        assert "f17_hover_xyz" in prefixes

def test_hover_mask_without_arm_status():
    df = pd.DataFrame({
        "s_state": [1.0, 0.0, 1.0],
        "ano_of.of_alt_cm": [50.0, 50.0, 50.0],
        "real_voltage": [15.0, 15.0, 15.0]
    })
    # no ARM_Status, no flight_phase
    mask = hover_mask(df)
    assert mask.iloc[0] == True
    assert mask.iloc[1] == False
    assert mask.iloc[2] == True
