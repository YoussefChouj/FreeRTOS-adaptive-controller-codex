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
