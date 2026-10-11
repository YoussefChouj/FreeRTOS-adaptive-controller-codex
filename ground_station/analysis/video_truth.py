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
ARUCO_DICT = "DICT_4X4_50"                         # R4 markers: pad id0, top id1, loose floor/wall 2-5
X_CM, Y_CM, Z_M = "Ctrler.locxPID.FB", "Ctrler.locyPID.FB", "Ctrler.Z_posPID.FB"
X_SP, Y_SP = "Ctrler.locxPID.Des", "Ctrler.locyPID.Des"   # position setpoints, estimator frame (cm)
X_V, Y_V = "Ctrler.locxsPID.FB", "Ctrler.locysPID.FB"     # velocity estimate, estimator frame (cm/s)
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
def _spread_cm(objs, imgs, size, dist_m=4.0, flags=0, K0=None) -> dict | None:
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
        _, K, d, _, _ = cv2.calibrateCamera(objs[s::3], imgs[s::3], size, None if K0 is None else K0.copy(), None,
                                            flags=flags)
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


def aruco_detector(dict_name: str = ARUCO_DICT):
    a = _cv2().aruco
    p = a.DetectorParameters()
    p.cornerRefinementMethod = a.CORNER_REFINE_SUBPIX    # sub-pixel corners: the lens fit and the ~29 px top marker need them
    return a.ArucoDetector(a.getPredefinedDictionary(getattr(a, dict_name)), p)


def aruco_scan(video: str, step: int = 1, t0: float = 0.0, t1: float | None = None, dict_name: str = ARUCO_DICT,
               out: str | None = None) -> dict:
    """Corners of every ArUco marker in every `step`-th frame between t0 and t1 s (video time). Returns {"t": (n,) s,
    "c<id>": (n, 4, 2) px, NaN where that id was not seen}; `out` saves it as .npz (a scan is minutes of decoding,
    what follows is seconds)."""
    cv2 = _cv2()
    det, cap = aruco_detector(dict_name), cv2.VideoCapture(video)
    if t0:
        cap.set(cv2.CAP_PROP_POS_MSEC, t0 * 1000)
    ts, seen, i = [], {}, -1
    while cap.grab():
        i += 1
        t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
        if t1 is not None and t > t1:
            break
        if i % step:
            continue
        c, ids, _ = det.detectMarkers(cv2.cvtColor(cap.retrieve()[1], cv2.COLOR_BGR2GRAY))
        for cc, m in zip(c, ids.ravel() if ids is not None else []):
            seen.setdefault(int(m), {})[len(ts)] = cc.reshape(4, 2)
        ts.append(t)
    res = {"t": np.array(ts)}
    for m, d in sorted(seen.items()):
        a = np.full((len(ts), 4, 2), np.nan, np.float32)
        a[list(d)] = np.array(list(d.values()))
        res[f"c{m}"] = a
    if out:
        np.savez_compressed(out, **res)
    return res


def marker_obj(side: float) -> np.ndarray:
    """ArUco corner order (TL, TR, BR, BL) in the marker plane, centred (the SOLVEPNP_IPPE_SQUARE layout)."""
    h = side / 2
    return np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]], np.float32)


def marker_views(c, size, max_views: int = 150, min_px: float = 40.0, max_move_px: float = 2.0) -> np.ndarray:
    """Indices of sharp, diverse views of one moving marker: seen in both neighbouring scan frames and moved less than
    `max_move_px` per corner to each (a hand-waved marker blurs), side >= `min_px`, then farthest-point sampled over
    image position and the two foreshortening ratios (opposite-edge length differences = tilt), so the 4-point views
    cover the frame and the tilts that fix the focal length."""
    ok = ~np.isnan(c[:, 0, 0])
    move = np.full(len(c), np.inf)
    both = ok[1:-1] & ok[:-2] & ok[2:]
    m = np.maximum(np.abs(c[1:-1] - c[:-2]).max(axis=(1, 2)), np.abs(c[1:-1] - c[2:]).max(axis=(1, 2)))
    move[1:-1][both] = m[both]
    e = np.linalg.norm(np.roll(c, -1, axis=1) - c, axis=2)                  # edges TL-TR, TR-BR, BR-BL, BL-TL
    side = np.nanmean(e, axis=1)
    cand = np.flatnonzero((move < max_move_px) & (side >= min_px))
    if len(cand) == 0:
        return cand
    ctr = c[cand].mean(axis=1) / np.asarray(size, float)
    tilt = np.c_[(e[cand, 0] - e[cand, 2]) / (e[cand, 0] + e[cand, 2]), (e[cand, 1] - e[cand, 3]) / (e[cand, 1] + e[cand, 3])]
    f = np.c_[ctr, tilt * 4.0]                                              # 0.25 foreshortening ~ a frame width apart
    pick, d = [int(np.argmax(side[cand]))], np.full(len(cand), np.inf)
    while len(pick) < min(max_views, len(cand)):
        d = np.minimum(d, np.linalg.norm(f - f[pick[-1]], axis=1))
        pick.append(int(np.argmax(d)))
    return cand[np.sort(pick)]


def marker_calibrate(scan: dict, mid: int, side: float, size, f0: float = 1000.0, max_views: int = 150,
                     fix_k3: bool = True, **kw) -> dict:
    """Lens model from one hand-waved square marker (4 corners per view). k3 fixed by default: 4-point views cannot
    hold the 6th-order term. Views with a reprojection error > 3x the median are dropped once and the fit repeated."""
    cv2 = _cv2()
    c = scan[f"c{mid}"]
    keep = marker_views(c, size, max_views, **kw)
    if len(keep) < 10:
        raise SystemExit(f"only {len(keep)} sharp views of id{mid}; need >= 10")
    obj = marker_obj(side)
    K0 = np.array([[f0, 0, size[0] / 2], [0, f0, size[1] / 2], [0, 0, 1]], float)
    flags = cv2.CALIB_USE_INTRINSIC_GUESS | (cv2.CALIB_FIX_K3 if fix_k3 else 0)
    for _ in range(2):
        objs, imgs = [obj] * len(keep), [c[k].astype(np.float32).reshape(-1, 1, 2) for k in keep]
        rms, K, dist, rv, tv = cv2.calibrateCamera(objs, imgs, tuple(size), K0.copy(), None, flags=flags)
        per = np.array([np.sqrt(np.mean(np.sum((cv2.projectPoints(obj, r, t, K, dist)[0] - im) ** 2, axis=2)))
                        for im, r, t in zip(imgs, rv, tv)])
        good = per <= 3 * np.median(per)
        if good.all():
            break
        keep = keep[good]
    pts = np.concatenate(imgs).reshape(-1, 2) / np.asarray(size, float) * 6
    cells = len({(int(u), int(v)) for u, v in pts})
    return {"K": K.tolist(), "dist": dist.ravel().tolist(), "rms_px": float(rms), "frames": len(imgs),
            "size": list(size), "worst_frame_px": float(per.max()), "coverage_6x6": cells / 36,
            "fx_over_fy": float(K[0, 0] / K[1, 1]), "marker_id": mid, "marker_side_m": side,
            "view_t_s": scan["t"][keep].round(3).tolist(),
            "spread_cm": _spread_cm(objs, imgs, size, flags=flags, K0=K0)}


def marker_pose(K, dist, corners, side: float):
    """R, t (marker -> camera) of one square marker, IPPE_SQUARE (the planar-square solver)."""
    cv2 = _cv2()
    ok, rv, tv = cv2.solvePnP(marker_obj(side), np.asarray(corners, np.float32).reshape(-1, 1, 2),
                              np.asarray(K, float), np.asarray(dist, float), flags=cv2.SOLVEPNP_IPPE_SQUARE)
    return cv2.Rodrigues(rv)[0], tv.ravel()


def static_corners(c):
    """Median corners of a marker over the frames it was seen, and the median absolute deviation of its corners (px):
    a static marker shows detector jitter only; a large MAD means it moved or was bumped."""
    ok = ~np.isnan(c[:, 0, 0])
    if not ok.any():
        return None, np.inf
    med = np.median(c[ok], axis=0)
    return med, float(np.median(np.abs(c[ok] - med)))


def static_check(scan: dict, cam: dict, sides: dict, origin: int = 0, frames=None) -> dict:
    """Cross-check a lens model on the markers that did not move (optionally only scan `frames`): the origin marker
    fixes the floor plane; every other static marker's corners are cast onto that plane and its sides measured (a flat
    floor marker must come back at its taped side), and its own PnP normal is compared with the floor normal (floor
    ~0 deg, the wall marker ~90 deg)."""
    K, dist = np.array(cam["K"]), np.array(cam["dist"])
    sel = slice(None) if frames is None else frames
    c0, mad0 = static_corners(scan[f"c{origin}"][sel])
    R0, t0 = marker_pose(K, dist, c0, sides[origin])
    out = {"origin": origin, "origin_mad_px": mad0, "origin_range_m": float(np.linalg.norm(t0)), "markers": {}}
    for key in scan:
        if not key.startswith("c") or int(key[1:]) == origin or int(key[1:]) not in sides:
            continue
        mid = int(key[1:])
        cm, mad = static_corners(scan[key][sel])
        if cm is None:
            continue
        R, t = marker_pose(K, dist, cm, sides[mid])
        xyz = pixel_to_plane(cm, K, dist, R0, t0, 0.0)                     # marker-0 plane, world -> camera = (R0, t0)
        e = np.linalg.norm(np.roll(xyz, -1, axis=0) - xyz, axis=1)
        out["markers"][mid] = {"seen": int((~np.isnan(scan[key][sel][:, 0, 0])).sum()), "mad_px": mad,
                               "normal_deg": float(np.degrees(np.arccos(min(1.0, abs(float(R0[:, 2] @ R[:, 2])))))),
                               "side_on_floor_cm": float(e.mean() * 100), "side_sd_cm": float(e.std() * 100),
                               "xy_m": xyz[:, :2].mean(0).round(3).tolist(), "pnp_range_m": float(np.linalg.norm(t))}
    return out



def floor_from_marker(scan: dict, cam: dict, origin: int = 0, side: float = 0.199):
    """Floor frame (world -> camera R, t) = the origin marker's median pose over every frame it was seen (the drone
    hides the pad marker while it sits on it), and its corner MAD (px)."""
    c0, mad = static_corners(scan[f"c{origin}"])
    if c0 is None:
        raise SystemExit(f"id{origin} never seen: no floor frame")
    R, t = marker_pose(np.array(cam["K"]), np.array(cam["dist"]), c0, side)
    return R, t, mad


def track_top(scan: dict, cam: dict, R, t, top: int = 1, side: float = 0.077) -> dict:
    """The top marker per scan frame in the floor frame (R, t world -> camera). Its corners are cast onto z = 0; the
    camera centre C is the homothety centre between parallel planes, so a cast side s0 puts the marker at the height
    h = Cz (1 - side / s0) ("scale height", noisy at ~30 px). Returns t (s video), uv (px centre), xy0 (the centre ray
    at z = 0), C (camera centre), h_scale (m) and heading (rad, the cast TL->TR edge, unwrapped), NaN where unseen."""
    K, dist = np.array(cam["K"]), np.array(cam["dist"])
    c = scan[f"c{top}"]
    ok = ~np.isnan(c[:, 0, 0])
    n = len(c)
    C = -R.T @ t
    xy0 = np.full((n, 2), np.nan); s0 = np.full(n, np.nan); hd = np.full(n, np.nan)
    p = pixel_to_plane(c[ok].reshape(-1, 2), K, dist, R, t, 0.0).reshape(-1, 4, 3)
    xy0[ok] = pixel_to_plane(c[ok].mean(axis=1), K, dist, R, t, 0.0)[:, :2]
    s0[ok] = np.linalg.norm(np.roll(p, -1, axis=1) - p, axis=2).mean(axis=1)
    e = p[:, 1, :2] - p[:, 0, :2]
    hd[ok] = np.arctan2(e[:, 1], e[:, 0])
    hd[ok] = np.unwrap(hd[ok])
    return {"t": scan["t"], "uv": c.mean(axis=1), "xy0": xy0, "C": C, "h_scale": C[2] * (1 - side / s0),
            "heading": hd, "seen": ok}


def top_at(track: dict, h) -> np.ndarray:
    """Top-marker centre (x, y) at height h (scalar or per frame): slide the z = 0 cast towards the camera centre."""
    C = track["C"]
    return C[:2] + (track["xy0"] - C[:2]) * ((C[2] - np.asarray(h, float)) / C[2])[..., None]


def sync_xcorr(t_a, a, t_b, b, max_lag: float = 30.0, dt: float = 0.01, min_overlap: float = 0.5, centre=0.0):
    """Offset o (t_b = t_a + o) maximising the normalised cross-correlation of two scalar series (NaN-free on their
    own grids), searched over centre +- max_lag. Returns (o, r_best, r_runner_up 1 s away or more)."""
    grid = np.arange(t_b[0], t_b[-1], dt)
    sb = np.interp(grid, t_b, b)
    lags = np.arange(centre - max_lag, centre + max_lag + dt / 2, dt)
    r = np.full(len(lags), -np.inf)
    for i, o in enumerate(lags):
        g = grid - o
        m = (g >= t_a[0]) & (g <= t_a[-1])
        if m.sum() < max(50, min_overlap * len(grid)):
            continue
        x, y = sb[m] - sb[m].mean(), np.interp(g[m], t_a, a)
        y = y - y.mean()
        r[i] = float(x @ y / (np.linalg.norm(x) * np.linalg.norm(y) + 1e-12))
    k = int(np.argmax(r))
    far = np.abs(lags - lags[k]) >= 1.0
    return float(lags[k]), float(r[k]), float(r[far].max()) if far.any() else -np.inf


def load_cam(path, size=None) -> dict:
    """Lens model from `calib`; size (w, h) of the video it is used on rescales K (same sensor crop and aspect assumed:
    a phone 1080p clip of a 4K-calibrated lens). Distortion is in normalised coordinates, so it does not change."""
    cam = json.loads(Path(path).read_text())
    if size and cam.get("size") and tuple(size) != tuple(cam["size"]):
        sx, sy = size[0] / cam["size"][0], size[1] / cam["size"][1]
        if abs(sx - sy) > 0.01:
            raise SystemExit(f"video {size} has another aspect than the lens model {cam['size']}: recalibrate")
        cam["K"] = (np.diag([sx, sy, 1.0]) @ np.array(cam["K"], float)).tolist()
        cam["size"] = list(size)
    return cam


def video_size(video: str) -> tuple[int, int]:
    cap = _cv2().VideoCapture(video)
    return int(cap.get(3)), int(cap.get(4))


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
                roi=None, zoom: float = 1.0) -> dict:
    """Floor marks from a checkerboard lying flat on the floor (the phone is fixed, so every detection sees the same
    corners): per-corner median pixel over the frames where the whole board is found. Origin = first inner corner.
    roi (x0, y0, x1, y1) px limits the search: boards of the same size on a wall would give a wall plane.
    zoom upscales the roi before the search: 2.6 cm squares are ~10 px at 1080p, too small for the detector."""
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
        gray = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        if zoom != 1.0:
            gray = cv2.resize(gray, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_CUBIC)
        ok, c = cv2.findChessboardCornersSB(gray, board, flags=flags)
        if not ok:
            continue
        c = (c.reshape(-1, 2) + 0.5) / zoom - 0.5 + (x0, y0)    # pixel-centre convention survives the resize
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
    return {"world": world.tolist(), "pixel": pixel.tolist(), "frames": len(found), "size": list(img.shape[1::-1]),
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


CORAL_LO, CORAL_HI = (0, 90, 120), (12, 255, 255)  # R4 guard rings (coral, b17); the hue also wraps from 172


def coral_mask(hsv, lo=CORAL_LO, hi=CORAL_HI, wrap: int = 172) -> np.ndarray:
    """Guard-ring pixels of an HSV image; a red-orange hue wraps past 180, so `wrap`..180 counts too."""
    cv2 = _cv2()
    m = cv2.inRange(hsv, lo, hi)
    if wrap:
        m |= cv2.inRange(hsv, (wrap, lo[1], lo[2]), (180, hi[1], hi[2]))
    return m > 0


def coral_static(video: str, n: int = 30, frac: float = 0.7, grow_px: int = 7) -> np.ndarray:
    """Guard-coloured scenery: pixels coral in >= `frac` of n frames spread over the video, grown by grow_px."""
    cv2 = _cv2()
    cap = cv2.VideoCapture(video)
    dur = cap.get(cv2.CAP_PROP_FRAME_COUNT) / (cap.get(cv2.CAP_PROP_FPS) or 30.0)
    acc, k = None, 0
    for ts in np.linspace(0.02, 0.95, n) * dur:
        cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000)
        ok, f = cap.read()
        if not ok:
            continue
        m = coral_mask(cv2.cvtColor(f, cv2.COLOR_BGR2HSV)).astype(np.uint16)
        acc, k = (m if acc is None else acc + m), k + 1
    st = (acc >= frac * k).astype(np.uint8)
    return cv2.dilate(st, np.ones((grow_px, grow_px), np.uint8)) > 0


def track_coral(video: str, start_uv, static=None, roi: int = 140, min_px: int = 40, step: int = 1, out=None) -> dict:
    """Drone pixel per frame from its guard rings: coral pixels (minus `static` scenery) in a +-roi px box around
    the last fix, their median (u, v) is the fix (robust to a ring the view hides; with 3 guards it sits off the
    hub). A lost frame (< min_px pixels) is NaN and the box grows 1.5x per frame until the drone is found."""
    cv2 = _cv2()
    cap = cv2.VideoCapture(video)
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    ts, uv, npx, i = [], [], [], -1
    c, r = np.asarray(start_uv, float), float(roi)
    while cap.grab():
        i += 1
        if i % step:
            continue
        ts.append(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000)
        u0, v0 = int(max(0, c[0] - r)), int(max(0, c[1] - r))
        u1, v1 = int(min(W, c[0] + r)), int(min(H, c[1] + r))
        m = coral_mask(cv2.cvtColor(cap.retrieve()[1][v0:v1, u0:u1], cv2.COLOR_BGR2HSV))
        if static is not None:
            m &= ~static[v0:v1, u0:u1]
        vv, uu = np.nonzero(m)
        npx.append(len(uu))
        if len(uu) >= min_px:
            c, r = np.array([np.median(uu) + u0, np.median(vv) + v0]), float(roi)
            uv.append(c.copy())
        else:
            uv.append((np.nan, np.nan))
            r = min(r * 1.5, float(max(W, H)))
    res = {"t": np.array(ts), "uv": np.array(uv, float), "npx": np.array(npx)}
    if out:
        np.savez_compressed(out, **res)
    return res


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


def slope(x, dt: float, half: int) -> np.ndarray:
    """Zero-phase least-squares slope over +-half samples (Savitzky-Golay first derivative); edges NaN."""
    k = np.arange(-half, half + 1, dtype=float)
    v = np.convolve(x, k[::-1] / (k @ k * dt), mode="same")
    v[:half] = v[len(v) - half:] = np.nan
    return v


def fit_2d(a, b) -> dict:
    """truth a ~ k * est b as complex numbers: rotation, scale and rotation-free coherence (1 = same shape)."""
    za, zb = a @ np.array([1, 1j]), b @ np.array([1, 1j])
    k, n = np.vdot(zb, za) / np.vdot(zb, zb), np.sqrt(np.vdot(za, za).real * np.vdot(zb, zb).real)
    return {"rot_deg": round(float(np.degrees(np.angle(k))), 1), "scale_2d": round(float(abs(k)), 3),
            "coherence": round(float(abs(np.vdot(zb, za)) / n), 3)}


def velocity_fit(t, truth_xy, est_v, est_xy=None, lags=np.arange(-0.2, 0.61, 0.02), dt=0.02, half=10,
                 band_s=2.5, scan_s=2.0) -> dict:
    """Truth velocity (zero-phase slope of the video track) against the estimator's velocity, same frame: lag of
    the estimate (positive = late, picked by rotation-free coherence), per-axis scale and correlation, the 2-D fit
    truth ~ k * est, and that fit split into slow drift (band_s moving average) and fast wobble (the rest).
    Only a flight driven by the setpoint (steps) or an open-loop carry grades the estimator. In a hold the loop is
    driven by estimator error n: est = S n, truth = -T n, so truth ~ -L est (L the loop gain) and the fit reads
    ~180 deg with |k| ~ |L| whatever the estimator; err_rms_mps (raw est - truth) is the only fair number there.
    In a driven test, slow right but fast reversed points at the flow's gyro (tilt-rate) compensation, not the frame.
    est_xy: also report the slope of the estimated position against est_v (a units and lag self-check).
    sync_scan: [lag, coherence, rot_deg] every 0.1 s over +-scan_s, to catch a video/telemetry sync error (a peak
    far outside the physical lag range, or rot flipping with lag, means the clocks are off, not the estimator)."""
    g = np.arange(t[0], t[-1], dt)
    tv = np.c_[[slope(np.interp(g, t, truth_xy[:, i]), dt, half) for i in (0, 1)]].T

    def at(lag):
        ev = np.c_[[np.interp(g + lag, t, est_v[:, i], left=np.nan, right=np.nan) for i in (0, 1)]].T
        ok = np.isfinite(tv).all(1) & np.isfinite(ev).all(1)
        return fit_2d(tv[ok], ev[ok]), tv[ok], ev[ok]
    best = None
    for lag in lags:
        f, a, b = at(lag)
        if best is None or f["coherence"] > best[0]["coherence"]:
            best = (f, float(lag), a, b)
    f, lag, a, b = best
    s = [float(a[:, i] @ b[:, i] / (b[:, i] @ b[:, i])) for i in (0, 1)]
    c = [float(np.corrcoef(a[:, i], b[:, i])[0, 1]) for i in (0, 1)]
    m = int(round(band_s / dt / 2))
    lo = lambda x: np.c_[[np.convolve(x[:, i], np.ones(2 * m + 1) / (2 * m + 1), mode="same") for i in (0, 1)]].T[m:-m]
    out = {"lag_s": round(lag, 2), "scale_x": round(s[0], 3), "scale_y": round(s[1], 3),
           "corr_x": round(c[0], 3), "corr_y": round(c[1], 3), **f,
           "slow": fit_2d(lo(a), lo(b)), "fast": fit_2d(a[m:-m] - lo(a), b[m:-m] - lo(b)),
           "truth_v_rms_mps": round(float(np.sqrt(np.mean(np.sum(a ** 2, 1)))), 3),
           "resid_rms_mps": round(float(np.sqrt(np.mean(np.sum((a - b * s) ** 2, 1)))), 3),
           "err_rms_mps": round(float(np.sqrt(np.mean(np.sum((b - a) ** 2, 1)))), 3)}
    if scan_s:
        out["sync_scan"] = [[round(float(L), 1), (f := at(L)[0])["coherence"], f["rot_deg"]]
                            for L in np.arange(-scan_s, scan_s + 1e-9, 0.1)]
    if est_xy is not None:
        dv = np.c_[[slope(np.interp(g, t, est_xy[:, i]), dt, half) for i in (0, 1)]].T
        ev = np.c_[[np.interp(g, t, est_v[:, i]) for i in (0, 1)]].T
        ok = np.isfinite(dv).all(1)
        out["est_pos_slope_over_v"] = round(float(np.sum(dv[ok] * ev[ok]) / np.sum(ev[ok] ** 2)), 3)
    return out


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


def read_slots(stem: str, slots=(0, 1, 2, 3)) -> dict:
    """stream_log slot CSVs `<stem>.slot<k>.csv` (R4 presets): {k: {"t": s on the drone clock (t_src_ms / 1000),
    column: array}}. Every slot shares that clock, so slots at different rates line up by "t"."""
    import pandas as pd
    out = {}
    for k in slots:
        p = Path(f"{stem}.slot{k}.csv")
        if not p.exists():
            continue
        df = pd.read_csv(p)
        d = {c: df[c].to_numpy(float) for c in df.columns}
        d["t"] = d["t_src_ms"] / 1000.0
        out[k] = d
    return out


# ---------------------------------------------------------------- run
def flight_window(fly, armed, h, lift: float = 0.3):
    """Flight mask, armed mask, take-off and disarm sample indices. A disarmed hand carry has no flight phase: the
    samples held more than `lift` m above the pad stand in for both (sync it with sync="speed")."""
    if not fly.any():
        fly = armed = h > lift
    return fly, armed, int(np.argmax(fly)), int(len(armed) - 1 - np.argmax(armed[::-1]))


def run(video, cam, marks, csv_path, out, guard_offset=0.0, align_s=None, stride=1, lo=HSV_LO, hi=HSV_HI,
        min_area=MIN_AREA, static_t=(), sync="takeoff", survey=None) -> dict:
    """sync: "takeoff" (lift-off in both clocks; video and campaign started by hand), "speed" (speed
    cross-correlation), "audio" (motor spool-up sound vs the motor command rise) or a number of seconds
    (telemetry time = video time + sync).
    align_s None fits the estimator rotation over the whole flight (a hover-only window is ill-conditioned).
    survey (a survey() dict): compare in the drone frame at tape scale with that fixed board -> drone map, the raw
    estimate against it; the free fit then only reports the estimator's own yaw offset (align_rot_deg)."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    K, dist = cam["K"], cam["dist"]
    R, t, reproj = floor_pose(K, dist, marks["world"], marks["pixel"])
    tel = read_livewatch(csv_path, [X_CM, Y_CM, Z_M, PHASE, ARM])
    te = tel["t"] - tel["t"][0]
    est = np.c_[tel[X_CM], tel[Y_CM]] / 100.0
    h_tel = np.nan_to_num(tel[Z_M])
    fly, armed, i_to, i_end = flight_window(tel[PHASE] == 1, tel[ARM] > 0, h_tel)

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
    elif sync == "audio":                                # motor sound vs motor command (guard_pose.audio_offset)
        from ground_station.analysis.guard_pose import audio_offset
        off = audio_offset(video, csv_path, tel["t"][0])
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
    A = np.eye(2) if survey is None else to_drone(np.eye(2), survey).T   # board -> compare frame (drone, tape m)
    t_c = t_rel @ A.T
    qf, refl = align(e_rel, t_c, max(n_fit, 10))
    q = qf if survey is None else np.linalg.inv(A)     # est -> board, for truth.csv and `overlay`
    e_al = e_rel @ q.T
    e_c = e_al @ A.T                                   # the raw estimate with a survey, e_rel @ qf.T without
    err = e_c - t_c
    e_f = e_rel @ qf.T                                 # est after its own best yaw fit
    g = np.arange(te[i_to], te[i_end], 0.2)            # path on a 0.2 s grid: per-frame jitter would inflate it
    def path(xy_):
        return float(np.sum(np.hypot(np.diff(np.interp(g, te[seg], xy_[:, 0])), np.diff(np.interp(g, te[seg], xy_[:, 1])))))
    path_t, path_e = path(t_c), path(e_c)
    try:                                               # velocity estimate: older logs lack it
        vl = read_livewatch(csv_path, [X_V, Y_V])
        ev = np.c_[[np.interp(te, vl["t"] - tel["t"][0], vl[n]) for n in (X_V, Y_V)]].T / 100.0 @ (A @ q).T
        vel = velocity_fit(te[seg], t_c, ev[seg], e_c)
    except SystemExit:
        vel = None
    rep = {
        "floor_reproj_px": round(reproj, 2),
        "floor_noise_err_1p5m_m": round(pose_noise(K, dist, marks["world"], marks["pixel"],
                                                   marks.get("sigma_px", 1.0), h_med + guard_offset), 4),
        "sync_offset_s": round(off, 3), "frames": int(len(tv)),
        "frames_tracked": int(ok.sum()), "spikes_replaced": n_spikes,
        "landing_vs_start_spot_m": [round(float(v), 3) for v in (*land, np.hypot(*land))],
        "est_landing_vs_start_m": round(float(np.hypot(*(est[i_end] - est[i_to]))), 3), "align_rot_deg": round(float(np.degrees(np.arctan2(qf[1, 0], qf[0, 0]))), 1),
        "align_reflection": refl, "flight_s": round(float(te[i_end] - te[i_to]), 2),
        "compare_frame": "board, free fit" if survey is None else "drone (survey), tape scale",
        **({} if survey is None else {"landing_vs_start_drone_m": [round(float(v), 3) for v in (*(A @ land), np.hypot(*(A @ land)))]}),
        # with a survey: the same numbers once the estimator's own yaw offset (align_rot_deg) is taken out
        **({} if survey is None else {"after_yaw_fit": {
            "scale_truth_over_est": round(float(np.nansum(t_c * e_f) / np.nansum(e_f * e_f)), 4),
            "err_rms_m": round(float(np.sqrt(np.nanmean(np.sum((e_f - t_c) ** 2, axis=1)))), 3)}}),
        **({} if vel is None else {"velocity": vel}),
        "truth_at_disarm_m": [round(float(v), 3) for v in t_c[-1]],
        "est_at_disarm_m": [round(float(v), 3) for v in e_c[-1]],
        "err_at_disarm_m": [round(float(v), 3) for v in err[-1]],
        "err_max_m": round(float(np.nanmax(np.hypot(*err.T))), 3),
        "err_rms_m": round(float(np.sqrt(np.nanmean(np.sum(err ** 2, axis=1)))), 3),
        "path_truth_m": round(path_t, 2), "path_est_m": round(path_e, 2),
        "sync": sync if isinstance(sync, str) else "manual",
        # least-squares truth ~ s * est over the flight
        "scale_truth_over_est": round(float(np.nansum(t_c * e_c) / np.nansum(e_c * e_c)), 4),
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
    _plot(te[seg], t_c, e_c, out / "truth.png")
    return rep


def overlay(video, cam, marks, csv_path, run_dir, out_mp4, width=1920) -> int:
    """Demo video: planned path (setpoints), the drone's own estimate and the video truth drawn on the roam video,
    a heading arrow for the plan (path tangent) and, when `guard_pose pose` has written pose.csv, the truth heading,
    plus an estimate-error-vs-time strip along the bottom."""
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
    plan_f, est_f = floor(tel[X_SP], tel[Y_SP]), floor(tel[X_CM], tel[Y_CM])
    err = np.hypot(*(np.c_[np.interp(tr[:, 0], te, est_f[:, 0]), np.interp(tr[:, 0], te, est_f[:, 1])]
                     - (t0 + tr[:, 1:3])).T)
    pose = np.genfromtxt(run_dir / "pose.csv", delimiter=",", names=True) if (run_dir / "pose.csv").exists() else None
    cap = cv2.VideoCapture(video)
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    sc = width / W
    size = (width, int(round(H * sc)))
    sh, emax = size[1] // 8, max(float(np.nanmax(err)) if len(err) else 0.0, 0.05)       # error strip, bottom 1/8
    sx = lambda tt: 20 + (np.asarray(tt) - fr["t_to"]) / max(fr["t_end"] - fr["t_to"], 1e-3) * (size[0] - 40)
    strip = np.c_[sx(tr[:, 0]), size[1] - 8 - (size[1] - 8 - (size[1] - sh)) * np.clip(err / emax, 0, 1)]

    def arrow(img, xy, zz, yaw, col):                 # 0.25 m heading arrow at floor-frame xy, height zz
        a = px(np.array([xy, xy + 0.25 * np.r_[np.cos(yaw), np.sin(yaw)]]), np.r_[zz, zz]) * sc
        if np.isfinite(a).all():
            cv2.arrowedLine(img, tuple(np.int32(a[0])), tuple(np.int32(a[1])), col, lw, cv2.LINE_AA, tipLength=0.3)

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
        if te[0] <= now <= te[-1] and len(te) > 8:                                   # plan heading: path tangent
            k = int(np.clip(np.searchsorted(te, now), 3, len(te) - 4))
            d = plan_f[k + 3] - plan_f[k - 3]
            if np.hypot(*d) > 1e-3:
                arrow(img, plan_f[k], z[k], np.arctan2(d[1], d[0]), (0, 215, 255))
        if pose is not None and pose["t_tel"][0] <= now <= pose["t_tel"][-1]:        # truth heading from PnP yaw
            g = {c: np.interp(now, pose["t_tel"], pose[c]) for c in ("x_m", "y_m", "z_m", "yaw_deg")}
            arrow(img, np.r_[g["x_m"], g["y_m"]], g["z_m"], np.radians(g["yaw_deg"]), (60, 220, 60))
        if len(strip) > 1:                                                           # error vs time strip
            roi = img[size[1] - sh:]
            roi[:] = (roi * 0.45).astype(img.dtype)
            cv2.polylines(img, [np.int32(strip[np.isfinite(strip).all(1)])], False, (255, 255, 255), max(1, lw // 2),
                          cv2.LINE_AA)
            cx = int(sx(now))
            cv2.line(img, (cx, size[1] - sh), (cx, size[1]), (0, 215, 255), max(1, lw // 2))
            cv2.putText(img, f"estimate error, max {100 * emax:.0f} cm", (20, size[1] - sh + 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
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


# ---------------------------------------------------------------- R4: guard-ring truth on the slot logs
R4_EST = {"earth": ("ano_of.earth_x", "ano_of.earth_y", 0.01),      # control path, cm
          "ekf": ("s_ekf_of.x[0]", "s_ekf_of.x[1]", 1.0)}            # EKF state, m


def ring_height(uv_pad, K, dist, R, t) -> float:
    """Height of the guard-ring centroid while the drone sits on the pad: the z at which its ray passes closest to
    the origin marker's vertical (the drone sits centred on the pad marker)."""
    p0 = pixel_to_plane(uv_pad, K, dist, R, t, 0.0)[0]
    d = pixel_to_plane(uv_pad, K, dist, R, t, 1.0)[0] - p0
    return float(-p0[:2] @ d[:2] / (d[:2] @ d[:2]))


def touchdown_video(t, xy, guess: float, search_s: float = 15.0, still_m: float = 0.03, hold_s: float = 2.0) -> float:
    """Video touchdown near `guess` (the telemetry landing mapped to video time): first frame after guess - search_s
    from which the floor cast stays within still_m of itself for hold_s (a hover moves the floor cast several
    times its real motion, so it is never that still in the air). NaN if none in the search window."""
    for k in np.flatnonzero((t >= guess - search_s) & (t <= guess + search_s)):
        w = (t >= t[k]) & (t <= t[k] + hold_s)
        if np.hypot(*(xy[w] - xy[k]).T).max() < still_m:
            return float(t[k])
    return float("nan")


def r4_truth(stem: str, video_dir: str, cam, refine_s: float = 2.0, coral_step: int = 1, rot_s: float = 60.0,
             out=None) -> dict:
    """Camera truth for one R4 flight. Floor = id0 median pose (aruco.npz); drone pixel = guard-ring track
    (coral.npz, made here if missing); the ring plane is its on-pad height plus the telemetry height rise, so a
    climb does not move the cast. Sync: take-off (video cast leaves the pad) vs telemetry lift-off, refined +-refine_s
    by speed cross-correlation against the earth estimate; the landing offset is reported as a clock-skew check.
    Each estimate (R4_EST) is taken relative to take-off and rotated (orthogonal Kabsch over the first rot_s of flight) onto the
    floor frame. Returns the per-telemetry-sample table and a summary; `out` writes <out>.csv and <out>.json."""
    cam = json.loads(Path(cam).read_text()) if not isinstance(cam, dict) else cam
    K, dist, vd = np.array(cam["K"]), np.array(cam["dist"]), Path(video_dir)
    scan = dict(np.load(vd / "aruco.npz"))
    R, tw, mad0 = floor_from_marker(scan, cam)
    cp = vd / "coral.npz"
    if not cp.exists():
        c0, _ = static_corners(scan["c0"])
        track_coral(str(vd / "cam.mkv"), c0.mean(0), coral_static(str(vd / "cam.mkv")), step=coral_step, out=str(cp))
    tr = np.load(cp)
    ok = ~np.isnan(tr["uv"][:, 0])
    t_v, uv = tr["t"][ok], tr["uv"][ok]

    S = read_slots(stem)
    s2 = S[2]
    te = s2["t"]
    armed = np.interp(te, S[0]["t"], S[0]["DroneStatus.ARM_Status"]) > 0.5
    z = np.nan_to_num(s2["Ctrler.Z_posPID.FB"])
    z0 = float(np.median(z[armed][:25]))
    up = armed & (z > z0 + 0.05)
    to_tel, ld_tel = float(te[np.argmax(up)]), float(te[len(up) - 1 - np.argmax(up[::-1])])

    g0 = pixel_to_plane(uv, K, dist, R, tw, 0.0)[:, :2]                     # floor cast: a climb moves it too
    to_vid = takeoff_video(t_v, g0)
    pad = t_v < to_vid - 1.0
    h = ring_height(np.median(uv[pad], axis=0), K, dist, R, tw)

    def cast(o):
        rise = np.clip(np.interp(t_v + o, te, z) - z0, 0.0, None)
        return pixel_to_plane(uv, K, dist, R, tw, h + rise)[:, :2]

    fly = (te >= to_tel) & (te <= ld_tel)
    ex, ey, es = R4_EST["earth"]
    earth = np.c_[s2[ex], s2[ey]] * es
    o0 = to_tel - to_vid
    d = sync_offset(t_v + o0, cast(o0), te[fly], earth[fly], max_lag=refine_s, dt=0.02)
    o, anchor = o0 + d, "takeoff"
    ld_vid = touchdown_video(t_v, g0, ld_tel - o)
    if np.isfinite(ld_vid) and abs(ld_tel - ld_vid - o) > 2.0:
        # R4 4-guard flights: Z_posPID.FB rises 9-12 s after the video lift-off, while the touchdown (= disarm)
        # agrees with a +-15 s speed cross-correlation, so the landing is the better anchor there
        o1 = ld_tel - ld_vid
        d = sync_offset(t_v + o1, cast(o1), te[fly], earth[fly], max_lag=refine_s, dt=0.02)
        o, anchor = o1 + d, "landing"
    xy = cast(o)
    xy -= np.median(xy[t_v < to_vid - 1.0], axis=0)                         # truth relative to the pad spot

    tt = te[fly]
    truth = np.c_[np.interp(tt - o, t_v, xy[:, 0]), np.interp(tt - o, t_v, xy[:, 1])]
    rows = {"t_tel": tt, "truth_x": truth[:, 0], "truth_y": truth[:, 1], "z": z[fly] - z0}
    summ = {"stem": stem, "h_ring_m": h, "id0_mad_px": mad0, "z0_m": z0, "takeoff_tel_s": to_tel,
            "takeoff_video_s": to_vid, "landing_tel_s": ld_tel, "landing_video_s": ld_vid,
            "offset_takeoff_s": o0, "offset_landing_s": ld_tel - ld_vid, "offset_refine_s": d, "offset_s": o,
            "sync_anchor": anchor if np.isfinite(ld_vid) else "takeoff-unverified", "rot_fit_s": rot_s,
            "flight_s": ld_tel - to_tel, "coral_lost_frac": float(1 - ok.mean()),
            "pad_jitter_cm": float(np.median(np.hypot(*(xy[pad] - np.median(xy[pad], 0)).T)) * 100),
            "truth_land_cm": float(np.hypot(*np.median(xy[t_v > ld_vid + 1.0], 0)) * 100) if (t_v > ld_vid + 1).any() else None,
            "est": {}}
    for name, (cx, cy, sc) in R4_EST.items():
        e = np.c_[s2[cx], s2[cy]][fly] * sc
        e = e - e[0]
        q, refl = align(e, truth, int(np.searchsorted(tt, tt[0] + rot_s)) if rot_s else len(e))
        ea = e @ q.T
        err = np.hypot(*(ea - truth).T)
        rows[f"{name}_x"], rows[f"{name}_y"] = ea[:, 0], ea[:, 1]
        summ["est"][name] = {"rot_deg": float(np.degrees(np.arctan2(q[1, 0], q[0, 0]))), "reflect": refl,
                             "rms_cm": float(np.sqrt(np.mean(err ** 2)) * 100), "p90_cm": float(np.percentile(err, 90) * 100),
                             "max_cm": float(err.max() * 100), "end_cm": float(err[-1] * 100)}
    if out:
        with open(f"{out}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(list(rows))
            w.writerows(np.c_[tuple(rows.values())].round(5).tolist())
        Path(f"{out}.json").write_text(json.dumps(summ, indent=2))
    return {"rows": rows, "summary": summ}


def hold_windows(t, des, min_s: float = 20.0) -> list:
    """Hands-off windows: runs of a constant position setpoint (slot3 locx/locy Des) lasting >= min_s."""
    ch = np.r_[True, np.any(np.diff(des, axis=0) != 0, axis=1)]
    k = np.flatnonzero(ch)
    ends = np.r_[k[1:] - 1, len(t) - 1]
    return [(float(t[a]), float(t[b])) for a, b in zip(k, ends) if t[b] - t[a] >= min_s]


def drift_score(name: str, cam: str = "docs/video-truth/cam_r4_lenswave_2026-10-11.json", win_s: float = 60.0,
                yaw_dps: float = 15.0, min_hold_s: float = 20.0, ests: dict | None = None, tag: str = "",
                smooth_s: float = 1.0) -> dict:
    """Score one truth4 flight. The estimate error e = est - truth (both from take-off, est rotated by truth4) is
    split by what the drone was doing: `yaw` (|yaw rate| > yaw_dps), `hold` (hands-off: constant Des, see
    hold_windows) and `move` (the rest); the net error each class adds is the sum of its increments, so the
    classes add up to the end error. Drift = |e(t+win_s) - e(t)| over sliding windows. Along = horizontal
    camera->pad direction (cast error from a height error lands here), across = its normal (height-free).
    `ests` scores other estimates ({name: (N, 2) m on the truth4 rows}, e.g. fuse4) into truth4_score<tag>.json."""
    vd = Path(f"logs/video_truth/{name}")
    rows = np.genfromtxt(vd / "truth4.csv", delimiter=",", names=True)
    summ = json.loads((vd / "truth4.json").read_text())
    camd = json.loads(Path(cam).read_text())
    R, tw, _ = floor_from_marker(dict(np.load(vd / "aruco.npz")), camd)
    c = -R.T @ tw
    u = -c[:2] / np.linalg.norm(c[:2])
    ax = np.c_[u, [-u[1], u[0]]]                                    # columns: along, across
    t, truth = rows["t_tel"], np.c_[rows["truth_x"], rows["truth_y"]]
    S = read_slots(f"logs/vofa/{name}")
    s0, s3 = S[0], S[3]
    yaw = np.unwrap(np.radians(s0["imu_data.yaw"]))
    k = max(1, int(round(0.5 / np.median(np.diff(s0["t"])))))
    rate = np.degrees(np.gradient(np.convolve(yaw, np.ones(k) / k, "same"), s0["t"]))
    yawing = np.abs(np.interp(t, s0["t"], rate)) > yaw_dps
    holds = [w for w in hold_windows(s3["t"], np.c_[s3["Ctrler.locxPID.Des"], s3["Ctrler.locyPID.Des"]], min_hold_s)
             if w[1] > t[0] and w[0] < t[-1]]
    hold = np.zeros(len(t), bool)
    for a, b in holds:
        hold |= (t >= a) & (t <= b)
    cls = np.where(yawing, "yaw", np.where(hold, "hold", "move"))
    dt = np.diff(t)
    out = {"name": name, "flight_s": summ["flight_s"], "offset_skew_s": summ["offset_s"] - summ["offset_landing_s"],
           "sync_anchor": summ.get("sync_anchor"),
           "view_dir": u.round(3).tolist(), "class_s": {q: float(dt[cls[1:] == q].sum()) for q in ("hold", "move", "yaw")},
           "truth_land_cm": summ["truth_land_cm"], "holds": [], "est": {}}
    for a, b in holds:
        w = (t >= a) & (t <= b)
        if w.sum() < 10:
            continue
        d = truth[w] - truth[w][0]
        out["holds"].append({"t0": a, "t1": b, "dur_s": float(t[w][-1] - t[w][0]), "truth_disp_cm": float(np.hypot(*d[-1]) * 100),
                             "truth_max_cm": float(np.hypot(*d.T).max() * 100),
                             "disp_along_across_cm": (d[-1] @ ax * 100).round(1).tolist()})
    for est, xy in (ests or {k: np.c_[rows[f"{k}_x"], rows[f"{k}_y"]] for k in R4_EST}).items():
        e = xy - truth
        de = np.diff(e, axis=0)
        net = {q: de[cls[1:] == q].sum(0) for q in ("hold", "move", "yaw")}
        j = np.searchsorted(t, t + win_s)
        i = np.flatnonzero(j < len(t))
        g = e[j[i]] - e[i]
        gm = np.hypot(*g.T) * 100
        ks = max(1, int(round(smooth_s / np.median(np.diff(t)))))      # drift of the 1 s mean: jitter is not drift
        es = np.c_[[np.convolve(e[:, a], np.ones(ks), "same") / np.convolve(np.ones(len(e)), np.ones(ks), "same") for a in (0, 1)]].T
        gs = np.hypot(*(es[j[i]] - es[i]).T) * 100
        o = {"rms_cm": float(np.sqrt(np.mean(np.sum(e ** 2, 1))) * 100), "rot_deg": summ["est"].get(est, {}).get("rot_deg"),
             "rms_along_across_cm": (np.sqrt(np.mean((e @ ax) ** 2, 0)) * 100).round(1).tolist(),
             "end_cm": float(np.hypot(*e[-1]) * 100),
             "net_cm": {q: float(np.hypot(*v) * 100) for q, v in net.items()},
             "net_rate_cm_s": {q: float(np.hypot(*v) * 100 / max(out["class_s"][q], 1e-9)) for q, v in net.items()}}
        if len(i):
            o.update({f"drift{int(win_s)}_med_cm": float(np.median(gm)), f"drift{int(win_s)}_p90_cm": float(np.percentile(gm, 90)),
                      f"drift{int(win_s)}_max_cm": float(gm.max()), "frac_le_10cm": float((gm <= 10).mean()),
                      "drift_across_p90_cm": float(np.percentile(np.abs(g @ ax[:, 1]) * 100, 90)),
                      f"drift{int(win_s)}s_p90_cm": float(np.percentile(gs, 90)), f"drift{int(win_s)}s_max_cm": float(gs.max()),
                      "frac_s_le_10cm": float((gs <= 10).mean()), "hf_rms_cm": float(np.sqrt(np.mean(np.sum((e - es) ** 2, 1))) * 100)})
        out["est"][est] = o
    (vd / f"truth4_score{tag}.json").write_text(json.dumps(out, indent=1))
    return out


# ---------------------------------------------------------------- R4: live camera fusion replay
# Ring and gate as API/ekf_of.h; slew, gain and the re-anchor rule are the PROPOSED closed loop
# (docs/video-truth/live_camera_fusion_design_2026-10-11.md): gain 0.1 at 10 fixes/s ~ a 1 s time constant.
EXT_POS = {"ring_n": 32, "ring_dt": 0.05, "gate_m": 0.40, "slew_m": 0.03, "gain": 0.1, "rej_n": 3}


def ext_pos_replay(t, est, fix_t, fix_xy, lat_s: float, p: dict = EXT_POS, arr_t=None) -> tuple[np.ndarray, dict]:
    """The firmware EXT_POS path closed: a fix of the position at fix_t reaches the drone lat_s later; its
    innovation e = z - (ring(fix_t) + c) is taken against the raw estimate buffered every ring_dt for ring_n samples
    (linear between samples, the newest after it) plus the running correction c. |e| > gate is dropped unless
    rej_n gated fixes came in a row, then accepted through the slew clamp until a fix is back inside the gate
    (slewed re-anchor). An accepted fix moves c by gain*e, at most slew. `arr_t` (sorted): per-fix arrival times
    instead of fix_t + lat_s, for a fix time the drone computes from a wrong delay. Returns est + c(t), fix counts."""
    rt, rx, c = [], [], np.zeros(2)
    n = {"ok": 0, "gated": 0, "old": 0, "reanchor": 0}
    out, rej, k = np.empty_like(est), 0, 0
    arr = np.asarray(fix_t) + lat_s if arr_t is None else np.asarray(arr_t)
    for i, ti in enumerate(t):
        if not rt or ti - rt[-1] >= p["ring_dt"] - 1e-9:
            rt.append(ti), rx.append(est[i].copy())
            if len(rt) > p["ring_n"]:
                rt.pop(0), rx.pop(0)
        while k < len(arr) and arr[k] <= ti:
            tm = fix_t[k]
            if tm < rt[0]:
                n["old"] += 1
            else:
                j = int(np.searchsorted(rt, tm, side="right"))
                pm = rx[-1] if j >= len(rt) else rx[j - 1] + (tm - rt[j - 1]) / (rt[j] - rt[j - 1]) * (rx[j] - rx[j - 1])
                e = fix_xy[k] - (pm + c)
                take = np.hypot(*e) <= p["gate_m"]
                if take:
                    rej, n["ok"] = 0, n["ok"] + 1
                else:
                    rej, n["gated"] = rej + 1, n["gated"] + 1
                    take = rej >= p["rej_n"]
                    n["reanchor"] += int(take)
                if take:
                    d = p["gain"] * e
                    c = c + d * min(1.0, p["slew_m"] / max(np.hypot(*d), 1e-12))
            k += 1
        out[i] = est[i] + c
    return out, n


def fuse4(name: str, rate_hz: float = 10.0, lat_s: float = 0.3, sd_m: float | None = None, drop: float | None = None,
          gain: float = EXT_POS["gain"], seed: int = 0) -> dict:
    """Replay the closed-loop camera fusion on one truth4 flight: fixes at rate_hz = truth + noise (default sd: the
    flight's on-pad jitter / 1.18, the Rayleigh median), a drop rate (default: the tracker's lost fraction), lat_s
    capture-to-drone delay, fed to ext_pos_replay on the earth estimate; earth and fused are scored by
    drift_score. The fixes come from the same camera as the truth, so this scores the fusion, not the camera."""
    vd = Path(f"logs/video_truth/{name}")
    rows = np.genfromtxt(vd / "truth4.csv", delimiter=",", names=True)
    summ = json.loads((vd / "truth4.json").read_text())
    t, truth, earth = rows["t_tel"], np.c_[rows["truth_x"], rows["truth_y"]], np.c_[rows["earth_x"], rows["earth_y"]]
    rng = np.random.default_rng(seed)
    sd = summ["pad_jitter_cm"] / 100 / 1.18 if sd_m is None else sd_m
    ft = np.arange(t[0], t[-1], 1.0 / rate_hz)
    ft = ft[rng.random(len(ft)) >= (summ["coral_lost_frac"] if drop is None else drop)]
    z = np.c_[np.interp(ft, t, truth[:, 0]), np.interp(ft, t, truth[:, 1])] + rng.normal(0.0, sd, (len(ft), 2))
    fused, n = ext_pos_replay(t, earth, ft, z, lat_s, dict(EXT_POS, gain=gain))
    r = drift_score(name, ests={"earth": earth, "fused": fused}, tag=f"_fuse_{int(lat_s * 1000)}ms")
    r["fuse"] = {"rate_hz": rate_hz, "lat_s": lat_s, "sd_cm": sd * 100, "gain": gain, "counts": n, "seed": seed}
    return r


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
    b.add_argument("--zoom", type=float, default=1.0, help="upscale the roi first (2-3 for 1080p video)")
    f = sub.add_parser("frame"); f.add_argument("video"); f.add_argument("--t", type=float, default=1.0)
    f.add_argument("--out", required=True)
    r = sub.add_parser("run"); r.add_argument("video"); r.add_argument("--cam", required=True)
    r.add_argument("--marks", required=True); r.add_argument("--csv", required=True); r.add_argument("--out", required=True)
    r.add_argument("--guard-offset", type=float, default=0.0, help="guard plane above the height sensor, m")
    r.add_argument("--align-s", type=float, default=None, help="fit window after take-off, s (default: whole flight)")
    r.add_argument("--sync", default="takeoff", help="takeoff | speed | audio | seconds (telemetry t = video t + sync)"); r.add_argument("--stride", type=int, default=1)
    r.add_argument("--survey", help="survey.json from `survey`: fixed board -> drone frame instead of the free fit")
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
    q = sub.add_parser("aruco", help="scan every marker's corners into an .npz")
    q.add_argument("video"); q.add_argument("--out", required=True); q.add_argument("--step", type=int, default=1)
    q.add_argument("--t0", type=float, default=0.0); q.add_argument("--t1", type=float)
    m = sub.add_parser("mcal", help="lens model from one waved marker in an `aruco` scan, cross-checked on the static ones")
    m.add_argument("scan"); m.add_argument("--id", type=int, required=True); m.add_argument("--side", type=float, required=True)
    m.add_argument("--size", default="1920x1080"); m.add_argument("--max-views", type=int, default=150)
    m.add_argument("--free-k3", action="store_true"); m.add_argument("--max-move-px", type=float, default=2.0)
    m.add_argument("--sides", default="0:0.199", help="static markers id:side_m,... for the cross-check (origin first)")
    m.add_argument("--out", required=True)
    r4 = sub.add_parser("truth4", help="R4 guard-ring truth for one flight: logs/vofa/<name> + logs/video_truth/<name>")
    r4.add_argument("name"); r4.add_argument("--cam", default="docs/video-truth/cam_r4_lenswave_2026-10-11.json")
    r4.add_argument("--refine", type=float, default=2.0)
    r4.add_argument("--coral-step", type=int, default=2, help="track every Nth frame (2 = 15 Hz at 30 fps)")
    r4.add_argument("--rot-s", type=float, default=60.0, help="fit the estimate->floor rotation on the first N s of flight "
                    "(0 = whole flight; a later estimate jump corrupts a whole-flight fit)")
    sc = sub.add_parser("score4", help="drift score of truth4 flights (hold/move/yaw split, 60 s windows)")
    sc.add_argument("names", nargs="+"); sc.add_argument("--win", type=float, default=60.0)
    fu = sub.add_parser("fuse4", help="replay the PROPOSED closed-loop camera fusion (EXT_POS) on truth4 flights")
    fu.add_argument("names", nargs="+"); fu.add_argument("--rate", type=float, default=10.0)
    fu.add_argument("--lat", type=float, nargs="+", default=[0.3], help="capture-to-drone delay(s), s")
    fu.add_argument("--gain", type=float, default=EXT_POS["gain"]); fu.add_argument("--seed", type=int, default=0)
    fu.add_argument("--sd-cm", type=float, default=None, help="fix noise sd per axis (default: on-pad jitter / 1.18)")
    a = ap.parse_args(argv)
    if a.cmd == "aruco":
        r = aruco_scan(a.video, a.step, a.t0, a.t1, out=a.out)
        print(f"{len(r['t'])} frames {r['t'][0]:.2f}-{r['t'][-1]:.2f} s -> {a.out}; seen: "
              + ", ".join(f"id{k[1:]} {int((~np.isnan(v[:, 0, 0])).sum())}" for k, v in r.items() if k != "t"))
    elif a.cmd == "score4":
        for n in a.names:
            r = drift_score(n, win_s=a.win); e = r["est"]["earth"]
            print(f"{n:30s} fly {r['flight_s']:5.0f} s skew {r['offset_skew_s']:+.2f} {(r['sync_anchor'] or '?')[:4]} hold {r['class_s']['hold']:4.0f} s "
                  f"land {r['truth_land_cm'] or float('nan'):5.1f} | earth rms {e['rms_cm']:5.1f} d60 med/p90/max "
                  f"{e.get('drift60_med_cm', float('nan')):5.1f}/{e.get('drift60_p90_cm', float('nan')):5.1f}/{e.get('drift60_max_cm', float('nan')):5.1f} "
                  f"rate hold/move/yaw " + "/".join(f"{e['net_rate_cm_s'][q]:.2f}" for q in ("hold", "move", "yaw")))
    elif a.cmd == "fuse4":
        for n in a.names:
            for lat in a.lat:
                r = fuse4(n, a.rate, lat, None if a.sd_cm is None else a.sd_cm / 100, gain=a.gain, seed=a.seed)
                print(f"{n:24s} lat {lat:.2f} sd {r['fuse']['sd_cm']:.1f} " + " | ".join(
                    f"{k} rms {e['rms_cm']:5.1f} d60 p90/max {e.get('drift60_p90_cm', float('nan')):5.1f}/"
                    f"{e.get('drift60_max_cm', float('nan')):5.1f} 1s-mean {e.get('drift60s_p90_cm', float('nan')):5.1f}/"
                    f"{e.get('drift60s_max_cm', float('nan')):5.1f} hf {e.get('hf_rms_cm', float('nan')):4.1f} "
                    f"hold {e['net_rate_cm_s']['hold']:.2f} cm/s"
                    for k, e in r["est"].items()) + f" | {r['fuse']['counts']}")
    elif a.cmd == "truth4":
        r = r4_truth(f"logs/vofa/{a.name}", f"logs/video_truth/{a.name}", a.cam, a.refine, a.coral_step, a.rot_s, out=f"logs/video_truth/{a.name}/truth4")
        print(json.dumps(r["summary"], indent=1))
    elif a.cmd == "mcal":
        scan, size = dict(np.load(a.scan)), tuple(int(v) for v in a.size.split("x"))
        cam = marker_calibrate(scan, a.id, a.side, size, max_views=a.max_views, fix_k3=not a.free_k3,
                               max_move_px=a.max_move_px)
        sides = {int(k): float(v) for k, v in (kv.split(":") for kv in a.sides.split(","))}
        cam["static_check"] = static_check(scan, cam, sides, origin=next(iter(sides)))
        Path(a.out).write_text(json.dumps(cam, indent=2))
        sp = cam["spread_cm"]
        print(f"id{a.id}: rms {cam['rms_px']:.3f} px, {cam['frames']} views, coverage {cam['coverage_6x6']:.0%}, "
              f"f {cam['K'][0][0]:.1f}/{cam['K'][1][1]:.1f}, c {cam['K'][0][2]:.1f},{cam['K'][1][2]:.1f}, "
              f"dist {[round(v, 4) for v in cam['dist']]}" + (f", spread@4m rms {sp['rms']:.1f} max {sp['max']:.1f} cm" if sp else ""))
        for mid, r in cam["static_check"]["markers"].items():
            print(f"  id{mid}: seen {r['seen']}, mad {r['mad_px']:.2f} px, normal {r['normal_deg']:.1f} deg, side on floor "
                  f"{r['side_on_floor_cm']:.2f}+-{r['side_sd_cm']:.2f} cm, xy {r['xy_m']}")
    elif a.cmd == "board":
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
                        roi=tuple(int(v) for v in a.roi.split(",")) if a.roi else None, zoom=a.zoom)
        Path(a.out).write_text(json.dumps(m, indent=2))
        print(f"board in {m['frames']} frames, corner spread {m['corner_spread_px']} px -> {a.out}")
    elif a.cmd == "click":
        world = [[float(v) for v in p.split(",")] for p in a.world.split()]
        _click(a.video, a.t, world, a.out)
        print("wrote", a.out)
    elif a.cmd == "overlay":
        n = overlay(a.video, load_cam(a.cam, video_size(a.video)), json.loads(Path(a.marks).read_text()), a.csv, a.run,
                    a.out, a.width)
        print(f"wrote {n} frames -> {a.out}")
    elif a.cmd == "survey":
        marks = json.loads(Path(a.marks).read_text())
        cam = load_cam(a.cam, marks.get("size"))
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
        cv2, marks = _cv2(), json.loads(Path(a.marks).read_text())
        cam = load_cam(a.cam, video_size(a.video))
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
        rep = run(a.video, load_cam(a.cam, video_size(a.video)), json.loads(Path(a.marks).read_text()), a.csv, a.out,
                  a.guard_offset, a.align_s, a.stride, lo, hi, a.min_area,
                  tuple(float(s) for s in a.static_t.split(",") if s),
                  a.sync if a.sync in ("takeoff", "speed", "audio") else float(a.sync),
                  json.loads(Path(a.survey).read_text()) if a.survey else None)
        print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
