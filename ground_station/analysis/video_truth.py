"""Video ground truth for optical-flow roams: a fixed phone films the drone, the orange prop guards give its floor xy.

    python -m ground_station.analysis.video_truth calib <board.mp4> --board 9x6 --square 0.025 --out cam.json
    python -m ground_station.analysis.video_truth floor <roam.mp4> --board 8x5 --square 0.026 --out marks.json
    python -m ground_station.analysis.video_truth click <roam.mp4> --world "0,0 1.5,0 0,1.5 1.5,1.5" --out marks.json
    python -m ground_station.analysis.video_truth frame <roam.mp4> --t 2.0 --out frame.png      # check the colour mask
    python -m ground_station.analysis.video_truth run <roam.mp4> --cam cam.json --marks marks.json \
        --csv logs/livewatch/<roam>.csv --out logs/video_truth/<roam>

Pipeline: lens model from a checkerboard video (calib) -> camera pose from a checkerboard lying on the floor (floor:
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
X_CM, Y_CM, Z_M = "Ctrler.locxPID.FB", "Ctrler.locyPID.FB", "Ctrler.Z_posPID.FB"
X_SP, Y_SP = "Ctrler.locxPID.Des", "Ctrler.locyPID.Des"   # position setpoints, estimator frame (cm)
PHASE, ARM = "flight_phase", "DroneStatus.ARM_Status"


def _cv2():
    import cv2
    return cv2


# ---------------------------------------------------------------- geometry
def calibrate(video: str, board: tuple[int, int], square: float, step: int = 10) -> dict:
    """Lens model from a checkerboard video: every `step`-th frame with a full board is used."""
    cv2 = _cv2()
    obj = np.zeros((board[0] * board[1], 3), np.float32)
    obj[:, :2] = np.mgrid[0:board[0], 0:board[1]].T.reshape(-1, 2) * square
    cap, objs, imgs, size, i = cv2.VideoCapture(video), [], [], None, 0
    while cap.grab():
        i += 1
        if i % step:
            continue
        g = cv2.cvtColor(cap.retrieve()[1], cv2.COLOR_BGR2GRAY)
        size = g.shape[::-1]
        found, c = cv2.findChessboardCornersSB(g, board)    # classic detector misses the board in 4K frames
        if found:                                          # a mirrored grid is a rigid flip of a plane: harmless
            objs.append(obj)
            imgs.append(c)
    if len(objs) < 5:
        raise SystemExit(f"only {len(objs)} frames with a full {board[0]}x{board[1]} board; need >= 5")
    rms, K, dist, rv, tv = cv2.calibrateCamera(objs, imgs, size, None, None)
    per = [float(np.sqrt(np.mean(np.sum((cv2.projectPoints(o, r, t, K, dist)[0] - c) ** 2, axis=2))))
           for o, c, r, t in zip(objs, imgs, rv, tv)]
    pts = np.concatenate(imgs).reshape(-1, 2) / size * 6
    cells = len({(int(u), int(v)) for u, v in pts})       # 6x6 image grid cells a corner landed in
    return {"K": K.tolist(), "dist": dist.ravel().tolist(), "rms_px": float(rms), "frames": len(objs),
            "size": list(size), "worst_frame_px": max(per), "coverage_6x6": cells / 36}


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


def floor_board(video: str, board: tuple[int, int], square: float, every_s: float = 0.5, max_frames: int = 60) -> dict:
    """Floor marks from a checkerboard lying flat on the floor (the phone is fixed, so every detection sees the same
    corners): per-corner median pixel over the frames where the whole board is found. Origin = first inner corner."""
    cv2 = _cv2()
    cap = cv2.VideoCapture(video)
    step = max(1, int(round(every_s * (cap.get(cv2.CAP_PROP_FPS) or 30.0))))
    flags = cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY
    ref, found, i = None, [], 0
    while len(found) < max_frames and cap.grab():
        i += 1
        if (i - 1) % step:
            continue
        ok, c = cv2.findChessboardCornersSB(cv2.cvtColor(cap.retrieve()[1], cv2.COLOR_BGR2GRAY), board, flags=flags)
        if not ok:
            continue
        c = c.reshape(-1, 2)
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


# ---------------------------------------------------------------- detection
def detect_guards(frame_bgr, lo=HSV_LO, hi=HSV_HI, min_area=MIN_AREA, keep=4) -> np.ndarray:
    """(u, v, area) of the `keep` largest orange blobs, largest first."""
    cv2 = _cv2()
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(lo), np.array(hi))
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


def track(video: str, lo=HSV_LO, hi=HSV_HI, min_area=MIN_AREA, stride: int = 1):
    """Video time (s) and guard pixels (list of arrays) per kept frame."""
    cv2 = _cv2()
    cap, ts, blobs, i = cv2.VideoCapture(video), [], [], 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % stride == 0:
            ts.append(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0)
            blobs.append(detect_guards(frame, lo, hi, min_area))
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
def run(video, cam, marks, csv_path, out, guard_offset=0.0, align_s=10.0, stride=1, lo=HSV_LO, hi=HSV_HI,
        min_area=MIN_AREA) -> dict:
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
        tv, blobs = track(video, lo, hi, min_area, stride)
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

    # pass 1: constant flight height -> sync; pass 2: synced per-frame height
    h_med = float(np.nanmedian(h_tel[fly]))
    xy1, ok1 = centres(np.full(len(tv), h_med))
    off = sync_offset(tv[ok1], xy1[ok1], te[fly], est[fly])
    xy, ok = centres(np.interp(tv + off, te, h_tel))
    tvs, xy = tv[ok] + off, xy[ok]

    # truth on the telemetry clock, take-off-relative; est rotated onto it
    tr = np.c_[np.interp(te, tvs, xy[:, 0]), np.interp(te, tvs, xy[:, 1])]
    seg = slice(i_to, i_end + 1)
    e_rel, t_rel = est[seg] - est[i_to], tr[seg] - tr[i_to]
    n_fit = int(np.searchsorted(te[seg], te[i_to] + align_s))
    q, refl = align(e_rel, t_rel, max(n_fit, 10))
    e_al = e_rel @ q.T
    err = e_al - t_rel
    path_t = float(np.nansum(np.hypot(*np.diff(t_rel, axis=0).T)))
    path_e = float(np.nansum(np.hypot(*np.diff(e_al, axis=0).T)))
    rep = {
        "floor_reproj_px": round(reproj, 2),
        "floor_noise_err_1p5m_m": round(pose_noise(K, dist, marks["world"], marks["pixel"],
                                                   marks.get("sigma_px", 1.0), h_med + guard_offset), 4),
        "sync_offset_s": round(off, 3), "frames": int(len(tv)),
        "frames_tracked": int(ok.sum()), "align_rot_deg": round(float(np.degrees(np.arctan2(q[1, 0], q[0, 0]))), 1),
        "align_reflection": refl, "flight_s": round(float(te[i_end] - te[i_to]), 2),
        "truth_at_disarm_m": [round(float(v), 3) for v in t_rel[-1]],
        "est_at_disarm_m": [round(float(v), 3) for v in e_al[-1]],
        "err_at_disarm_m": [round(float(v), 3) for v in err[-1]],
        "err_max_m": round(float(np.nanmax(np.hypot(*err.T))), 3),
        "err_rms_m": round(float(np.sqrt(np.nanmean(np.sum(err ** 2, axis=1)))), 3),
        "path_truth_m": round(path_t, 2), "path_est_m": round(path_e, 2),
        # least-squares truth ~ s * est over the flight; path lengths are inflated by tracking jitter
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
    c.add_argument("--out", required=True)
    k = sub.add_parser("click"); k.add_argument("video"); k.add_argument("--t", type=float, default=1.0)
    k.add_argument("--world", required=True, help='"x,y x,y ..." metres'); k.add_argument("--out", required=True)
    b = sub.add_parser("floor"); b.add_argument("video"); b.add_argument("--board", required=True)
    b.add_argument("--square", type=float, required=True); b.add_argument("--out", required=True)
    f = sub.add_parser("frame"); f.add_argument("video"); f.add_argument("--t", type=float, default=1.0)
    f.add_argument("--out", required=True)
    r = sub.add_parser("run"); r.add_argument("video"); r.add_argument("--cam", required=True)
    r.add_argument("--marks", required=True); r.add_argument("--csv", required=True); r.add_argument("--out", required=True)
    r.add_argument("--guard-offset", type=float, default=0.0, help="guard plane above the height sensor, m")
    r.add_argument("--align-s", type=float, default=10.0); r.add_argument("--stride", type=int, default=1)
    for p in (f, r):
        p.add_argument("--hsv-lo", default=",".join(map(str, HSV_LO)))
        p.add_argument("--hsv-hi", default=",".join(map(str, HSV_HI)))
        p.add_argument("--min-area", type=int, default=MIN_AREA)
    o = sub.add_parser("overlay"); o.add_argument("video"); o.add_argument("--cam", required=True)
    o.add_argument("--marks", required=True); o.add_argument("--csv", required=True)
    o.add_argument("--run", required=True, help="output folder of `run`"); o.add_argument("--out", required=True)
    o.add_argument("--width", type=int, default=1920)
    a = ap.parse_args(argv)
    if a.cmd == "calib":
        cols, rows = (int(v) for v in a.board.lower().split("x"))
        cam = calibrate(a.video, (cols, rows), a.square, a.step)
        Path(a.out).write_text(json.dumps(cam, indent=2))
        print(f"rms {cam['rms_px']:.3f} px from {cam['frames']} frames -> {a.out}")
    elif a.cmd == "floor":
        cols, rows = (int(v) for v in a.board.lower().split("x"))
        m = floor_board(a.video, (cols, rows), a.square)
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
    elif a.cmd == "frame":
        cv2 = _cv2()
        frame = _grab(a.video, a.t)
        lo, hi = tuple(map(int, a.hsv_lo.split(","))), tuple(map(int, a.hsv_hi.split(",")))
        for u, v, _ in detect_guards(frame, lo, hi, a.min_area):
            cv2.circle(frame, (int(u), int(v)), 12, (255, 0, 255), 2)
        cv2.imwrite(a.out, frame)
        print("wrote", a.out)
    else:
        lo, hi = tuple(map(int, a.hsv_lo.split(","))), tuple(map(int, a.hsv_hi.split(",")))
        rep = run(a.video, json.loads(Path(a.cam).read_text()), json.loads(Path(a.marks).read_text()), a.csv, a.out,
                  a.guard_offset, a.align_s, a.stride, lo, hi, a.min_area)
        print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
