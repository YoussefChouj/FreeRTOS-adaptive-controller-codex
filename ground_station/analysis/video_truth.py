"""Video ground truth for optical-flow roams: a fixed phone films the drone, the orange prop guards give its floor xy.

    python -m ground_station.analysis.video_truth calib <board.mp4> --board 8x5 --square 0.026 [--until 60] --out cam.json   # prints lens spread
    python -m ground_station.analysis.video_truth board --squares 13x7 --out docs/video-truth/charuco_13x7.png   # ChArUco target
    python -m ground_station.analysis.video_truth calib <board.mp4> --board charuco:13x7 --square <measured m> --out cam.json
    python -m ground_station.analysis.video_truth floor <roam.mp4> --board 8x5 --square 0.026 --out marks.json
    python -m ground_station.analysis.video_truth click <roam.mp4> --world "0,0 1.5,0 0,1.5 1.5,1.5" --out marks.json
    python -m ground_station.analysis.video_truth frame <roam.mp4> --t 2.0 --out frame.png      # check the colour mask
    python -m ground_station.analysis.video_truth run <roam.mp4> --cam cam.json --marks marks.json \
        --csv logs/livewatch/<roam>.csv --out logs/video_truth/<roam>

Pipeline: lens model from a checkerboard or ChArUco video (calib; ChArUco also uses boards cut off by the frame edge) -> camera pose from a checkerboard lying on the floor (floor:
found automatically, origin = its first inner corner) or >= 4 measured floor tape marks (click; metres, x = drone
nose, y = drone left at take-off); report.json gives the 95% floor-pose error 1.5 m out from mark noise -> per frame, orange blobs -> each guard centroid is cast
as a ray onto the plane z = h(t) + guard offset -> drone centre from the guards (see drone_centre: merged and hidden
guards are handled by blob area). Video and telemetry are synced by cross-correlating horizontal speed; the estimator path (locx/locy FB, cm) is
rotated onto the truth with an orthogonal fit over the first seconds of flight only, so later drift is not absorbed.
Thresholds (HSV band, min blob area, align window) are starting points to tune on the first video, not measured values.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

HSV_LO, HSV_HI = (5, 120, 100), (22, 255, 255)   # orange, OpenCV H 0-180 (tune with `frame`)
MIN_AREA = 30                                      # px, smallest blob kept
DETECTOR_PX = 0.15                                 # px per axis, SB corner error on a perfect render (measured 0.12-0.14 mean)
CHARUCO_DICT, CHARUCO_MARKER = "DICT_5X5_100", 0.75   # marker side / square side
CHARUCO_MIN = 12                                   # corners a partial ChArUco view needs to be used
X_CM, Y_CM, Z_M = "Ctrler.locxPID.FB", "Ctrler.locyPID.FB", "Ctrler.Z_posPID.FB"
X_SP, Y_SP = "Ctrler.locxPID.Des", "Ctrler.locyPID.Des"   # position setpoints, estimator frame (cm)
PHASE, ARM = "flight_phase", "DroneStatus.ARM_Status"


def _cv2():
    import cv2
    return cv2


def charuco_board(squares: tuple[int, int], square: float = 1.0):
    """ChArUco board of `squares` (cols, rows) squares; corner ids and layout follow OpenCV >= 4.6 (non-legacy)."""
    a = _cv2().aruco
    return a.CharucoBoard(squares, square, square * CHARUCO_MARKER, a.getPredefinedDictionary(getattr(a, CHARUCO_DICT)))


def board_image(squares: tuple[int, int], square_px: int = 140, margin_px: int = 50) -> np.ndarray:
    """Printable / full-screen ChArUco image: 13x7 squares at 140 px + 50 px margin = 1920x1080."""
    size = (squares[0] * square_px + 2 * margin_px, squares[1] * square_px + 2 * margin_px)
    return charuco_board(squares).generateImage(size, marginSize=margin_px)


# ---------------------------------------------------------------- geometry
def _spread_cm(objs, imgs, size, dist_m=4.0) -> dict | None:
    """Lens-model repeatability: calibrate 3 interleaved thirds of the views and compare their rays, scaled to `dist_m`
    (common offset removed: the floor pose absorbs it), inside the region the board covered. Outside it the polynomial
    distortion is extrapolated and unbounded: that part is what coverage_6x6 reports."""
    if len(objs) < 30:
        return None
    cv2 = _cv2()
    hull = cv2.convexHull(np.concatenate(imgs).reshape(-1, 1, 2).astype(np.float32))
    u, v = np.meshgrid(np.linspace(0, size[0] - 1, 24), np.linspace(0, size[1] - 1, 24))
    grid = np.array([p for p in np.c_[u.ravel(), v.ravel()] if cv2.pointPolygonTest(hull, (float(p[0]), float(p[1])), False) >= 0],
                    np.float32).reshape(-1, 1, 2)
    rays = []
    for s in range(3):
        _, K, d, _, _ = cv2.calibrateCamera(objs[s::3], imgs[s::3], size, None, None)
        rays.append(cv2.undistortPoints(grid, K, d).reshape(-1, 2))
    e = np.concatenate([np.linalg.norm((a - b) - (a - b).mean(0), axis=1)
                        for a, b in ((rays[0], rays[1]), (rays[0], rays[2]), (rays[1], rays[2]))]) * dist_m * 100
    return {"rms": float(np.sqrt(np.mean(e ** 2))), "max": float(e.max()), "at_m": dist_m}


def calibrate(video: str, board: tuple[int, int], square: float, step: int = 10, t_end: float | None = None,
              max_frames: int = 45, charuco: bool = False) -> dict:
    """Lens model from a board video: every `step`-th frame with a full checkerboard (`board` = inner corners) or, with
    `charuco`, >= CHARUCO_MIN ChArUco corners spanning 3+ rows and columns (`board` = squares; partial boards count, so
    the frame edges get covered), up to `t_end` s (lens part of a clip that goes on to film the roam), thinned evenly
    to `max_frames` (calibrateCamera time grows steeply with views: measured 0.5 s for 10, 10 s for 30 views of 40
    corners at 4K)."""
    cv2 = _cv2()
    obj = np.zeros((board[0] * board[1], 3), np.float32)
    obj[:, :2] = np.mgrid[0:board[0], 0:board[1]].T.reshape(-1, 2) * square
    if charuco:
        cb = charuco_board(board, square)
        det, all_obj = cv2.aruco.CharucoDetector(cb), cb.getChessboardCorners().astype(np.float32)
    cap, objs, imgs, size, i = cv2.VideoCapture(video), [], [], None, 0
    while cap.grab():
        i += 1
        if t_end is not None and cap.get(cv2.CAP_PROP_POS_MSEC) > t_end * 1000:
            break
        if i % step:
            continue
        g = cv2.cvtColor(cap.retrieve()[1], cv2.COLOR_BGR2GRAY)
        size = g.shape[::-1]
        if charuco:
            c, ids, _, _ = det.detectBoard(g)
            o = all_obj[ids.ravel()] if ids is not None else np.zeros((0, 3), np.float32)
            if len(o) >= CHARUCO_MIN and len(np.unique(o[:, 0])) >= 3 and len(np.unique(o[:, 1])) >= 3:   # not a line
                objs.append(o)
                imgs.append(c.astype(np.float32))
            continue
        found, c = cv2.findChessboardCornersSB(g, board)    # classic detector misses the board in 4K frames
        if found:                                          # a mirrored grid is a rigid flip of a plane: harmless
            objs.append(obj)
            imgs.append(c)
    if len(objs) < 5:
        raise SystemExit(f"only {len(objs)} frames with a usable {board[0]}x{board[1]} board; need >= 5")
    found = len(objs)
    if found > max_frames:
        keep = np.linspace(0, found - 1, max_frames).round().astype(int)
        objs, imgs = [objs[k] for k in keep], [imgs[k] for k in keep]
    rms, K, dist, rv, tv = cv2.calibrateCamera(objs, imgs, size, None, None)
    per = [float(np.sqrt(np.mean(np.sum((cv2.projectPoints(o, r, t, K, dist)[0] - c) ** 2, axis=2))))
           for o, c, r, t in zip(objs, imgs, rv, tv)]
    pts = np.concatenate(imgs).reshape(-1, 2) / size * 6
    cells = len({(int(u), int(v)) for u, v in pts})       # 6x6 image grid cells a corner landed in
    return {"K": K.tolist(), "dist": dist.ravel().tolist(), "rms_px": float(rms), "frames": len(objs),
            "frames_found": found, "size": list(size), "worst_frame_px": max(per), "coverage_6x6": cells / 36,
            "spread_cm": _spread_cm(objs, imgs, size)}


def floor_pose(K, dist, world_xy, pixel) -> tuple[np.ndarray, np.ndarray, float]:
    """Camera pose from floor marks (z = 0). Returns R, t (world -> camera) and the mean reprojection error in px."""
    cv2 = _cv2()
    w = np.c_[np.asarray(world_xy, float), np.zeros(len(world_xy))]
    p = np.asarray(pixel, float)
    K, dist = np.asarray(K, float), np.asarray(dist, float)
    ok, rvec, tvec = cv2.solvePnP(w, p, K, dist, flags=cv2.SOLVEPNP_IPPE)
    if not ok:
        raise SystemExit("solvePnP failed on the floor marks")
    proj, _ = cv2.projectPoints(w, rvec, tvec, K, dist)
    err = float(np.mean(np.linalg.norm(proj.reshape(-1, 2) - p, axis=1)))
    R, t = cv2.Rodrigues(rvec)[0], tvec.ravel()
    if (-R.T @ t)[2] < 0:                                # marks left-handed seen from above: mirror y so z points up
        R = R @ np.diag([1.0, -1.0, -1.0])
    return R, t, err


def floor_board(video: str, board: tuple[int, int], square: float, every_s: float = 0.5, max_frames: int = 60,
                roi=None) -> dict:
    """Floor marks from a checkerboard lying flat on the floor (the phone is fixed, so every detection sees the same
    corners): per-corner median pixel over the frames where the whole board is found. Origin = first inner corner.
    roi (x0, y0, x1, y1) px limits the search: boards of the same size on a wall would give a wall plane."""
    cv2 = _cv2()
    cap = cv2.VideoCapture(video)
    step = max(1, int(round(every_s * (cap.get(cv2.CAP_PROP_FPS) or 30.0))))
    flags = cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY
    ref, found, i = None, [], 0
    while len(found) < max_frames and cap.grab():
        i += 1
        if (i - 1) % step:
            continue
        img = cap.retrieve()[1]
        x0, y0, x1, y1 = roi or (0, 0, img.shape[1], img.shape[0])
        ok, c = cv2.findChessboardCornersSB(cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY), board, flags=flags)
        if not ok:
            continue
        c = c.reshape(-1, 2) + (x0, y0)
        if ref is None:
            ref = c
        elif np.linalg.norm(c[0] - ref[-1]) < np.linalg.norm(c[0] - ref[0]):
            c = c[::-1]                                  # same board read from the other end
        found.append(c)
    if len(found) < 3:
        raise SystemExit(f"floor board {board[0]}x{board[1]} found in {len(found)} frames; need >= 3")
    P = np.array(found)
    pixel = np.median(P, axis=0)
    spread = float(np.median(np.linalg.norm(P - pixel, axis=2)))
    sigma = 1.2533 * (spread / 1.1774) / np.sqrt(len(found))   # per-axis sd of a median of n (median radius = 1.18 sd)
    sigma = float(np.hypot(sigma, DETECTOR_PX))                 # a static board repeats the detector's own error every frame
    world = np.mgrid[0:board[0], 0:board[1]].T.reshape(-1, 2) * square
    return {"world": world.tolist(), "pixel": pixel.tolist(), "frames": len(found),
            "corner_spread_px": round(spread, 3), "sigma_px": round(float(sigma), 4)}


def _plane_pose(cv2, obj, img_pts, K, dist, flag):
    ok, rvec, tvec = cv2.solvePnP(np.asarray(obj, float), np.asarray(img_pts, float), K, dist, flags=flag)
    proj = cv2.projectPoints(np.asarray(obj, float), rvec, tvec, K, dist)[0].reshape(-1, 2)
    return cv2.Rodrigues(rvec)[0], tvec.ravel(), float(np.mean(np.linalg.norm(proj - img_pts, axis=1)))


def wall_targets(frame, K, dist, R, t, qr_side=0.19, board=(8, 5), square=0.026, roi=None, max_boards=8) -> dict:
    """Wall targets in the floor frame, each sized by its own print (QR side, board square), so they check the floor
    pose without trusting it: code / board normals must be horizontal (floor tilt), the two walls perpendicular,
    and the code spacing comes out in metres. R, t: floor pose (world -> camera)."""
    cv2 = _cv2()
    K, dist = np.asarray(K, float), np.asarray(dist, float)
    to_w = lambda Xc: (R.T @ (np.asarray(Xc, float).T - t[:, None])).T     # camera -> floor frame (points)
    out = {"qr": [], "boards": [], "cam_xy": (-R.T @ t)[:2]}
    ok, pts = cv2.QRCodeDetectorAruco().detectMulti(frame)
    h = qr_side / 2
    sq = np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]])   # TL TR BR BL, y up
    for c in (pts if ok else []):
        Rc, tc, err = _plane_pose(cv2, sq, c.reshape(4, 2), K, dist, cv2.SOLVEPNP_IPPE_SQUARE)
        out["qr"].append({"centre": to_w(tc[None])[0], "normal": R.T @ Rc[:, 2], "px": c.reshape(4, 2).mean(0),
                          "side_px": float(np.mean(np.linalg.norm(np.diff(np.r_[c.reshape(4, 2), c[:1].reshape(1, 2)], axis=0), axis=1))),
                          "reproj_px": err, "cam": (Rc, tc)})
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    x0, y0, x1, y1 = roi or (0, 0, grey.shape[1], grey.shape[0])
    obj = np.c_[np.mgrid[0:board[0], 0:board[1]].T.reshape(-1, 2) * square, np.zeros(board[0] * board[1])]
    for _ in range(max_boards):
        ok, c = cv2.findChessboardCornersSB(grey[y0:y1, x0:x1], board, flags=cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY)
        if not ok:
            break
        c = c.reshape(-1, 2) + (x0, y0)
        Rc, tc, err = _plane_pose(cv2, obj, c, K, dist, cv2.SOLVEPNP_IPPE)
        n = R.T @ Rc[:, 2]
        out["boards"].append({"centre": to_w((Rc @ obj.mean(0) + tc)[None])[0], "normal": n, "px": c.mean(0),
                              "reproj_px": err, "cam": (Rc, tc)})
        cv2.fillConvexPoly(grey, cv2.convexHull(c.astype(np.int32)), 128)    # hide it, look for the next one
    return out


def wall_report(tg: dict) -> dict:
    """Numbers from wall_targets: tilt of each target from vertical, wall-to-wall angle, QR centre spacing."""
    tilt = lambda n: round(float(np.degrees(np.arcsin(min(1.0, abs(n[2]) / np.linalg.norm(n))))), 2)
    rep = {"qr_found": len(tg["qr"]), "boards_found": len(tg["boards"])}
    for key in ("qr", "boards"):
        if tg[key]:
            rep[f"{key}_tilt_from_vertical_deg"] = [tilt(d["normal"]) for d in tg[key]]
            rep[f"{key}_reproj_px"] = [round(d["reproj_px"], 2) for d in tg[key]]
    if tg["qr"] and tg["boards"]:
        nq, nb = (_mean_normal([d["normal"] for d in tg[k]]) for k in ("qr", "boards"))
        a = float(np.degrees(np.arccos(abs(nq[:2] @ nb[:2]) / np.linalg.norm(nq[:2]) / np.linalg.norm(nb[:2]))))
        rep["front_vs_right_wall_deg"] = round(a, 2)
    q = tg["qr"]
    if len(q) >= 2:
        C = np.array([d["centre"] for d in q])
        top = C[:, 2] > np.median(C[:, 2]) if len(q) == 6 else np.ones(len(q), bool)
        along = np.cross([0, 0, 1], _mean_normal([d["normal"] for d in q]))[:2]   # horizontal axis in the wall
        rows = {}
        for name, m in (("top", top), ("bottom", ~top)):
            r = C[m][np.argsort(C[m][:, :2] @ along)]
            rows[name] = r
            rep[f"qr_{name}_centre_spacing_m"] = [round(float(np.linalg.norm(b - a)), 3) for a, b in zip(r[:-1], r[1:])]
            rep[f"qr_{name}_height_m"] = [round(float(z), 3) for z in r[:, 2]]
        if len(rows["top"]) == len(rows["bottom"]) > 0:
            rep["qr_row_centre_spacing_m"] = [round(float(np.linalg.norm(a - b)), 3) for a, b in zip(rows["top"], rows["bottom"])]
        rep["qr_wall_distance_m"] = round(float(np.median(np.linalg.norm(C[:, :2] - tg["cam_xy"], axis=1))), 3)
    return rep


def _mean_normal(ns) -> np.ndarray:
    ns = np.array([n / np.linalg.norm(n) for n in ns])
    ns *= np.sign(ns @ ns[0])[:, None]                   # one side of the plane
    m = ns.sum(0)
    return m / np.linalg.norm(m)


def junction_lines(tg: dict, K, dist, R, t, scales=(0.8, 0.9, 1.0, 1.1, 1.2), reach=4.0) -> dict:
    """Where each wall meets the floor, projected into the image, for the floor pose scaled by each factor. The wall
    planes come from the targets' own print size; the floor height from the floor board. The scale whose line lies
    on the visible wall/floor corner is the true one (a 10 % floor scale error moves the line ~100 px at 4K)."""
    cv2 = _cv2()
    K, dist = np.asarray(K, float), np.asarray(dist, float)
    nf = R[:, 2]                                         # floor normal, camera frame; floor: nf.X = s * nf.t
    lines = {}
    for key in ("qr", "boards"):
        if not tg[key]:
            continue
        nw = _mean_normal([d["cam"][0][:, 2] for d in tg[key]])
        dw = float(np.median([nw @ d["cam"][1] for d in tg[key]]))
        u = np.cross(nf, nw); u /= np.linalg.norm(u)
        for s in scales:
            A = np.array([nf, nw, u]); p0 = np.linalg.solve(A, [s * (nf @ t), dw, 0.0])
            P = p0 + np.linspace(-reach, reach, 81)[:, None] * u
            P = P[P[:, 2] > 0.2]                         # in front of the camera
            if len(P) >= 2:
                lines[(key, s)] = cv2.projectPoints(P, np.zeros(3), np.zeros(3), K, dist)[0].reshape(-1, 2)
    return lines


def pose_noise(K, dist, world_xy, pixel, sigma_px, z, reach=1.5, n=200, seed=0) -> float:
    """95th-percentile xy error, from mark pixel noise alone (Monte Carlo), at `reach` m around the origin, height z."""
    cv2 = _cv2()
    K, dist, pixel = np.asarray(K, float), np.asarray(dist, float), np.asarray(pixel, float)
    R, t, _ = floor_pose(K, dist, world_xy, pixel)
    a = np.arange(8) * np.pi / 4
    ring = np.c_[reach * np.cos(a), reach * np.sin(a), np.full(8, z)]     # mirror-symmetric: unaffected by the y flip
    uv = cv2.projectPoints(ring, cv2.Rodrigues(R)[0], t, K, dist)[0].reshape(-1, 2)
    rng, errs = np.random.default_rng(seed), []
    for _ in range(n):
        R2, t2, _ = floor_pose(K, dist, world_xy, pixel + rng.normal(0, sigma_px, pixel.shape))
        errs.append(np.linalg.norm(pixel_to_plane(uv, K, dist, R2, t2, z)[:, :2] - ring[:, :2], axis=1))
    return float(np.percentile(errs, 95))


def pixel_to_plane(uv, K, dist, R, t, z) -> np.ndarray:
    """Cast pixels as rays and intersect them with the world plane at height z (scalar or one per pixel)."""
    cv2 = _cv2()
    uv = np.asarray(uv, float).reshape(-1, 1, 2)
    crit = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 50, 1e-10)   # default 5 iters misses mm near edges
    n = cv2.undistortPointsIter(uv, np.asarray(K, float), np.asarray(dist, float), None, None, crit).reshape(-1, 2)
    d = (R.T @ np.c_[n, np.ones(len(n))].T).T          # ray directions, world frame
    c = -R.T @ t                                         # camera centre, world frame
    s = (np.broadcast_to(np.asarray(z, float), len(d)) - c[2]) / d[:, 2]
    return c + s[:, None] * d


def survey(K, dist, R, t, pad_xy, cam_tape, refs=()) -> dict:
    """Check the floor frame against tape measured in the drone frame (drone on the pad facing forward, origin = pad
    centre). cam_tape: lens (x, y, z); refs: (pixel, drone xy) floor points. Fits drone = a * board (complex, or a *
    mirrored board) over the camera foot point and the refs: |a| = tape / board scale, arg(a) = board -> drone yaw.
    With the camera alone both handednesses fit exactly; one floor ref decides it."""
    C = -R.T @ t
    zb, zd = [complex(*(C[:2] - pad_xy))], [complex(*cam_tape[:2])]
    for uv, xy in refs:
        zb.append(complex(*(pixel_to_plane(uv, K, dist, R, t, 0.0)[0][:2] - pad_xy))); zd.append(complex(*xy))
    zb, zd = np.array(zb), np.array(zd)
    fits = []
    for mirror in (False, True):
        b = zb.conj() if mirror else zb
        a = np.vdot(b, zd) / np.vdot(b, b)                     # least squares: sum(conj(b) zd) / sum |b|^2
        fits.append((float(np.sqrt(np.mean(np.abs(zd - a * b) ** 2))), mirror, complex(a)))
    fits.sort(key=lambda f: f[0])
    rms, mirror, a = fits[0]
    return {"cam_board_m": np.round(C, 3).tolist(), "cam_height_tape_over_board": round(cam_tape[2] / C[2], 4),
            "refs_board_m": [[round(z.real, 3), round(z.imag, 3)] for z in zb[1:]],
            "scale_tape_over_board": round(abs(a), 4), "yaw_board_to_drone_deg": round(float(np.degrees(np.angle(a))), 1),
            "mirror": mirror, "fit_rms_m": round(rms, 3), "other_handedness_rms_m": round(fits[1][0], 3),
            "handedness_decided": len(zb) > 1, "a": [a.real, a.imag]}


def to_drone(v_board, sv: dict) -> np.ndarray:
    """Board-frame horizontal vector(s) -> drone frame with a survey() fit."""
    v = np.atleast_2d(np.asarray(v_board, float))[:, :2]
    z = v[:, 0] + 1j * v[:, 1]
    z = (z.conj() if sv["mirror"] else z) * complex(*sv["a"])
    return np.c_[z.real, z.imag]


# ---------------------------------------------------------------- detection
def colour_mask(frame_bgr, lo=HSV_LO, hi=HSV_HI):
    """HSV band mask; lo H > hi H wraps through 180/0 (red guards: --hsv-lo 170,120,100 --hsv-hi 6,255,255)."""
    cv2 = _cv2()
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    if lo[0] <= hi[0]:
        return cv2.inRange(hsv, np.array(lo), np.array(hi))
    return cv2.inRange(hsv, np.array(lo), np.array((180, *hi[1:]))) | cv2.inRange(hsv, np.array((0, *lo[1:])), np.array(hi))


def static_mask(video, times, lo=HSV_LO, hi=HSV_HI, grow_px=15):
    """Colour pixels present at every one of `times` (s): scenery in the guard colour (net poles, mat prints).
    Pick times with the drone in different places so it is never in all of them."""
    cv2 = _cv2()
    m = None
    for ts in times:
        c = colour_mask(_grab(video, ts), lo, hi)
        m = c if m is None else m & c
    return cv2.dilate(m, np.ones((grow_px, grow_px), np.uint8))


def detect_guards(frame_bgr, lo=HSV_LO, hi=HSV_HI, min_area=MIN_AREA, keep=4, static=None) -> np.ndarray:
    """(u, v, area) of the `keep` largest guard-colour blobs, largest first; `static` pixels are ignored."""
    cv2 = _cv2()
    mask = colour_mask(frame_bgr, lo, hi)
    if static is not None:
        mask &= ~static
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, _, stats, cent = cv2.connectedComponentsWithStats(mask)
    idx = [i for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= min_area]
    idx = sorted(idx, key=lambda i: -stats[i, cv2.CC_STAT_AREA])[:keep]
    return np.c_[cent[idx], stats[idx, cv2.CC_STAT_AREA]] if idx else np.zeros((0, 3))


def drone_centre(guards_xy: np.ndarray, areas=None):
    """Drone centre from guard positions on the plane. In an oblique view two guards can merge into one blob of
    about twice the area, so blob areas decide: 4 blobs -> mean; 3 with one >= 1.5x the others -> merged pair
    weighted 2; 3 similar -> one guard hidden, midpoint of the farthest (diagonal) pair; 2 similar -> two merged
    pairs, mean. Anything else -> None."""
    g = np.asarray(guards_xy, float)[:, :2]
    a = np.ones(len(g)) if areas is None else np.asarray(areas, float)
    if len(g) >= 4:
        return g[:4].mean(axis=0)
    if len(g) == 3:
        big = int(np.argmax(a))
        if a[big] >= 1.5 * np.median(np.delete(a, big)):
            w = np.ones(3); w[big] = 2
            return (w[:, None] * g).sum(axis=0) / 4
        pairs = [(0, 1), (0, 2), (1, 2)]
        i, j = max(pairs, key=lambda p: np.linalg.norm(g[p[0]] - g[p[1]]))
        return (g[i] + g[j]) / 2
    if len(g) == 2 and areas is not None and max(a) <= 1.5 * min(a):
        return g.mean(axis=0)
    return None


def track(video: str, lo=HSV_LO, hi=HSV_HI, min_area=MIN_AREA, stride: int = 1, static=None):
    """Video time (s) and guard pixels (list of arrays) per kept frame."""
    cv2 = _cv2()
    cap, ts, blobs, i = cv2.VideoCapture(video), [], [], 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % stride == 0:
            ts.append(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0)
            blobs.append(detect_guards(frame, lo, hi, min_area, static=static))
        i += 1
    return np.array(ts), blobs


# ---------------------------------------------------------------- sync + alignment
def _speed(t, xy, grid):
    x = np.interp(grid, t, xy[:, 0])
    y = np.interp(grid, t, xy[:, 1])
    v = np.hypot(np.gradient(x, grid), np.gradient(y, grid))
    k = max(1, int(round(0.2 / (grid[1] - grid[0]))))       # 0.2 s moving average
    return np.convolve(v, np.ones(k) / k, mode="same")


def sync_offset(t_v, xy_v, t_e, xy_e, max_lag: float = 30.0, dt: float = 0.02, min_overlap: float = 0.6) -> float:
    """Offset o such that telemetry time = video time + o, from the best speed cross-correlation.
    Candidates whose overlap covers less than `min_overlap` of the telemetry span are skipped: a few seconds of
    overlap at the edge of the search can correlate better by chance than the true full-flight alignment."""
    grid_e = np.arange(t_e[0], t_e[-1], dt)
    se = _speed(t_e, xy_e, grid_e)
    best = (-np.inf, 0.0)
    for o in np.arange(-max_lag, max_lag + dt / 2, dt):
        g = grid_e - o                                   # video times that line up with grid_e
        m = (g >= t_v[0]) & (g <= t_v[-1])
        if m.sum() < max(50, min_overlap * len(grid_e)):
            continue
        sv = _speed(t_v, xy_v, g[m])
        a, b = se[m] - se[m].mean(), sv - sv.mean()
        r = float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))
        if r > best[0]:
            best = (r, float(o))
    return best[1]


def takeoff_video(t, xy, pre_s: float = 2.0, thresh: float = 0.05, hold_s: float = 0.5) -> float:
    """Video take-off: first time the pad-height centre stays more than thresh m from its pre-roll spot for hold_s.
    A climb also moves the pad-height projection (away from the camera), so this fires on lift-off or slide."""
    pad = np.nanmedian(xy[t < t[0] + pre_s], axis=0)
    out = np.hypot(*(xy - pad).T) > thresh
    n = max(1, int(round(hold_s / np.median(np.diff(t)))))
    run_ = np.convolve(out.astype(int), np.ones(n, int), mode="valid") == n
    if not run_.any():
        raise SystemExit("no take-off in the video: the drone never left its pre-roll spot")
    return float(t[int(np.argmax(run_))])


def takeoff_tel(te, z, armed, rise: float = 0.05) -> float:
    """Telemetry lift-off: first armed sample with the height more than `rise` m above its armed pad value."""
    z = np.nan_to_num(np.asarray(z, float))
    z0 = float(np.median(z[armed][:25]))
    return float(te[int(np.argmax(armed & (z > z0 + rise)))])


def despike(xy, n: int = 9, k: float = 3.0) -> tuple[np.ndarray, int]:
    """Hampel filter (k scaled MADs), then a running median, over n samples per axis. One-frame spikes come from the
    guard count changing (merged or hidden guards, see drone_centre), not from the drone; a centred median adds no
    lag. Returns the cleaned copy and the number of samples the Hampel step replaced."""
    from numpy.lib.stride_tricks import sliding_window_view as win
    h, out, n_bad = n // 2, np.array(xy, float), 0
    for j in range(out.shape[1]):
        w = win(np.pad(out[:, j], h, mode="edge"), n)
        med = np.median(w, axis=1)
        mad = 1.4826 * np.median(np.abs(w - med[:, None]), axis=1)
        bad = np.abs(out[:, j] - med) > k * np.maximum(mad, 1e-3)
        out[bad, j], n_bad = med[bad], n_bad + int(bad.sum())
        out[:, j] = np.median(win(np.pad(out[:, j], h, mode="edge"), n), axis=1)
    return out, n_bad


def align(est_xy, truth_xy, n_fit: int) -> tuple[np.ndarray, bool]:
    """Orthogonal Q (rotation or reflection) with truth ~ est @ Q.T, fitted on the first n_fit samples.
    Both inputs are already relative to the take-off point."""
    a, b = np.asarray(est_xy[:n_fit], float), np.asarray(truth_xy[:n_fit], float)
    u, _, vt = np.linalg.svd(b.T @ a)
    q = u @ vt
    return q, bool(np.linalg.det(q) < 0)


# ---------------------------------------------------------------- telemetry
def read_livewatch(path: str, names) -> dict:
    with open(path, newline="") as f:
        first = f.readline()
    if first.startswith("received_ns"):        # session telemetry.csv (long rows): hold every key on names[0]'s ticks
        from ground_station.analysis.log_corpus import _load_session, hold
        series = _load_session(Path(path))
        if names[0] not in series:
            raise SystemExit(f"{path}: no column for {names[0]}")
        t = np.unique(series[names[0]][0])
        return {"t": t, **{n: hold(series, n, t) for n in names}}
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    hdr, body = rows[0], rows[1:]
    out = {"t": np.array([float(r[0]) for r in body])}
    for n in names:
        cols = sorted([i for i, h in enumerate(hdr) if h == n or h.endswith("." + n) or h.endswith(n)],
                      key=lambda i: len(hdr[i]))
        if not cols:
            raise SystemExit(f"{path}: no column for {n}")
        out[n] = np.array([float(r[cols[0]]) if r[cols[0]] not in ("", "nan") else np.nan for r in body])
    return out


# ---------------------------------------------------------------- run
def run(video, cam, marks, csv_path, out, guard_offset=0.0, align_s=None, stride=1, lo=HSV_LO, hi=HSV_HI,
        min_area=MIN_AREA, static_t=(), sync="takeoff") -> dict:
    """sync: "takeoff" (lift-off in both clocks; video and campaign started by hand), "speed" (speed
    cross-correlation) or a number of seconds (telemetry time = video time + sync).
    align_s None fits the estimator rotation over the whole flight (a hover-only window is ill-conditioned)."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    K, dist = cam["K"], cam["dist"]
    R, t, reproj = floor_pose(K, dist, marks["world"], marks["pixel"])
    tel = read_livewatch(csv_path, [X_CM, Y_CM, Z_M, PHASE, ARM])
    te = tel["t"] - tel["t"][0]
    fly = tel[PHASE] == 1
    armed = tel[ARM] > 0
    i_to, i_end = int(np.argmax(fly)), int(len(armed) - 1 - np.argmax(armed[::-1]))
    est = np.c_[tel[X_CM], tel[Y_CM]] / 100.0
    h_tel = np.nan_to_num(tel[Z_M])

    cache = out / "track.npz"
    if cache.exists():
        z = np.load(cache, allow_pickle=True)
        tv, blobs = z["t"], list(z["blobs"])
    else:
        tv, blobs = track(video, lo, hi, min_area, stride, static_mask(video, static_t, lo, hi) if static_t else None)
        arr = np.empty(len(blobs), dtype=object)      # ragged: np.array() would stack equal-shape frames
        for k, b in enumerate(blobs):
            arr[k] = b
        np.savez(cache, t=tv, blobs=arr)

    def centres(h_of_t):
        pts, ok = [], []
        for k, b in enumerate(blobs):
            c = None
            if len(b) >= 2:
                c = drone_centre(pixel_to_plane(b[:, :2], K, dist, R, t, h_of_t[k] + guard_offset), b[:, 2])
            ok.append(c is not None)
            pts.append(c if c is not None else (np.nan, np.nan))
        return np.array(pts), np.array(ok)

    # pass 1: constant height -> sync; pass 2: synced per-frame height
    h_med = float(np.nanmedian(h_tel[fly]))
    if sync == "speed":
        xy1, ok1 = centres(np.full(len(tv), h_med))
        off = sync_offset(tv[ok1], xy1[ok1], te[fly], est[fly])
    elif sync == "takeoff":
        xy1, ok1 = centres(np.zeros(len(tv)))
        off = takeoff_tel(te, h_tel, armed) - takeoff_video(tv[ok1], xy1[ok1])
    else:
        off = float(sync)
    xy, ok = centres(np.interp(tv + off, te, h_tel))
    tvs, (xy, n_spikes) = tv[ok] + off, despike(xy[ok])

    # landing spot vs take-off spot at pad level, before and after the flight (what a tape on the floor measures)
    xy0, ok0 = (xy1, ok1) if sync == "takeoff" else centres(np.zeros(len(tv)))
    t0v, p0 = tv[ok0] + off, despike(xy0[ok0])[0]
    after = (t0v > te[i_end] + 1.0) & (t0v < te[i_end] + 3.0)
    land = (np.median(p0[after], axis=0) if after.any() else np.full(2, np.nan)) - np.median(p0[t0v < t0v[0] + 2.0], axis=0)

    # truth on the telemetry clock, take-off-relative; est rotated onto it
    tr = np.c_[np.interp(te, tvs, xy[:, 0]), np.interp(te, tvs, xy[:, 1])]
    seg = slice(i_to, i_end + 1)
    e_rel, t_rel = est[seg] - est[i_to], tr[seg] - tr[i_to]
    n_fit = len(e_rel) if align_s is None else int(np.searchsorted(te[seg], te[i_to] + align_s))
    q, refl = align(e_rel, t_rel, max(n_fit, 10))
    e_al = e_rel @ q.T
    err = e_al - t_rel
    g = np.arange(te[i_to], te[i_end], 0.2)            # path on a 0.2 s grid: per-frame jitter would inflate it
    def path(xy_):
        return float(np.sum(np.hypot(np.diff(np.interp(g, te[seg], xy_[:, 0])), np.diff(np.interp(g, te[seg], xy_[:, 1])))))
    path_t, path_e = path(t_rel), path(e_al)
    rep = {
        "floor_reproj_px": round(reproj, 2),
        "floor_noise_err_1p5m_m": round(pose_noise(K, dist, marks["world"], marks["pixel"],
                                                   marks.get("sigma_px", 1.0), h_med + guard_offset), 4),
        "sync_offset_s": round(off, 3), "frames": int(len(tv)),
        "frames_tracked": int(ok.sum()), "spikes_replaced": n_spikes,
        "landing_vs_start_spot_m": [round(float(v), 3) for v in (*land, np.hypot(*land))],
        "est_landing_vs_start_m": round(float(np.hypot(*(est[i_end] - est[i_to]))), 3), "align_rot_deg": round(float(np.degrees(np.arctan2(q[1, 0], q[0, 0]))), 1),
        "align_reflection": refl, "flight_s": round(float(te[i_end] - te[i_to]), 2),
        "truth_at_disarm_m": [round(float(v), 3) for v in t_rel[-1]],
        "est_at_disarm_m": [round(float(v), 3) for v in e_al[-1]],
        "err_at_disarm_m": [round(float(v), 3) for v in err[-1]],
        "err_max_m": round(float(np.nanmax(np.hypot(*err.T))), 3),
        "err_rms_m": round(float(np.sqrt(np.nanmean(np.sum(err ** 2, axis=1)))), 3),
        "path_truth_m": round(path_t, 2), "path_est_m": round(path_e, 2),
        "sync": sync if isinstance(sync, str) else "manual",
        # least-squares truth ~ s * est over the flight
        "scale_truth_over_est": round(float(np.nansum(t_rel * e_al) / np.nansum(e_al * e_al)), 4),
        # estimator -> floor frame for `overlay`: floor_xy = truth0 + (est_m - est0) @ q.T
        "frame": {"q": q.tolist(), "est0": est[i_to].tolist(), "truth0": tr[i_to].tolist(), "z_off": guard_offset,
                  "t_to": float(te[i_to]), "t_end": float(te[i_end])},
    }
    with open(out / "truth.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_tel", "truth_x_m", "truth_y_m", "est_x_m", "est_y_m"])
        for k in range(len(t_rel)):
            w.writerow([f"{te[i_to + k]:.3f}", *(f"{v:.4f}" for v in t_rel[k]), *(f"{v:.4f}" for v in e_al[k])])
    (out / "report.json").write_text(json.dumps(rep, indent=2))
    _plot(te[seg], t_rel, e_al, out / "truth.png")
    return rep


def overlay(video, cam, marks, csv_path, run_dir, out_mp4, width=1920) -> int:
    """Demo video: planned path (setpoints), the drone's own estimate and the video truth drawn on the roam video."""
    cv2 = _cv2()
    run_dir = Path(run_dir)
    rep = json.loads((run_dir / "report.json").read_text())
    fr, off = rep["frame"], rep["sync_offset_s"]
    q, e0, t0 = np.array(fr["q"]), np.array(fr["est0"]), np.array(fr["truth0"])
    K, dist = np.array(cam["K"], float), np.array(cam["dist"], float)
    R, t, _ = floor_pose(K, dist, marks["world"], marks["pixel"])
    rvec = cv2.Rodrigues(R)[0]
    tel = read_livewatch(csv_path, [X_CM, Y_CM, Z_M, X_SP, Y_SP])
    te = tel["t"] - tel["t"][0]
    seg = (te >= fr["t_to"]) & (te <= fr["t_end"])
    te, z = te[seg], np.nan_to_num(tel[Z_M][seg]) + fr["z_off"]

    def px(xy, zz):                                   # floor-frame metres at height zz -> pixels (fixed camera)
        return cv2.projectPoints(np.c_[xy, zz].astype(float), rvec, t, K, dist)[0].reshape(-1, 2)

    def floor(xc, yc):
        return t0 + (np.c_[xc[seg], yc[seg]] / 100.0 - e0) @ q.T

    tr = np.loadtxt(run_dir / "truth.csv", delimiter=",", skiprows=1, ndmin=2)
    lines = {"plan": (te, px(floor(tel[X_SP], tel[Y_SP]), z), (0, 215, 255)),       # BGR: yellow
             "estimate": (te, px(floor(tel[X_CM], tel[Y_CM]), z), (255, 160, 40)),   # blue
             "video truth": (tr[:, 0], px(t0 + tr[:, 1:3], np.interp(tr[:, 0], te, z)), (60, 220, 60))}  # green
    cap = cv2.VideoCapture(video)
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    sc = width / W
    size = (width, int(round(H * sc)))
    w = cv2.VideoWriter(str(out_mp4), cv2.VideoWriter_fourcc(*"mp4v"), cap.get(cv2.CAP_PROP_FPS) or 30, size)
    lw = max(2, width // 640)
    n = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        now = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0 + off                         # telemetry clock
        img = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
        tp, pp, _ = lines["plan"]
        cv2.polylines(img, [np.int32(pp[np.isfinite(pp).all(1)] * sc * 16)], False, (90, 90, 90), lw, cv2.LINE_AA, 4)
        heads = {}
        for y, (name, (tl, pl, col)) in enumerate(lines.items()):
            k = int(np.searchsorted(tl, now))
            seen = pl[:k][np.isfinite(pl[:k]).all(1)] * sc
            if len(seen) > 1:
                cv2.polylines(img, [np.int32(seen * 16)], False, col, lw, cv2.LINE_AA, 4)
                cv2.circle(img, tuple(np.int32(seen[-1])), 3 * lw, col, -1, cv2.LINE_AA)
                heads[name] = k
            cv2.putText(img, name, (20, 40 + 36 * y), cv2.FONT_HERSHEY_SIMPLEX, 1.0, col, 2, cv2.LINE_AA)
        if "estimate" in heads and "video truth" in heads:                           # estimator error, live
            ke, kt = min(heads["estimate"], len(te)) - 1, min(heads["video truth"], len(tr)) - 1
            d = np.hypot(*(floor(tel[X_CM], tel[Y_CM])[ke] - (t0 + tr[kt, 1:3])))
            cv2.putText(img, f"estimate error {100 * d:.0f} cm", (20, 40 + 36 * 3), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                        (255, 255, 255), 2, cv2.LINE_AA)
        w.write(img)
        n += 1
    w.release()
    return n


def _plot(t, truth, est, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(12, 5))
    ax[0].plot(truth[:, 0], truth[:, 1], label="video truth")
    ax[0].plot(est[:, 0], est[:, 1], label="estimator", alpha=0.8)
    ax[0].set_aspect("equal"); ax[0].set_xlabel("x m"); ax[0].set_ylabel("y m"); ax[0].legend(); ax[0].grid(alpha=.3)
    e = est - truth
    ax[1].plot(t, e[:, 0] * 100, label="err x"); ax[1].plot(t, e[:, 1] * 100, label="err y")
    ax[1].set_xlabel("telemetry s"); ax[1].set_ylabel("est - truth, cm"); ax[1].legend(); ax[1].grid(alpha=.3)
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


# ---------------------------------------------------------------- CLI helpers
def _grab(video, t_s):
    cv2 = _cv2()
    cap = cv2.VideoCapture(video)
    cap.set(cv2.CAP_PROP_POS_MSEC, t_s * 1000.0)
    ok, frame = cap.read()
    if not ok:
        raise SystemExit(f"no frame at {t_s} s")
    return frame


def _click(video, t_s, world, out):
    cv2 = _cv2()
    frame, pts = _grab(video, t_s), []

    def cb(ev, x, y, *_):
        if ev == cv2.EVENT_LBUTTONDOWN and len(pts) < len(world):
            pts.append([float(x), float(y)])
            cv2.circle(frame, (x, y), 6, (0, 255, 0), 2)
            cv2.putText(frame, str(len(pts)), (x + 8, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    cv2.namedWindow("marks", cv2.WINDOW_NORMAL)
    cv2.setMouseCallback("marks", cb)
    print("click the marks in this order (world m):", world, "- Esc when done")
    while len(pts) < len(world):
        cv2.imshow("marks", frame)
        if cv2.waitKey(20) == 27:
            break
    cv2.destroyAllWindows()
    if len(pts) != len(world):
        raise SystemExit(f"got {len(pts)} clicks for {len(world)} marks")
    Path(out).write_text(json.dumps({"world": world, "pixel": pts}, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("calib"); c.add_argument("video"); c.add_argument("--board", required=True)
    c.add_argument("--square", type=float, required=True); c.add_argument("--step", type=int, default=10)
    c.add_argument("--until", type=float, help="s; use only the lens part of a clip that then films the roam")
    c.add_argument("--max-frames", type=int, default=45); c.add_argument("--out", required=True)
    g = sub.add_parser("board"); g.add_argument("--squares", default="13x7"); g.add_argument("--square-px", type=int, default=140)
    g.add_argument("--margin-px", type=int, default=50); g.add_argument("--out", required=True)
    k = sub.add_parser("click"); k.add_argument("video"); k.add_argument("--t", type=float, default=1.0)
    k.add_argument("--world", required=True, help='"x,y x,y ..." metres'); k.add_argument("--out", required=True)
    b = sub.add_parser("floor"); b.add_argument("video"); b.add_argument("--board", required=True)
    b.add_argument("--square", type=float, required=True); b.add_argument("--out", required=True)
    b.add_argument("--roi", help="x0,y0,x1,y1 px search box (keep wall boards out)")
    f = sub.add_parser("frame"); f.add_argument("video"); f.add_argument("--t", type=float, default=1.0)
    f.add_argument("--out", required=True)
    r = sub.add_parser("run"); r.add_argument("video"); r.add_argument("--cam", required=True)
    r.add_argument("--marks", required=True); r.add_argument("--csv", required=True); r.add_argument("--out", required=True)
    r.add_argument("--guard-offset", type=float, default=0.0, help="guard plane above the height sensor, m")
    r.add_argument("--align-s", type=float, default=None, help="fit window after take-off, s (default: whole flight)")
    r.add_argument("--sync", default="takeoff", help="takeoff | speed | seconds (telemetry t = video t + sync)"); r.add_argument("--stride", type=int, default=1)
    for p in (f, r):
        p.add_argument("--hsv-lo", default=",".join(map(str, HSV_LO)))
        p.add_argument("--hsv-hi", default=",".join(map(str, HSV_HI)))
        p.add_argument("--min-area", type=int, default=MIN_AREA)
        p.add_argument("--static-t", default="", help='"5,30": ignore guard-colour pixels present at all these times (s)')
    w = sub.add_parser("walls"); w.add_argument("video"); w.add_argument("--cam", required=True)
    w.add_argument("--marks", required=True); w.add_argument("--t", type=float, default=1.0)
    w.add_argument("--qr-side", type=float, default=0.19); w.add_argument("--board", default="8x5")
    w.add_argument("--square", type=float, default=0.026); w.add_argument("--roi", help="x0,y0,x1,y1 px for wall boards")
    w.add_argument("--out", required=True)
    o = sub.add_parser("overlay"); o.add_argument("video"); o.add_argument("--cam", required=True)
    o.add_argument("--marks", required=True); o.add_argument("--csv", required=True)
    o.add_argument("--run", required=True, help="output folder of `run`"); o.add_argument("--out", required=True)
    o.add_argument("--width", type=int, default=1920)
    s = sub.add_parser("survey"); s.add_argument("--cam", required=True); s.add_argument("--marks", required=True)
    s.add_argument("--cam-tape", required=True, help="x,y,z lens in the drone frame (on the pad, facing forward), m")
    s.add_argument("--ref", action="append", default=[], help='"u,v:x,y" floor pixel and its taped drone-frame xy')
    s.add_argument("--run", help="output folder of `run`: also convert its landing offset, write survey.json")
    a = ap.parse_args(argv)
    if a.cmd == "board":
        cols, rows = (int(v) for v in a.squares.lower().split("x"))
        _cv2().imwrite(a.out, board_image((cols, rows), a.square_px, a.margin_px))
        print(f"wrote {a.out}: {cols}x{rows} squares, {CHARUCO_DICT}; show it full-screen (or print it flat) and measure "
              f"one square in metres for --square (scale only: the lens model does not depend on it)")
    elif a.cmd == "calib":
        charuco = a.board.lower().startswith("charuco:")
        cols, rows = (int(v) for v in a.board.lower().removeprefix("charuco:").split("x"))
        cam = calibrate(a.video, (cols, rows), a.square, a.step, a.until, a.max_frames, charuco)
        Path(a.out).write_text(json.dumps(cam, indent=2))
        sp = cam["spread_cm"]
        print(f"rms {cam['rms_px']:.3f} px from {cam['frames']} of {cam['frames_found']} frames, corner coverage "
              f"{cam['coverage_6x6']:.0%} -> {a.out}")
        if sp:   # PROPOSED target: max < 2 cm keeps the lens model well under the guard-blob noise
            print(f"lens spread at {sp['at_m']:.0f} m: rms {sp['rms']:.1f} cm, max {sp['max']:.1f} cm (target max < 2 cm)")
    elif a.cmd == "floor":
        cols, rows = (int(v) for v in a.board.lower().split("x"))
        m = floor_board(a.video, (cols, rows), a.square,
                        roi=tuple(int(v) for v in a.roi.split(",")) if a.roi else None)
        Path(a.out).write_text(json.dumps(m, indent=2))
        print(f"board in {m['frames']} frames, corner spread {m['corner_spread_px']} px -> {a.out}")
    elif a.cmd == "click":
        world = [[float(v) for v in p.split(",")] for p in a.world.split()]
        _click(a.video, a.t, world, a.out)
        print("wrote", a.out)
    elif a.cmd == "overlay":
        n = overlay(a.video, json.loads(Path(a.cam).read_text()), json.loads(Path(a.marks).read_text()), a.csv, a.run,
                    a.out, a.width)
        print(f"wrote {n} frames -> {a.out}")
    elif a.cmd == "survey":
        cam, marks = json.loads(Path(a.cam).read_text()), json.loads(Path(a.marks).read_text())
        R, t, _ = floor_pose(cam["K"], cam["dist"], marks["world"], marks["pixel"])
        refs = [tuple([float(v) for v in p.split(",")] for p in r.split(":")) for r in a.ref]
        sv = survey(cam["K"], cam["dist"], R, t, np.mean(marks["world"], axis=0),
                    [float(v) for v in a.cam_tape.split(",")], refs)
        if a.run:
            rep = json.loads((Path(a.run) / "report.json").read_text())
            if "landing_vs_start_spot_m" in rep:
                sv["landing_vs_start_drone_m"] = np.round(to_drone(rep["landing_vs_start_spot_m"], sv)[0], 3).tolist()
            (Path(a.run) / "survey.json").write_text(json.dumps(sv, indent=2))
        print(json.dumps(sv, indent=2))
    elif a.cmd == "walls":
        cv2, cam, marks = _cv2(), json.loads(Path(a.cam).read_text()), json.loads(Path(a.marks).read_text())
        R, t, _ = floor_pose(cam["K"], cam["dist"], marks["world"], marks["pixel"])
        frame = _grab(a.video, a.t)
        tg = wall_targets(frame, cam["K"], cam["dist"], R, t, a.qr_side, tuple(int(v) for v in a.board.lower().split("x")),
                          a.square, tuple(int(v) for v in a.roi.split(",")) if a.roi else None)
        rep = wall_report(tg)
        out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
        (out / "walls.json").write_text(json.dumps(rep, indent=2))
        colours = {0.8: (255, 0, 255), 0.9: (255, 160, 40), 1.0: (60, 220, 60), 1.1: (0, 215, 255), 1.2: (40, 40, 255)}
        for (key, s), uv in junction_lines(tg, cam["K"], cam["dist"], R, t).items():
            cv2.polylines(frame, [uv.astype(np.int32)], False, colours[s], 3 if s == 1.0 else 2)
            u, v = uv[len(uv) // 2]
            cv2.putText(frame, f"x{s:.1f}", (int(u) + 8, int(v) - 8), cv2.FONT_HERSHEY_SIMPLEX, 1.2, colours[s], 3)
        for d in tg["qr"] + tg["boards"]:
            cv2.circle(frame, tuple(int(v) for v in d["px"]), 14, (0, 0, 255), 3)
        cv2.imwrite(str(out / "walls.png"), frame)
        print(json.dumps(rep, indent=2))
    elif a.cmd == "frame":
        cv2 = _cv2()
        frame = _grab(a.video, a.t)
        lo, hi = tuple(map(int, a.hsv_lo.split(","))), tuple(map(int, a.hsv_hi.split(",")))
        st = [float(s) for s in a.static_t.split(",") if s]
        for u, v, _ in detect_guards(frame, lo, hi, a.min_area, static=static_mask(a.video, st, lo, hi) if st else None):
            cv2.circle(frame, (int(u), int(v)), 12, (255, 0, 255), 2)
        cv2.imwrite(a.out, frame)
        print("wrote", a.out)
    else:
        lo, hi = tuple(map(int, a.hsv_lo.split(","))), tuple(map(int, a.hsv_hi.split(",")))
        rep = run(a.video, json.loads(Path(a.cam).read_text()), json.loads(Path(a.marks).read_text()), a.csv, a.out,
                  a.guard_offset, a.align_s, a.stride, lo, hi, a.min_area,
                  tuple(float(s) for s in a.static_t.split(",") if s),
                  a.sync if a.sync in ("takeoff", "speed") else float(a.sync))
        print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
