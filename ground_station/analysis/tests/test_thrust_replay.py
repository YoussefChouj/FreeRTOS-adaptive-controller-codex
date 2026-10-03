"""thrust_replay: imu_total forms, period -> omega^2, and the per-window hover balance on a synthetic log."""
from __future__ import annotations

import numpy as np
import pytest

from ground_station.analysis import thrust_replay as tr


def test_imu_total_forms_tilted_hover():
    c = np.cos(np.radians(10.0)) * np.cos(np.radians(20.0))
    T = tr.M_FW * tr.G / c                                     # body-z thrust holding altitude
    lin_mg = (tr.G / c - tr.G * c) * 1000.0 / 9.80665          # Lin_Acc_Z_body in mg
    old, new = tr.imu_total_forms(np.array([lin_mg]), np.array([10.0]), np.array([20.0]))
    assert new[0] == pytest.approx(T, rel=5e-4)
    assert old[0] == pytest.approx(T / c, rel=5e-4)            # the pre-WP-34 form reads T / cos(tilt)


def test_sum_w2_from_period():
    p = np.array([[168e6 / 100.0], [168e6 / 100.0], [0.0], [168e6 / 50.0]])   # 100, 100, stopped, 50 rev/s
    w = 2 * np.pi * np.array([100.0, 100.0, 0.0, 50.0])
    assert tr.sum_w2_from_period(p)[0] == pytest.approx(np.sum(w * w))


def _log(vbat0=16.0, sag=1.0, seconds=60.0, fs=50.0):
    t = np.arange(0.0, seconds, 1.0 / fs)
    v = vbat0 - sag * t / seconds
    pwm = 3000.0 + 90.0 * (vbat0 - v)                          # hover PWM rises as the pack sags
    s = {"flight_phase": ((t > 1) & (t < seconds - 1)).astype(float), "DroneStatus.ARM_Status": (t > 0.5).astype(float),
         "g_thrust_est.imu_total": np.full_like(t, tr.M_FW * tr.G), "real_voltage": v}
    for i in range(4):
        s[f"mymotor.motor{i + 1}"] = pwm
        s[f"g_thrust_est.empirical[{i}]"] = 3.3 + 1.5 * (pwm - 3000.0) / 200.0
        s[f"g_thrust_est.blade_element[{i}]"] = np.full_like(t, tr.M_FW * tr.G / 4)
        s[f"rpm_dbg_period_cyc[{i}]"] = np.full_like(t, tr.F_CPU / (np.sqrt(tr.M_FW * tr.G / 4 / tr.K_T) / (2 * np.pi)))
    return {k: (t, x) for k, x in s.items()}


def test_analyze_hover_balance_and_sag():
    r = tr.analyze(_log())
    assert r["n_win"] == 26                                     # airborne 1..59 s, 2 s cut at each end, 2 s windows
    assert r["m_blade"] == pytest.approx(tr.M_FW, rel=1e-3) and r["m_now"] == pytest.approx(tr.M_FW, rel=1e-3)
    assert r["dpwm_dv"] == pytest.approx(-90.0, rel=0.02)
    assert r["demp_dv"] == pytest.approx(-4 * 1.5 * 90.0 / 200.0, rel=0.02)   # the voltage-blind LUT drifts
    assert r["disarmed_s"] == pytest.approx(0.5, abs=0.03) and r["disarmed_emp_frac"] == 1.0
    assert tr.analyze({"x": (np.arange(3.0), np.arange(3.0))}) is None
    assert "| log |" in tr.to_markdown({"a": r})
