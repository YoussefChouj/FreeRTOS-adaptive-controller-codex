"""design.py: firmware conversion, margins, bounded steps and refusals."""

import math

import numpy as np
import pytest

from ground_station.autotune.design import (
    PidRow, Plant, Spec, clamp_step, design_angle, design_rate, margins, pid_frf, rate_loop, read_pid_rows,
    to_continuous, to_firmware,
)

PLANT = Plant(8.5, 0.03, 0.01)


def test_firmware_conversion_ki_times_dt_kd_over_dt():
    assert to_firmware(5.0, 2.0, 0.05) == pytest.approx((5.0, 0.01, 10.0))
    assert to_continuous(5.0, 0.01, 10.0) == pytest.approx((5.0, 2.0, 0.05))
    row = PidRow(5.0, 0.01, 10.0)
    assert row.continuous() == pytest.approx({"Kp": 5.0, "Ki": 2.0, "Kd": 0.05})
    # discrete C(z) tends to the continuous PID at low frequency
    w = np.array([1.0])
    assert pid_frf(row, w)[0] == pytest.approx(5.0 + 2.0 / (1j * w[0]) + 0.05j * w[0], rel=0.01)


def test_pid_rows_are_read_from_api_pid_c():
    rows = read_pid_rows()
    assert (rows["gyroxPID"].kp, rows["gyroxPID"].ki, rows["gyroxPID"].kd) == (5.0, 0.01, 10.0)
    assert rows["gyroxPID"].udmax == 100.0 and rows["rollPID"].kp == 3.0


def test_margins_of_a_pure_integrator_loop():
    w = np.geomspace(0.3, 500, 800)
    m = margins(np.full_like(w, 10.0) / w, np.full_like(w, -90.0), w)
    assert m.pm_deg == pytest.approx(90.0) and m.wc_rps == pytest.approx(10.0, rel=1e-3) and math.isinf(m.gm_db)


def test_delay_lowers_phase_margin():
    row = PidRow(5.0, 0.01, 10.0)
    fast = margins(*rate_loop(Plant(8.5, 0.03, 0.0), row))
    slow = margins(*rate_loop(Plant(8.5, 0.03, 0.02), row))
    assert slow.pm_deg < fast.pm_deg - 10.0 and slow.gm_db < fast.gm_db


def test_clamp_step_holds_30_percent():
    assert clamp_step(30.0, 10.0, 0.3) == 13.0 and clamp_step(1.0, 10.0, 0.3) == 7.0
    assert clamp_step(11.0, 10.0, 0.3) == 11.0 and clamp_step(5.0, 0.0, 0.3) == 0.0


def test_rate_design_stays_in_the_step_box_and_meets_the_spec():
    cur = read_pid_rows()["gyroxPID"]
    d = design_rate(PLANT, cur)
    assert d.status == "proposed", d.reason
    for new, old in ((d.proposed.kp, cur.kp), (d.proposed.kd, cur.kd)):
        assert 0.7 * old - 1e-9 <= new <= 1.3 * old + 1e-9
    assert d.proposed.ki == cur.ki
    assert d.margins["proposed"].meets(45.0, 6.0, 2.0)
    assert d.margins["proposed"].pm_deg > d.margins["current"].pm_deg  # current PM 44 misses: margin first
    assert all(v > 0 for v in d.itae.values())


def test_rate_design_refuses_an_unstable_or_hopeless_plant():
    cur = read_pid_rows()["gyroxPID"]
    neg = design_rate(Plant(-8.5, 0.03, 0.01), cur)
    assert neg.status == "refused" and "integrator" in neg.reason
    late = design_rate(Plant(8.5, 0.2, 0.08), cur, Spec(pm_min_deg=80.0))
    assert late.status == "refused" and isinstance(late.reason, str) and late.reason


def test_angle_design_moves_toward_a_quarter_of_rate_crossover():
    rows = read_pid_rows()
    assert design_angle(PLANT, rows["gyroxPID"], rows["rollPID"]).status == "refused"  # rate PM 44 < 45 first
    rate = design_rate(PLANT, rows["gyroxPID"]).proposed
    d = design_angle(PLANT, rate, rows["rollPID"])
    assert d.status == "proposed", d.reason
    assert d.proposed.kp == pytest.approx(rows["rollPID"].kp * 1.3)  # crossover 3 rad/s -> wants ~8: clamped
    assert (d.proposed.ki, d.proposed.kd) == (rows["rollPID"].ki, rows["rollPID"].kd)
    assert d.margins["proposed"].pm_deg >= 50.0
