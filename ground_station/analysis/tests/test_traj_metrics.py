"""traj_metrics: firmware preset model, refusal mirror, curvature cap and tracking metrics (WP-D)."""
import math
from pathlib import Path

import numpy as np
import pytest

from ground_station.analysis import traj_metrics as tm

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def lims():
    return tm.load_limits()


def _p(**kw):
    p = dict(tm.TRAJ_DEFAULTS)
    p.update(kw)
    return p


def test_limit_rows_parsed_from_firmware(lims):
    assert [(l.tilt_deg, l.trajv_cms, l.yawr_dps) for l in lims] == [(15, 80, 90), (20, 110, 120), (30, 150, 160)]


def test_phase_profile_total_and_continuous_rate():
    p = _p(move_s=20.0, ramp_s=2.0)
    t = np.arange(0.0, 20.0 + 1e-9, 0.001)
    th = tm.phase(t, p, 2.0)
    assert th[-1] == pytest.approx(4.0 * math.pi, rel=1e-6)
    rate = np.diff(th) / 0.001
    assert rate[0] == pytest.approx(0.0, abs=1e-3) and rate[-1] == pytest.approx(0.0, abs=1e-3)
    assert np.max(np.abs(np.diff(rate))) < 1e-3          # no step in the rate: the ramps join smoothly


def test_default_gerono_peak_matches_firmware_comment(lims):
    kin, notes = tm.check(_p(), 4, lims[0])
    assert kin.v_peak == pytest.approx(69.0, abs=1.0)    # AutoflyTask.c traj_p comment: "peak 69 cm/s (limit 80)"
    assert not [n for n in notes if n[0] == "REFUSE"]


def test_three_laps_refused_at_slow_allowed_at_conservative(lims):
    p = _p(f8_laps=3.0)
    kin, slow = tm.check(p, 4, lims[0])
    assert kin.v_peak == pytest.approx(104.0, abs=1.0)   # StabilizerTask.c s_lim row 1 comment: "f8_laps 3 fits (104 cm/s)"
    assert any(lv == "REFUSE" and "0xEE too fast" in m for lv, m in slow)
    assert not [n for n in tm.check(p, 4, lims[1])[1] if n[0] == "REFUSE"]


def test_numeric_speed_agrees_with_firmware_estimate():
    p = _p()
    t = np.arange(0.0, p["move_s"], 0.002)
    vel = np.gradient(tm.shape_offset(p, 4, tm.phase(t, p, p["f8_laps"])), 0.002, axis=0)
    assert np.max(np.linalg.norm(vel, axis=1)) == pytest.approx(tm.kinematics(p, 4).v_peak, rel=0.01)


def test_circle_fence(lims):
    assert any("0xEF" in m for _, m in tm.check(_p(circ_r_cm=70.0), 3, lims[2])[1])   # x0 = -1.4 m < -1.3
    assert not any("0xEF" in m for _, m in tm.check(_p(circ_r_cm=60.0), 3, lims[2])[1])


def test_timing_and_tilt_refusals(lims):
    assert tm.kinematics(_p(move_s=22.0, ret_s=3.0, hold_s=2.0), 3).refuse.startswith("0xEE timing")
    assert "z > 40" in tm.kinematics(_p(f8_a_cm=70.0, f8_tilt_deg=45.0), 4).refuse


def test_curvature_speed_cap(lims):
    cap = tm.accel_cap(lims[0])
    assert cap == pytest.approx(0.6 * 981.0 * math.tan(math.radians(15.0)))
    assert tm.curvature_speed_cap(1.0 / 40.0, lims[0]) == pytest.approx(math.sqrt(cap * 40.0))
    assert tm.curvature_speed_cap(0.0, lims[0]) == math.inf


def test_max_laps_is_the_speed_limit_at_slow(lims):
    n = tm.max_laps(_p(), 4, lims[0])
    assert n == pytest.approx(2.0 * 80.0 / tm.kinematics(_p(), 4).v_peak, rel=1e-6)


def test_along_cross_rmse_on_a_line():
    t = np.linspace(0.0, 10.0, 501)
    ref = np.stack([t, np.zeros_like(t)], axis=1)
    m = tm.along_cross_rmse(ref, ref + np.array([1.0, 2.0]))
    assert m["along_rmse"] == pytest.approx(1.0) and m["cross_rmse"] == pytest.approx(2.0)
    assert m["rmse"] == pytest.approx(math.sqrt(5.0))


def test_along_cross_rmse_on_a_circle_radial_offset_is_cross():
    th = np.linspace(0.0, 2.0 * math.pi, 2001)
    ref = np.stack([np.cos(th), np.sin(th)], axis=1)
    m = tm.along_cross_rmse(ref, 1.1 * ref)
    assert m["cross_rmse"] == pytest.approx(0.1, rel=1e-3) and m["along_rmse"] < 1e-3


def test_discrete_frechet():
    P = np.stack([np.linspace(0, 1, 50), np.zeros(50)], axis=1)
    assert tm.discrete_frechet(P, P) == 0.0
    assert tm.discrete_frechet(P, P + [0.0, 0.3]) == pytest.approx(0.3)
    # Eiter-Mannila: a lagging but same-path curve stays close; a reversed one is the full length apart
    assert tm.discrete_frechet(P, P[::2]) < 0.05
    assert tm.discrete_frechet(P, P[::-1]) == pytest.approx(1.0)


def test_check_fly_smoke():
    rows = tm.check_fly(ROOT / "ground_station" / "service" / "campaigns" / "lab_c_pid.fly")
    assert rows and {r["preset"] for r in rows} <= set(tm.PRESET_IDS)
    assert not [n for r in rows for n in r["notes"] if n[0] == "REFUSE"]


def test_check_fly_preset_fields_do_not_carry_over():
    rows = tm.check_fly(ROOT / "ground_station" / "service" / "campaigns" / "lab_f_dense_traj.fly")
    flat, tilted = (r["kin"] for r in rows if r["preset"] == "figure8")
    assert flat.box[5] - flat.box[4] < 1.0        # tilt 0: the ground writes its boot values, not lab_stage 1
    assert tilted.box[5] - tilted.box[4] > 20.0   # f8_tilt_deg=30 on that preset only
    assert not [n for r in rows for n in r["notes"] if n[0] == "REFUSE"]


def test_quintic_min_jerk_boundary_conditions():
    """Minimum-jerk quintic segment meets pos/vel/acc boundary conditions within 1e-4."""
    p0, v0, a0 = np.array([-20.0, 15.0, 0.0]), np.array([10.0, -5.0, 0.0]), np.array([-15.0, 8.0, 0.0])
    p1, v1, a1 = np.array([35.0, -25.0, 10.0]), np.array([-4.0, 12.0, 0.0]), np.array([6.0, -10.0, 0.0])
    pos0, vel0, acc0 = tm.quintic_segment(0.0, 2.5, p0, v0, a0, p1, v1, a1)
    pos1, vel1, acc1 = tm.quintic_segment(2.5, 2.5, p0, v0, a0, p1, v1, a1)
    assert np.max(np.abs(pos0 - p0)) < 1e-4
    assert np.max(np.abs(vel0 - v0)) < 1e-4
    assert np.max(np.abs(acc0 - a0)) < 1e-4
    assert np.max(np.abs(pos1 - p1)) < 1e-4
    assert np.max(np.abs(vel1 - v1)) < 1e-4
    assert np.max(np.abs(acc1 - a1)) < 1e-4


def test_slow_clock_and_waypoint_arrival_parser():
    """Slow clock scales s in [0,1] with rate-limited recovery; arrival parser fires once per waypoint."""
    s = 1.0
    for _ in range(10):
        s, ds = tm.slow_clock_step(s, 50.0, e_lo_cm=15.0, e_hi_cm=45.0, recover_rate=1.0, dt=0.01)
    assert s == 0.0
    for _ in range(40):
        s_prev = s
        s, ds = tm.slow_clock_step(s, 5.0, e_lo_cm=15.0, e_hi_cm=45.0, recover_rate=1.0, dt=0.01)
        assert 0.0 <= s - s_prev <= 0.01 + 1e-9
    assert s == pytest.approx(0.4, abs=1e-6)

    t = np.arange(0.0, 9.0, 0.01)
    wps = [(40.0, 0.0, 2.0), (40.0, 40.0, 4.0), (0.0, 40.0, 6.0), (0.0, 0.0, 8.0)]
    t_lag = np.maximum(t - 0.35, 0.0)
    px = np.select([t_lag < 2, t_lag < 4, t_lag < 6, t_lag < 8],
                   [20 * t_lag, 40.0, 40 - 20 * (t_lag - 4), 0.0], default=0.0)
    py = np.select([t_lag < 2, t_lag < 4, t_lag < 6, t_lag < 8],
                   [0.0, 20 * (t_lag - 2), 40.0, 40 - 20 * (t_lag - 6)], default=0.0)
    pos = np.column_stack([px, py])
    ref = np.column_stack([px + 4.0, py])
    arrivals = tm.parse_waypoint_arrivals(t, pos, ref, wps, r_cm=10.0, s_trace=np.full_like(t, 0.9))
    assert len(arrivals) == 4 and all(a.arrived for a in arrivals)
    assert all(a.peak_err_cm == pytest.approx(4.0, abs=1e-3) for a in arrivals)
    table = tm.format_arrival_table(arrivals)
    assert "t_plan_s" in table and "time_lost_s" in table


def test_fly_options_slow_clock_arrival_radius_and_min_jerk(tmp_path):
    """New .fly options (slow_clock on/e_lo/e_hi, arrive_r_cm, min_jerk / profile=2) compile and pass dry-run check."""
    from ground_station.service.fly_file import compile_fly
    fly_txt = "\n".join([
        "# n26-B slow clock + min-jerk figure-8 test",
        "campaign n26-b-test",
        "set g_xy_ctrl_kind=2 g_xyc_pred_lead=1",
        "slow_clock on=1 e_lo=15 e_hi=45 arrive_r_cm=15",
        "takeoff 0.85",
        "hold 2",
        "preset figure8 f8_a_cm=50 f8_laps=2 move_s=18 min_jerk=1 slow_on=1 e_lo=15 e_hi=45 arrive_r_cm=15",
        "land",
    ])
    f = tmp_path / "n26_b_test.fly"
    f.write_text(fly_txt, encoding="utf-8")
    camp, _ = compile_fly(fly_txt, str(f))
    exp = camp["experiments"][0]
    preset_step = [s for s in exp["scenario"]["steps"] if isinstance(s, dict) and "preset" in s][0]["preset"]
    assert preset_step["min_jerk"] == 1
    rows = tm.check_fly(f)
    assert len(rows) == 1
    assert rows[0]["preset"] == "figure8"
    assert not [n for r in rows for n in r["notes"] if n[0] == "REFUSE"]
