"""Synthetic checks for ground_station.analysis.video_truth (no video files needed)."""

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from ground_station.analysis import video_truth as vt

K = np.array([[900.0, 0, 640], [0, 900.0, 360], [0, 0, 1]])
DIST = np.array([0.05, -0.02, 0, 0, 0])


def _pose():
    """Camera 2.5 m from the floor origin, looking down at it from the side (world -> camera)."""
    rvec = np.array([2.2, 0.3, -0.2])
    R = cv2.Rodrigues(rvec)[0]
    c = np.array([-1.0, -2.0, 2.0])                # camera centre in the world frame
    return rvec, R, -R @ c


def _project(pts, rvec, t):
    p, _ = cv2.projectPoints(np.asarray(pts, float), rvec, t, K, DIST)
    return p.reshape(-1, 2)


def test_floor_pose_and_ray_plane_round_trip():
    rvec, R, t = _pose()
    marks = [[0, 0], [1.5, 0], [0, 1.5], [1.5, 1.5], [0.7, -0.4]]
    pix = _project(np.c_[marks, np.zeros(len(marks))], rvec, t)
    R2, t2, err = vt.floor_pose(K, DIST, marks, pix)
    assert err < 0.05
    np.testing.assert_allclose(R2, R, atol=1e-5)
    pts = np.array([[0.3, 0.2, 0.7], [1.1, -0.5, 0.7], [-0.4, 1.2, 0.7]])
    back = vt.pixel_to_plane(_project(pts, rvec, t), K, DIST, R2, t2, 0.7)
    np.testing.assert_allclose(back, pts, atol=1e-3)


def test_detect_guards_and_centre():
    img = np.full((360, 640, 3), (90, 90, 90), np.uint8)
    orange = (0, 128, 255)                          # BGR
    for u, v in [(200, 100), (300, 100), (200, 200), (300, 200)]:
        cv2.circle(img, (u, v), 14, orange, -1)
    cv2.circle(img, (500, 300), 2, orange, -1)      # speck below MIN_AREA
    g = vt.detect_guards(img)
    assert len(g) == 4
    np.testing.assert_allclose(vt.drone_centre(g[:, :2], g[:, 2]), [250, 150], atol=0.5)
    three = np.array([[200, 100], [300, 100], [300, 200.0]])
    np.testing.assert_allclose(vt.drone_centre(three, [50, 50, 50]), [250, 150])     # one hidden: diagonal
    merged = np.array([[200, 150], [300, 100], [300, 200.0]])                       # left pair merged
    np.testing.assert_allclose(vt.drone_centre(merged, [100, 50, 50]), [250, 150])
    np.testing.assert_allclose(vt.drone_centre(merged[[0, 0]] + [[0, 0], [100, 0]], [100, 100]), [250, 150])
    assert vt.drone_centre(three[:2]) is None and vt.drone_centre(three[:1], [50]) is None


def _path(t):
    return np.c_[np.sin(0.7 * t) + 0.3 * np.sin(2.3 * t), np.cos(0.5 * t) * np.sin(0.2 * t + 1)]


def test_sync_offset_recovers_shift():
    te = np.arange(0, 40, 0.005)                    # telemetry 200 Hz
    tv = np.arange(0, 35, 1 / 30)                   # video 30 fps, telemetry time = video time + 3.4
    off = vt.sync_offset(tv, _path(tv + 3.4), te, _path(te), max_lag=6.0)
    assert abs(off - 3.4) <= 0.04


def test_align_recovers_rotation_and_reflection():
    rng = np.random.default_rng(1)
    est = np.cumsum(rng.normal(size=(400, 2)), axis=0) * 0.01
    a = np.radians(37)
    rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    q, refl = vt.align(est, est @ rot.T, 200)
    np.testing.assert_allclose(q, rot, atol=1e-9)
    assert not refl
    flip = rot @ np.diag([1, -1])
    q, refl = vt.align(est, est @ flip.T, 200)
    np.testing.assert_allclose(q, flip, atol=1e-9)
    assert refl


def _lookat(c, target):
    z = (target - c) / np.linalg.norm(target - c)
    x = np.cross(z, [0, 0, 1.0]); x /= np.linalg.norm(x)
    R = np.vstack([x, np.cross(z, x), z])
    return R, -R @ c


def test_run_end_to_end(tmp_path):
    """Synthetic roam: video of 4 guards, telemetry 1.5 s later on its clock, estimator frame rotated -30 deg."""
    import csv
    k2, d0 = np.array([[450.0, 0, 320], [0, 450.0, 180], [0, 0, 1]]), np.zeros(5)
    R, t = _lookat(np.array([-2.5, 0.3, 2.2]), np.array([0.3, 0.3, 0.4]))
    rvec = cv2.Rodrigues(R)[0]
    off, h = 1.5, 0.7

    def truth(te):                                   # flies te 2..22, at the origin otherwise
        s = np.clip(te - 2.0, 0, 20.0)
        return np.c_[0.8 * np.sin(0.6 * s) + 0.2 * np.sin(1.9 * s), 0.7 * np.sin(0.45 * s) * np.cos(0.3 * s)]

    video = str(tmp_path / "roam.avi")
    w = cv2.VideoWriter(video, cv2.VideoWriter_fourcc(*"MJPG"), 30, (640, 360))
    arms = np.array([[0.12, 0.12], [0.12, -0.12], [-0.12, 0.12], [-0.12, -0.12]])
    for tv in np.arange(0, 23, 1 / 30):
        img = np.full((360, 640, 3), (80, 80, 80), np.uint8)
        g = truth(np.array([tv + off]))[0] + arms
        uv, _ = cv2.projectPoints(np.c_[g, np.full(4, h)], rvec, t, k2, d0)
        for u, v in uv.reshape(-1, 2):
            assert 0 < u < 640 and 0 < v < 360
            cv2.circle(img, (int(round(u)), int(round(v))), 8, (0, 128, 255), -1, cv2.LINE_AA)
        w.write(img)
    w.release()

    te = np.arange(0, 25, 0.005)
    a = np.radians(-30)
    est = truth(te) @ np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]]).T * 100
    path = tmp_path / "roam.csv"
    with open(path, "w", newline="") as f:
        cw = csv.writer(f)
        cw.writerow(["t", vt.X_CM, vt.Y_CM, vt.Z_M, vt.PHASE, vt.ARM])
        for i, ti in enumerate(te):
            cw.writerow([100 + ti, est[i, 0], est[i, 1], h, 1 if 2 <= ti <= 22 else 0, 1 if 1 <= ti <= 23 else 0])

    marks = [[0, 0], [1.5, 0], [0, 1.5], [1.5, 1.5]]
    pix = cv2.projectPoints(np.c_[marks, np.zeros(4)].astype(float), rvec, t, k2, d0)[0].reshape(-1, 2)
    cam = {"K": k2.tolist(), "dist": d0.tolist()}
    rep = vt.run(video, cam, {"world": marks, "pixel": pix.tolist()}, str(path), tmp_path / "out")
    assert abs(rep["sync_offset_s"] - off) <= 0.04
    assert abs(rep["align_rot_deg"] - 30) < 1 and not rep["align_reflection"]
    assert rep["err_rms_m"] < 0.02 and rep["err_max_m"] < 0.06
    assert abs(rep["scale_truth_over_est"] - 1) < 0.03
    assert (tmp_path / "out" / "truth.png").exists()


def test_floor_board_pose(tmp_path):
    """Board flat on the floor, seen obliquely: recovered camera height and a 1.5 m floor distance."""
    k2, d0 = np.array([[900.0, 0, 640], [0, 900.0, 360], [0, 0, 1]]), np.zeros(5)
    R, t = _lookat(np.array([-1.2, 0.2, 1.4]), np.array([0.1, 0.07, 0.0]))
    rvec, s = cv2.Rodrigues(R)[0], 0.026

    def poly(x0, y0, x1, y1):
        p = cv2.projectPoints(np.array([[x0, y0, 0], [x1, y0, 0], [x1, y1, 0], [x0, y1, 0]], float), rvec, t, k2, d0)[0]
        return np.round(p.reshape(-1, 2) * 16).astype(np.int32)

    img = np.full((720, 1280, 3), 110, np.uint8)
    cv2.fillConvexPoly(img, poly(-s, -s, 10 * s, 7 * s), (230, 230, 230), cv2.LINE_AA, 4)      # paper
    for r in range(6):
        for c in range(9):
            if (r + c) % 2:
                cv2.fillConvexPoly(img, poly(c * s, r * s, (c + 1) * s, (r + 1) * s), (30, 30, 30), cv2.LINE_AA, 4)
    video = str(tmp_path / "floor.avi")
    w = cv2.VideoWriter(video, cv2.VideoWriter_fourcc(*"MJPG"), 30, (1280, 720))
    for _ in range(45):
        w.write(img)
    w.release()

    m = vt.floor_board(video, (8, 5), s)
    assert m["frames"] == 3
    R2, t2, err = vt.floor_pose(k2, d0, m["world"], m["pixel"])
    assert err < 0.3
    assert abs((-R2.T @ t2)[2] - 1.4) < 0.02                    # z up (grid comes back mirrored), height ~1%
    pts = cv2.projectPoints(np.array([[-0.6, -0.5, 0.7], [0.9, -0.5, 0.7]]), rvec, t, k2, d0)[0]
    a, b = vt.pixel_to_plane(pts.reshape(-1, 2), k2, d0, R2, t2, 0.7)
    d_err = abs(np.linalg.norm(a[:2] - b[:2]) - 1.5)
    assert d_err < 0.03                                          # measured 1.6 cm: 23 cm board, 9 px squares
    assert m["sigma_px"] == pytest.approx(vt.DETECTOR_PX, abs=1e-3)                   # identical frames: floor only
    assert d_err / 2 < vt.pose_noise(k2, d0, m["world"], m["pixel"], m["sigma_px"], 0.7, n=50) < 0.05
