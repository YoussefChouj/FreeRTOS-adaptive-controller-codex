"""mrac_log_replay: 0x1D knob mapping, mixer deficit, metric helpers, and the gcc-built firmware law on a synthetic log."""
from __future__ import annotations

import shutil

import numpy as np
import pytest

from ground_station.analysis import mrac_log_replay as mr


def test_knob_args_from_v1_preset():
    args = mr.knob_args(mr.preset("mrac_v1", "pid_ref"))
    assert {"0:0:2.0", "1:0:2.0", "2:0:1.0", "0:2:1.0", "2:12:2.0", "0:3:0.0018", "0:11:1.0", "2:11:1.0"} <= set(args)
    assert set(mr.variants()) >= {"OFF", "V1 g1", "V2", "PR*", "3L*", "V3"}


def test_mixer_deficit_signs_and_zero_inside():
    z = np.zeros(3)
    inside = mr.mixer_deficit(np.full(3, 3000.0), z + 50, z, z)
    assert np.all(inside == 0)
    # +roll U at the throttle ceiling: motors 2 and 3 cut by 40 each -> roll and z deficits, no pitch / yaw
    d = mr.mixer_deficit(np.array([3990.0]), np.array([50.0]), np.zeros(1), np.zeros(1))[0]
    assert d[1] == pytest.approx(0.25 * 80 / mr.TO_MIXER["roll"]) and d[3] == pytest.approx(0.25 * 80 / mr.TO_MIXER["z"])
    assert d[0] == pytest.approx(0.0) and d[2] == pytest.approx(0.0)


def test_run_helpers():
    m = np.array([0, 1, 1, 1, 0, 1, 1, 0], bool)
    assert mr.longest_run(m, 0.5) == 1.5
    assert mr.runs_at_least(m, 1.0, 0.5) == 2 and mr.runs_at_least(m, 1.5, 0.5) == 1
    assert mr.growth_s(np.r_[np.linspace(0, 1, 400), np.ones(400)], 0.005) == pytest.approx(2.0, abs=0.3)


def _hover(seconds=40.0, fs=50.0, throttle=3000.0):
    t = np.arange(0.0, seconds, 1.0 / fs)
    rng = np.random.default_rng(0)
    phase = ((t > 1.0) & (t < seconds - 1.0)).astype(float)
    s = {"flight_phase": phase, "DroneStatus.ARM_Status": np.ones_like(t), "Throttle_out": np.full_like(t, throttle),
         "imu_data.pit": 5.0 * np.sin(0.7 * t), "imu_data.rol": -1.5 * np.sin(0.5 * t)}     # degrees, as logged
    for pid, amp in (("gyroxPID", 20.0), ("gyroyPID", 15.0), ("gyrozPID", 5.0)):
        des = amp * np.sin(1.3 * t)
        s[f"Ctrler.{pid}.Des"] = des
        s[f"Ctrler.{pid}.FB"] = des + 3.0 * rng.standard_normal(len(t)) + 2.0
        s[f"Ctrler.{pid}.U"] = 4.0 * amp * np.sin(1.3 * t + 0.4)
    s.update({"Ctrler.Z_ratePID.Des": np.zeros_like(t), "Ctrler.Z_ratePID.FB": 0.02 * rng.standard_normal(len(t)),
              "Ctrler.Z_ratePID.U": 20.0 * rng.standard_normal(len(t))})
    return {k: (t, v) for k, v in s.items()}


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not on PATH")
def test_replay_synthetic_hover(tmp_path):
    allv = mr.variants()
    which = {k: allv[k] for k in ("OFF", "V2", "V3")}
    res = mr.replay_log(_hover(throttle=3960.0), tmp_path, which)     # throttle near the ceiling: the mixer clips
    assert res["airborne_s"] == pytest.approx(38.0, abs=0.1) and res["has_throttle"]
    for name, row in res["variants"].items():
        for a in ("pitch", "roll", "yaw"):
            m = row[a]
            assert m["n"] > 0 and m["finite"]
            assert m["rms_u_ad"] <= 6.74 and np.isfinite(m["th_max"])
        assert row["roll"]["udef_frac"] > 0.1                          # V2's u_def drive is present
        assert row["would_trips"] == 0      # 5 deg tilt fed in degrees: inside the 3.14 rad envelope (WP-38 A)
    # fed degrees as rad (before WP-38) the grid sat off-centre, mean sum phi 0.38-0.66; in rad it is near 3.07
    assert res["variants"]["V3"]["rbf"]["pitch"]["mean_sum"] > 2.0
