"""excitation.py mirrors API/sysid.c: wire map, clamps, timing and the rebuilt dither."""

import numpy as np

from ground_station.autotune import excitation as ex


def test_start_commands_follow_the_cmd_0x14_index_map():
    cmds = ex.start_commands("roll", "multisine", 0.5, 15.0, 60.0, 30.0)
    assert cmds[:6] == [(0, 1.0), (1, 1.0), (2, 0.5), (3, 15.0), (4, 60.0), (5, 30.0)]
    assert cmds[-2:] == [(7, 1.0), (6, 1.0)]  # geofence on, then start last
    assert ex.start_commands("pitch", "chirp", 1, 2, 3, 4)[:2] == [(0, 0.0), (1, 0.0)]


def test_sanitize_and_step_timing_match_the_firmware():
    assert ex.sanitize("roll", 0.01, 0.05, -120.0, 90.0) == (0.1, 0.1, 90.0, 60.0)
    assert ex.sanitize("yaw", 1, 5, 100, 0.2) == (1, 5, 60.0, 1.0)
    assert ex.active_s(30) == 33.0 and ex.step_s(30) == 35.0


def test_multisine_dither_is_peak_limited_enveloped_and_zero_outside():
    t = np.arange(-1.0, 36.0, ex.DT_S)
    d = ex.dither(t, "multisine", 0.5, 15.0, 60.0, 30.0)
    assert np.all(d[t < 0] == 0) and np.all(d[t >= 33.0] == 0)
    assert 50.0 < np.max(np.abs(d)) < 62.0
    assert np.max(np.abs(d[(t > 0) & (t < 0.3)])) < 5.0  # cosine ramp in


def test_chirp_dither_has_unit_amplitude_shape():
    t = np.arange(0, 13.0, ex.DT_S)
    d = ex.dither(t, "chirp", 1.0, 5.0, 10.0, 10.0)
    assert np.max(np.abs(d)) <= 10.0 + 1e-9 and np.max(np.abs(d[(t > 2) & (t < 11)])) > 9.9


def test_id_amplitude_keeps_the_open_loop_angle_swing_under_5_deg():
    e = ex.ID_EXCITE
    assert ex.angle_swing_deg(e["signal"], e["f0"], e["f1"], e["amp"], e["duration_s"]) < 5.0
