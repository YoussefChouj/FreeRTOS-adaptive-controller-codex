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
        cw.writerow(["t", vt.X_CM, vt.Y_CM, vt.Z_M, vt.PHASE, vt.ARM, vt.X_SP, vt.Y_SP])
        for i, ti in enumerate(te):
            cw.writerow([100 + ti, est[i, 0], est[i, 1], h, 1 if 2 <= ti <= 22 else 0, 1 if 1 <= ti <= 23 else 0,
                         est[i, 0], est[i, 1]])

    marks = [[0, 0], [1.5, 0], [0, 1.5], [1.5, 1.5]]
    pix = cv2.projectPoints(np.c_[marks, np.zeros(4)].astype(float), rvec, t, k2, d0)[0].reshape(-1, 2)
    cam = {"K": k2.tolist(), "dist": d0.tolist()}
    rep = vt.run(video, cam, {"world": marks, "pixel": pix.tolist()}, str(path), tmp_path / "out",
                 sync="speed")      # synthetic video starts airborne: no lift-off to sync on
    assert abs(rep["sync_offset_s"] - off) <= 0.04
    assert abs(rep["align_rot_deg"] - 30) < 1 and not rep["align_reflection"]
    assert rep["err_rms_m"] < 0.02 and rep["err_max_m"] < 0.06
    assert abs(rep["scale_truth_over_est"] - 1) < 0.03
    assert (tmp_path / "out" / "truth.png").exists()
    mp4 = tmp_path / "overlay.mp4"
    assert vt.overlay(video, cam, {"world": marks, "pixel": pix.tolist()}, str(path), tmp_path / "out", mp4, 640) == 690
    cap = cv2.VideoCapture(str(mp4))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 600)                        # 20 s in: trails drawn, plan and truth overlap
    img = cap.read()[1]
    assert img.shape == (360, 640, 3)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    assert (cv2.inRange(hsv, (50, 120, 120), (70, 255, 255)) > 0).sum() > 200     # green truth trail present


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


def test_calibrate_recovers_intrinsics(tmp_path):
    """Board shown at 30 oblique poses: SB corners + calibrateCamera give back f, centre and distortion."""
    k2, d2, s = np.array([[900.0, 0, 640], [0, 900.0, 360], [0, 0, 1]]), np.array([0.08, -0.05, 0, 0, 0]), 0.026
    video = str(tmp_path / "lens.avi")
    w = cv2.VideoWriter(video, cv2.VideoWriter_fourcc(*"MJPG"), 30, (1280, 720))
    mid = np.array([4.5 * s, 3 * s, 0])
    for a in np.radians(np.arange(0, 360, 12)):
        c = mid + [0.25 * np.cos(a), 0.18 * np.sin(a), 0.45]
        R, t = _lookat(c, mid + [0.06 * np.cos(2 * a), 0.04 * np.sin(a), 0])
        rvec = cv2.Rodrigues(R)[0]

        def poly(x0, y0, x1, y1):
            p = cv2.projectPoints(np.array([[x0, y0, 0], [x1, y0, 0], [x1, y1, 0], [x0, y1, 0]], float), rvec, t, k2, d2)[0]
            return np.round(p.reshape(-1, 2) * 16).astype(np.int32)

        img = np.full((720, 1280, 3), 110, np.uint8)
        cv2.fillConvexPoly(img, poly(-s, -s, 10 * s, 7 * s), (230, 230, 230), cv2.LINE_AA, 4)
        for r in range(6):
            for col in range(9):
                if (r + col) % 2:
                    cv2.fillConvexPoly(img, poly(col * s, r * s, (col + 1) * s, (r + 1) * s), (30, 30, 30), cv2.LINE_AA, 4)
        w.write(img)
    w.release()
    cam = vt.calibrate(video, (8, 5), s, step=1)
    part = vt.calibrate(video, (8, 5), s, step=1, t_end=0.28, max_frames=6)
    assert (part["frames"], part["frames_found"], part["spread_cm"]) == (6, 9, None)   # 9 frames by 0.28 s, thinned to 6
    assert cam["frames"] == 30 and cam["rms_px"] < 0.3 and cam["worst_frame_px"] < 0.5
    K = np.array(cam["K"])
    assert abs(K[0, 0] - 900) < 5 and abs(K[1, 1] - 900) < 5      # measured 897.4 / 898.0, rms 0.05 px
    assert abs(K[0, 2] - 640) < 3 and abs(K[1, 2] - 360) < 3      # measured 640.0 / 362.4
    assert cam["coverage_6x6"] == pytest.approx(18 / 36)          # centre-heavy views
    assert cam["spread_cm"]["rms"] < 1.5 and cam["spread_cm"]["max"] < 3   # measured 0.58 / 1.27 cm at 4 m


def test_calibrate_charuco_uses_cut_off_boards(tmp_path):
    """ChArUco board rendered through the lens by ray casting, half the views cut by the frame edge: they still count."""
    k2, d2, s, sq, mg = np.array([[900.0, 0, 640], [0, 900.0, 360], [0, 0, 1]]), np.array([0.08, -0.05, 0, 0, 0]), 0.026, 60, 20
    board = vt.board_image((13, 7), sq, mg)
    assert vt.board_image((13, 7)).shape == (1080, 1920)                       # full-screen target
    uv = np.stack(np.meshgrid(np.arange(1280.0), np.arange(720.0)), -1).reshape(-1, 1, 2)
    ray = np.c_[cv2.undistortPoints(uv, k2, d2).reshape(-1, 2), np.ones(len(uv))]
    video = str(tmp_path / "charuco.avi")
    w = cv2.VideoWriter(video, cv2.VideoWriter_fourcc(*"MJPG"), 30, (1280, 720))
    mid, cut = np.array([6.5 * s, 3.5 * s, 0]), 0
    for i, a in enumerate(np.radians(np.arange(0, 360, 12))):
        near = i % 2                                   # odd views: close and aimed off-centre, so the board leaves the frame
        c = mid + [0.25 * np.cos(a), 0.18 * np.sin(a), -(0.30 if near else 0.55)]   # -z side: board seen as printed
        aim = [0.12 * np.cos(a + 2), 0.06 * np.sin(a + 2), 0] if near else [0.03 * np.cos(2 * a), 0.02 * np.sin(a), 0]
        R, t = _lookat(c, mid + aim)
        d, C = ray @ R, -R.T @ t                       # board-frame rays from the camera centre
        X = C + (-C[2] / d[:, 2])[:, None] * d
        m = (mg + X[:, :2] / s * sq - 0.5).astype(np.float32).reshape(720, 1280, 2)
        img = cv2.remap(board, m[..., 0], m[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=110)
        cut += bool(img[0].min() < 50 or img[-1].min() < 50 or img[:, 0].min() < 50 or img[:, -1].min() < 50)
        w.write(cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
    w.release()
    cam = vt.calibrate(video, (13, 7), s, step=1, charuco=True)
    assert cut == 15 and cam["frames"] == 30                                   # all 15 cut-off views used
    assert cam["rms_px"] < 0.3 and cam["worst_frame_px"] < 0.5
    K = np.array(cam["K"])
    assert abs(K[0, 0] - 900) < 5 and abs(K[1, 1] - 900) < 5      # measured 902.4 / 901.4, rms 0.15 px
    assert abs(K[0, 2] - 640) < 3 and abs(K[1, 2] - 360) < 5      # measured 639.9 / 363.4
    assert cam["coverage_6x6"] == pytest.approx(24 / 36)                       # full-board checkerboard test: 18 / 36


def test_takeoff_sync():
    tv = np.arange(0, 30, 1 / 30)
    xy = np.zeros((len(tv), 2)) + np.random.default_rng(0).normal(0, 0.005, (len(tv), 2))
    xy[tv >= 18.5, 0] += 0.3                              # lifts off (moves away from the pad spot) at 18.5 s
    te = np.arange(0, 12, 0.02)
    z = np.where(te >= 0.9, 0.5, 0.0)
    off = vt.takeoff_tel(te, z, np.ones(len(te), bool)) - vt.takeoff_video(tv, xy)
    assert abs(off - (0.9 - 18.5)) < 0.05


def test_despike_removes_frame_spikes_without_lag():
    t = np.linspace(0, 10, 500)
    clean = np.c_[np.sin(t), np.where(t > 5, 0.5, 0.0)]          # smooth path + a real step
    noisy = clean.copy()
    noisy[np.arange(20, 480, 46), 0] += 0.6                       # one-frame jumps (guard count changes)
    out, n_bad = vt.despike(noisy)
    assert n_bad >= 10
    assert np.abs(out[5:-5, 0] - clean[5:-5, 0]).max() < 0.025    # within one sample step of the ramp; ends: edge padding
    assert np.abs(out[:, 1] - clean[:, 1]).max() < 1e-9           # the step stays where it is


def _seg_dist(p, poly):
    a, ab = poly[:-1], np.diff(poly, axis=0)
    w = np.clip(((p - a) * ab).sum(1) / (ab * ab).sum(1), 0, 1)
    return float(np.min(np.linalg.norm(a + w[:, None] * ab - p, axis=1)))


def test_junction_line_sits_on_the_wall_base_at_true_scale():
    rvec, R, t = _pose()
    n_w = np.array([1.0, 0, 0])                                   # wall x = 1.5 m, facing the camera side
    Rc = np.eye(3); Rc[:, 2] = R @ n_w
    tg = {"qr": [], "boards": [{"cam": (Rc, R @ [1.5, 0.3, 0.5] + t)}]}
    lines = vt.junction_lines(tg, K, DIST, R, t, scales=(1.0, 1.2))
    base = _project([[1.5, y, 0.0] for y in (-0.5, 0.0, 0.5)], rvec, t)
    assert max(_seg_dist(p, lines[("boards", 1.0)]) for p in base) < 1.0
    assert min(_seg_dist(p, lines[("boards", 1.2)]) for p in base) > 20.0


def test_wall_report_spacing_and_angles():
    qr = [{"centre": np.array([x, 3.0, z]), "normal": np.array([0, -1.0, 0]), "reproj_px": 0.3}
          for z in (1.3, 0.9) for x in (-0.5, 0.0, 0.5)]
    boards = [{"centre": np.array([2.0, y, 0.5]), "normal": np.array([1.0, 0, 0.02]), "reproj_px": 0.4} for y in (0, 1)]
    rep = vt.wall_report({"qr": qr, "boards": boards, "cam_xy": np.zeros(2)})
    assert rep["qr_top_centre_spacing_m"] == [0.5, 0.5] and rep["qr_bottom_centre_spacing_m"] == [0.5, 0.5]
    assert rep["qr_row_centre_spacing_m"] == [0.4, 0.4, 0.4]
    assert rep["qr_top_height_m"] == [1.3, 1.3, 1.3]
    assert rep["front_vs_right_wall_deg"] == pytest.approx(90.0, abs=0.1)
    assert rep["boards_tilt_from_vertical_deg"] == [pytest.approx(1.15, abs=0.01)] * 2


def test_survey_recovers_yaw_handedness_and_scale():
    K, dist = np.array([[1000.0, 0, 960], [0, 1000.0, 540], [0, 0, 1]]), np.zeros(5)
    C = np.array([2.0, -1.0, 1.9]); f = -C / np.linalg.norm(C)                 # camera looks at the board origin
    r = np.cross(f, [0, 0, 1.0]); r /= np.linalg.norm(r); R = np.array([r, np.cross(f, r), f]); t = -R @ C
    pad, a = np.array([0.1, 0.05]), 0.95 * np.exp(1j * np.radians(30))        # tape = 0.95 x board, yawed 30 deg
    drone = lambda p: (lambda z: [z.real, z.imag])(a * complex(*(np.asarray(p) - pad)))
    q = np.array([0.4, 0.3, 0.0]); uv = K @ (R @ q + t); uv = uv[:2] / uv[2]
    sv = vt.survey(K, dist, R, t, pad, drone(C[:2]) + [1.9 * 0.95], [(uv, drone(q[:2]))])
    assert not sv["mirror"] and sv["handedness_decided"] and sv["fit_rms_m"] < 1e-6 and sv["other_handedness_rms_m"] > 0.1
    assert abs(sv["yaw_board_to_drone_deg"] - 30) < 0.1 and abs(sv["scale_tape_over_board"] - 0.95) < 1e-4
    assert abs(sv["cam_height_tape_over_board"] - 0.95) < 1e-6
    assert np.allclose(vt.to_drone([[0.3, 0.0]], sv), [[0.285 * np.cos(np.radians(30)), 0.285 * np.sin(np.radians(30))]])
