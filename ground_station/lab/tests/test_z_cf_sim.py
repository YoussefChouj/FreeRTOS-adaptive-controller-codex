"""Focused offline simulation invariants, not flight acceptance tests."""
import numpy as np
from ground_station.lab.z_cf_sim import Estimator, PID, F, kalman_gain, run, margins, Assumptions


def test_pid_tick_scaling_separation_and_clamps():
    pid = PID(.7, .005, .1, 1, .9, .3, .3, 30, .3)
    assert np.isclose(pid.step(1, .8), .161)
    assert np.isclose(pid.step(1, .8), .142)
    previous = pid.total
    pid.step(1, 0)
    assert pid.total == previous  # EMin gate is strict
    for _ in range(1000):
        pid.step(1, .8)
    assert pid.total == 30
    assert np.isclose(pid.output, .29)


def test_legacy_stale_vs_a2_arrival_and_delayed_history_propagation():
    old, fixed = Estimator('current_cf'), Estimator('cf_a2')
    old.step(0, .82)
    fixed.step(0, .82)
    assert old.x[0] == .8
    assert fixed.x[0] > .8
    delayed = Estimator('cf_a2_a3', 8)
    delayed.apply(np.array([.01, .02, -.03]))
    assert np.allclose(delayed.x, np.array([.8, 0, 0]) + np.linalg.matrix_power(F, 8) @ [.01, .02, -.03])
    assert np.allclose(delayed.history[8], [.81, .02, -.03])


def test_kalman_observer_stable_and_run_deterministic():
    gain = kalman_gain()
    observer = (np.eye(3) - np.outer(gain, [1, 0, 0])) @ np.linalg.matrix_power(F, 10)
    assert max(abs(np.linalg.eigvals(observer))) < 1
    first = run('cf_a2_a3', seconds=16)
    assert first == run('cf_a2_a3', seconds=16)
    assert all(np.isfinite(v) for v in first.values() if v is not None)


def test_margin_depends_on_motor_and_controller_not_estimator_phase_alone():
    f = np.geomspace(.025, 12, 300)
    hh, hv = np.ones(len(f)), 2j * np.pi * f
    fast = margins(hh, hv, f, 20, .001)
    slow = margins(hh, hv, f, 60, .001)
    assert fast['phase_margin_deg'] > slow['phase_margin_deg']


def test_a4_preserves_residual_and_delivers_finite_correction():
    est = Estimator('cf_a2_a3_a4')
    est.step(0, .82)
    assert est.packets[0]["remaining"] == 9
    assert np.linalg.norm(est.packets[0]["delta"]) > 0
    for _ in range(9):
        est.step(0)
    assert not est.packets
    assert est.x[0] > .8


def test_a4_overlapping_packet_preserves_old_packet():
    est = Estimator('cf_a2_a3_a4', 8)
    est.step(0, .82)
    est.step(0)
    est.step(0, .83)
    assert len(est.packets) == 2
    assert est.packets[0]['remaining'] == 7
    assert est.packets[1]['remaining'] == 1
    for _ in range(7):
        est.step(0)
    assert not est.packets


def test_flat_hover_has_no_claimed_oscillation_period():
    ideal = Assumptions(accel_noise=0, range_noise=0, accel_bias=0, thrust_bias=0, quantize_cm=False)
    metrics = run('current_cf', seconds=16, assumptions=ideal)
    assert metrics['hover_rms_cm'] == 0
    assert metrics['hover_half_pp_cm'] == 0
    assert metrics['period_s'] is None
