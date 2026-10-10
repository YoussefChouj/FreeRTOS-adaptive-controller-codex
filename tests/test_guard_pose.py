"""Synthetic checks for ground_station.analysis.guard_pose (no video files needed)."""

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from ground_station.analysis import guard_pose as gp

K = np.array([[900.0, 0, 640], [0, 900.0, 360], [0, 0, 1]])
DIST = np.array([0.05, -0.02, 0, 0, 0])
B = 0.25                                            # guard spacing, m


def _cam():
    """Camera at (-1, -2, 2) looking at the floor origin from the side (world -> camera), as in test_video_truth."""
    rvec = np.array([2.2, 0.3, -0.2])
    R = cv2.Rodrigues(rvec)[0]
    return rvec, R, -R @ np.array([-1.0, -2.0, 2.0])


def _guard_px(p, rpy, odd_deg=45.0, noise=0.0, seed=0):
    """Pixels of the 4 guards (u, v, area) for a drone at world p with roll/pitch/yaw, odd guard last, others shuffled."""
    rvec, R, t = _cam()
    body = gp.body_points(B, odd_deg)
    w = np.asarray(p) + body @ gp.rot_rpy(*rpy).T
    px = cv2.projectPoints(w, rvec, t, K, DIST)[0].reshape(-1, 2)
    px += np.random.default_rng(seed).normal(0, noise, px.shape)
    order = [2, 1, 3, 0]                                                    # odd (index 0) last
    return np.c_[px[order], np.full(4, 200.0)], R, t


@pytest.mark.parametrize("yaw_deg", range(-180, 180, 30))
def test_pnp_round_trip_every_heading(yaw_deg):
    rpy = np.radians([6.0, -9.0, yaw_deg])
    px4, R, t = _guard_px([0.4, 0.3, 0.9], rpy)
    out = gp.guard_pose(px4, 3, gp.body_points(B, 45.0), K, DIST, R, t)
    np.testing.assert_allclose(out["p"], [0.4, 0.3, 0.9], atol=0.005)
    d = np.degrees(np.angle(np.exp(1j * (out["rpy"] - rpy))))
    assert np.max(np.abs(d)) < 0.5, d
    assert out["reproj_px"] < 0.05


def test_pnp_with_pixel_noise_and_prev():
    rpy = np.radians([3.0, 4.0, 120.0])
    body = gp.body_points(B, -135.0)
    prev, errs = None, []
    for s in range(30):
        px4, R, t = _guard_px([0.0, 0.5, 1.0], rpy, odd_deg=-135.0, noise=0.3, seed=s)
        out = gp.guard_pose(px4, 3, body, K, DIST, R, t, prev)
        prev = out["R"]
        errs.append(np.r_[out["p"] - [0.0, 0.5, 1.0], np.degrees(out["rpy"][2] - rpy[2])])
    e = np.abs(np.array(errs))
    assert np.median(e[:, :3]) < 0.02 and np.median(e[:, 3]) < 2.0, np.median(e, axis=0)


def test_wrong_odd_guard_turns_yaw_by_90():
    rpy = np.radians([0.0, 0.0, 30.0])
    px4, R, t = _guard_px([0.2, 0.2, 0.8], rpy)
    out = gp.guard_pose(px4, 3, gp.body_points(B, 135.0), K, DIST, R, t)    # told the wrong arm
    assert abs(abs(np.degrees(np.angle(np.exp(1j * (out["rpy"][2] - rpy[2]))))) - 90) < 1


def test_orderings_start_at_odd_and_are_cyclic():
    px4 = np.array([[0, 0, 1], [10, 0, 1], [10, 10, 1], [0, 10, 1]], float)
    a, b = gp.orderings(px4, 2)
    assert a[0] == b[0] == 2 and sorted(a) == sorted(b) == [0, 1, 2, 3]
    assert a[1:] == b[1:][::-1] and {a[1], a[3]} == {1, 3}


def test_depth_sigma():
    assert gp.depth_sigma(3.0, 1500.0, 0.25, 0.5) == pytest.approx(9 * 0.5 / (1500 * 0.25))


def _frame(r_white=9):
    img = np.full((360, 640, 3), (60, 60, 60), np.uint8)
    for c in ((200, 150), (260, 150), (260, 210)):
        cv2.circle(img, c, 9, (0, 128, 255), -1)                                # BGR orange, H ~ 15
    cv2.circle(img, (200, 210), r_white, (250, 250, 250), -1)                   # the white guard
    cv2.circle(img, (600, 30), 3, (255, 255, 255), -1)                          # a lamp speck, too small
    cv2.rectangle(img, (500, 300), (620, 350), (255, 255, 255), -1)             # paper, too big and far
    return img


def test_detect_frame_white_last():
    g = gp.detect_frame(_frame())
    assert g is not None and g.shape == (4, 3)
    np.testing.assert_allclose(g[3, :2], (200, 210), atol=0.5)
    assert {tuple(np.round(r)) for r in g[:3, :2]} == {(200, 150), (260, 150), (260, 210)}


def test_detect_frame_rejects_merged_and_missing_white():
    img = _frame()
    cv2.circle(img, (260, 150), 15, (0, 128, 255), -1)                         # two guards merged into one blob
    assert gp.detect_frame(img) is None
    assert gp.detect_frame(_frame(r_white=0)) is None                           # white guard hidden


def test_rts_velocity_on_noisy_sine():
    rng = np.random.default_rng(1)
    t = np.cumsum(rng.uniform(1 / 70, 1 / 50, 600))                            # irregular ~60 fps
    w = 2 * np.pi * 0.4
    z = 0.5 * np.sin(w * t) + rng.normal(0, 0.01, len(t))
    z[100:115] = np.nan                                                        # a dropout
    p, v, a, sp, sv, sa = gp.rts_ca(t, z, 0.01, 200.0)
    m = slice(20, -20)
    assert np.sqrt(np.mean((p[m] - 0.5 * np.sin(w * t[m])) ** 2)) < 0.006
    assert np.sqrt(np.mean((v[m] - 0.5 * w * np.cos(w * t[m])) ** 2)) < 0.05
    assert np.all(np.isfinite(p)) and sp[107] > sp[300]                        # wider during the gap


def test_umeyama_and_ate():
    rng = np.random.default_rng(2)
    a = rng.normal(0, 1, (200, 3))
    R = gp.rot_rpy(0.1, -0.2, 1.0)
    b = 1.3 * a @ R.T + [0.5, -1.0, 2.0]
    R2, t2, s2 = gp.umeyama(a, b, scale=True)
    np.testing.assert_allclose(R2, R, atol=1e-9)
    np.testing.assert_allclose(t2, [0.5, -1.0, 2.0], atol=1e-9)
    assert s2 == pytest.approx(1.3)
    tt = np.arange(200) * 0.05
    rep = gp.ate_rpe(tt, b, a + rng.normal(0, 0.01, a.shape), scale=True)
    assert rep["ate_rms_m"] < 0.03 and rep["scale"] == pytest.approx(1.3, abs=0.01)


def test_write_tum(tmp_path):
    t = np.array([0.0, 0.1, 0.2])
    p = np.array([[0, 0, 1], [np.nan, 0, 1], [1, 2, 3]], float)
    gp.write_tum(tmp_path / "x.tum", t, p, np.array([[0, 0, np.pi / 2]] * 3))
    rows = [r.split() for r in (tmp_path / "x.tum").read_text().splitlines()]
    assert len(rows) == 2 and all(len(r) == 8 for r in rows)
    np.testing.assert_allclose([float(v) for v in rows[1]], [0.2, 1, 2, 3, 0, 0, np.sqrt(0.5), np.sqrt(0.5)], atol=1e-5)


def test_audio_onset_holds_through_motors_not_clap():
    sr = 16000
    rng = np.random.default_rng(3)
    tt = np.arange(int(9 * sr)) / sr
    x = rng.normal(0, 1e-3, len(tt))
    clap = (tt >= 2.0) & (tt < 2.05)
    x[clap] += rng.normal(0, 0.5, clap.sum())
    on = tt >= 5.0
    x[on] += 0.1 * (np.sin(2 * np.pi * 310 * tt[on]) + np.sin(2 * np.pi * 620 * tt[on]) + np.sin(2 * np.pi * 1240 * tt[on]))
    t, lv = gp.band_level_db(x, sr)
    assert gp.onset(t, lv) == pytest.approx(5.0, abs=0.03)


def test_motor_onset_ignores_blip():
    t = np.arange(0, 10, 0.02)
    m = np.zeros((len(t), 4))
    m[50] = 400                                                                # one-sample blip at 1 s
    m[t >= 3.0] = 1100
    assert gp.motor_onset(t, m) == pytest.approx(3.0, abs=0.021)


def test_yaw_compare_finds_sign_and_offset():
    t = np.linspace(0, 20, 400)
    truth = np.angle(np.exp(1j * np.radians(90 * np.sin(0.3 * t))))
    est_deg = -(np.degrees(truth) - 30.0)                                      # flipped sign, 30 deg off
    r = gp.yaw_compare(t, truth, t, est_deg)
    assert r["sign"] == -1.0 and abs(r["offset_deg"] - 30.0) < 0.5 and r["rms_deg"] < 0.5
