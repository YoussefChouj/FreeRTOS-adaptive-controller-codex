"""Video truth, second stage: 6-DoF pose from the 4 guards, audio sync, RTS smoothing, TUM/ATE export, side-by-side.

Build order from docs/agent/video-truth-review-2026-10-10.md, items 1-5. `video_truth run` stays the first stage (floor
pose, sync, the plane track); this module adds what one odd-coloured guard makes possible:

    pose   one guard swapped for white breaks the 4-fold symmetry, so the 4 guard centres give a full PnP pose
           (SOLVEPNP_IPPE, Collins & Bartoli 2014: x, y, z, roll, pitch, yaw) with no telemetry height in it
    audio  the phone mic hears the motors spool up; lined up with the motor command rise in telemetry, it is a sync
           that needs no hardware (Argus does the same between cameras)
    rts    constant-acceleration Kalman filter + RTS smoother per axis on the video track only, so the truth stays
           independent of the IMU; it also gives truth velocity and yaw rate
    tum    TUM files (t x y z qx qy qz qw) for `evo_ape` / `evo_rpe`, plus a built-in Umeyama ATE and a 1 s RPE
    sbs    a side-by-side clip of two runs (PID vs vpNN) started at the same trajectory moment

Frames: world = the floor marks / board frame of `video_truth floor_pose` (z up); body = x nose, y left, z up.
OpenCV only (audio via the MSMF backend on Windows); no ffmpeg needed.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from ground_station.analysis import video_truth as vt

WHITE_S_MAX, WHITE_V_MIN = 60, 200      # HSV: low saturation, high value
UPRIGHT_COS = np.cos(np.radians(60.0))   # reject PnP branches tilted more than 60 deg (the drone is never that far over)
MOTORS = tuple(f"mymotor.motor{i}" for i in range(1, 5))
EST_YAW = "imu_data.yaw"


# ---------------------------------------------------------------- detection

def white_mask(frame_bgr, s_max=WHITE_S_MAX, v_min=WHITE_V_MIN):
    return vt.colour_mask(frame_bgr, (0, 0, v_min), (180, s_max, 255))


def white_static(video, times, s_max=WHITE_S_MAX, v_min=WHITE_V_MIN, grow_px=15):
    """White pixels present at every one of `times`: walls, lamps, paper. Same idea as video_truth.static_mask."""
    cv2 = vt._cv2()
    m = None
    for ts in times:
        c = white_mask(vt._grab(video, ts), s_max, v_min)
        m = c if m is None else m & c
    return cv2.dilate(m, np.ones((grow_px, grow_px), np.uint8))


def detect_frame(frame_bgr, lo=vt.HSV_LO, hi=vt.HSV_HI, min_area=vt.MIN_AREA, static=None, wstatic=None,
                 s_max=WHITE_S_MAX, v_min=WHITE_V_MIN, area_tol=4.0, merge_ratio=1.8):
    """(4, 3) guard pixels (u, v, area), the odd (white) one last, or None. The 3 largest orange blobs must be of
    similar area (a merged pair is about twice the size); the white blob is the one nearest their centroid whose
    area is within area_tol x of theirs and that lies within 2.5 x their spread."""
    cv2 = vt._cv2()
    org = vt.detect_guards(frame_bgr, lo, hi, min_area, keep=3, static=static)
    if len(org) < 3 or org[:, 2].max() > merge_ratio * org[:, 2].min():
        return None
    m = white_mask(frame_bgr, s_max, v_min)
    if wstatic is not None:
        m &= ~wstatic
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, _, stats, cent = cv2.connectedComponentsWithStats(m)
    c = org[:, :2].mean(axis=0)
    reach = 2.5 * np.max(np.hypot(*(org[:, :2] - c).T))
    a_med = float(np.median(org[:, 2]))
    best, best_d = None, np.inf
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        d = float(np.hypot(*(cent[i] - c)))
        if a_med / area_tol <= a <= a_med * area_tol and d < min(reach, best_d):
            best, best_d = np.r_[cent[i], a], d
    return None if best is None else np.vstack([org, best])


# ---------------------------------------------------------------- pose

def body_points(spacing_m: float, odd_deg: float) -> np.ndarray:
    """Guard centres in the body frame (m), the odd guard first, then counter-clockwise seen from above.
    spacing_m: centre-to-centre distance of adjacent guards (the square's side). odd_deg: body angle of the odd guard
    from the nose, counter-clockwise (an X frame: 45, 135, -135 or -45)."""
    a = np.radians(odd_deg + 90.0 * np.arange(4))
    return np.c_[np.cos(a), np.sin(a), np.zeros(4)] * spacing_m / np.sqrt(2.0)


def orderings(px4: np.ndarray, odd: int) -> list[list[int]]:
    """Both cyclic orders of 4 image points (a convex quad: angle order about the mean is the hull order), each starting
    at the odd point. Which one matches counter-clockwise in the body depends on the view; PnP tries both."""
    c = px4[:, :2].mean(axis=0)
    ring = list(np.argsort(np.arctan2(px4[:, 1] - c[1], px4[:, 0] - c[0])))
    k = ring.index(odd)
    ring = ring[k:] + ring[:k]
    return [ring, [ring[0]] + ring[1:][::-1]]


def rpy(Rwb) -> np.ndarray:
    """roll, pitch, yaw (rad), ZYX: yaw is the body x-axis heading in the world frame."""
    return np.array([np.arctan2(Rwb[2, 1], Rwb[2, 2]), -np.arcsin(np.clip(Rwb[2, 0], -1, 1)),
                     np.arctan2(Rwb[1, 0], Rwb[0, 0])])


def _rot_angle(A, B) -> float:
    return float(np.arccos(np.clip((np.trace(A.T @ B) - 1) / 2, -1, 1)))


def guard_pose(px4, odd, body, K, dist, R, t, prev=None, ambig=1.5):
    """World pose of the drone from 4 guard pixels. R, t: floor pose (world -> camera). Tries both image orders with
    IPPE (2 solutions each), keeps upright branches, takes the lowest reprojection error; when another branch is
    within `ambig` x of it (the planar flip ambiguity at a shallow view), takes the one nearest `prev` (a rotation)."""
    cv2 = vt._cv2()
    K, dist = np.asarray(K, float), np.asarray(dist, float)
    R, t = np.asarray(R, float), np.asarray(t, float).reshape(3)
    obj = np.asarray(body, float)
    cands = []
    for order in orderings(np.asarray(px4, float), odd):
        img = np.asarray(px4, float)[order, :2]
        _, rvecs, tvecs, _ = cv2.solvePnPGeneric(obj, img.reshape(-1, 1, 2), K, dist, flags=cv2.SOLVEPNP_IPPE)
        for rv, tv in zip(rvecs, tvecs):
            Rcb = cv2.Rodrigues(rv)[0]
            Rwb = R.T @ Rcb
            if Rwb[2, 2] < UPRIGHT_COS:
                continue
            err = float(np.sqrt(np.mean(np.sum(
                (cv2.projectPoints(obj, rv, tv, K, dist)[0].reshape(-1, 2) - img) ** 2, axis=1))))
            cands.append((err, Rwb, R.T @ (tv.reshape(3) - t), float(tv.reshape(3)[2])))
    if not cands:
        return None
    cands.sort(key=lambda c: c[0])
    best = cands[0]
    if prev is not None:
        near = [c for c in cands if c[0] <= ambig * best[0] + 0.3]
        best = min(near, key=lambda c: _rot_angle(c[1], prev))
    err, Rwb, p, rng = best
    return {"R": Rwb, "p": p, "rpy": rpy(Rwb), "reproj_px": err, "range_m": rng, "n_cand": len(cands)}


def depth_sigma(range_m, f_px, spacing_m, sigma_px) -> float:
    """PnP depth noise along the view ray: sigma_z ~ Z^2 sigma_px / (f B)."""
    return float(range_m) ** 2 * float(sigma_px) / (float(f_px) * float(spacing_m))


def track_pose(video, cam, marks, spacing_m, odd_deg, stride=1, static_t=(), lo=vt.HSV_LO, hi=vt.HSV_HI,
               min_area=vt.MIN_AREA, max_reproj_px=4.0):
    """Per kept frame: video time, world position (3), roll/pitch/yaw (3), reprojection px, camera range m; NaN rows
    where the pose fails (fewer than 3 orange + 1 white guard, merged blobs, or reprojection above max_reproj_px)."""
    cv2 = vt._cv2()
    K, dist = np.asarray(cam["K"], float), np.asarray(cam["dist"], float)
    R, t, _ = vt.floor_pose(K, dist, marks["world"], marks["pixel"])
    body = body_points(spacing_m, odd_deg)
    st = vt.static_mask(video, static_t, lo, hi) if static_t else None
    ws = white_static(video, static_t) if static_t else None
    cap, rows, prev, i = cv2.VideoCapture(video), [], None, 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % stride == 0:
            row = np.full(9, np.nan)
            row[0] = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            g = detect_frame(frame, lo, hi, min_area, st, ws)
            pz = None if g is None else guard_pose(g, 3, body, K, dist, R, t, prev)
            if pz is not None and pz["reproj_px"] <= max_reproj_px:
                row[1:] = [*pz["p"], *pz["rpy"], pz["reproj_px"], pz["range_m"]]
                prev = pz["R"]
            rows.append(row)
        i += 1
    return np.array(rows).reshape(-1, 9)


# ---------------------------------------------------------------- audio sync

def audio_track(video):
    """Mono samples (float) and rate of the video's first audio stream, and the audio start on the video clock (s)."""
    cv2 = vt._cv2()
    cap = cv2.VideoCapture(str(video), cv2.CAP_MSMF, [cv2.CAP_PROP_AUDIO_STREAM, 0, cv2.CAP_PROP_VIDEO_STREAM, -1,
                                                      cv2.CAP_PROP_AUDIO_DATA_DEPTH, cv2.CV_16S])
    if not cap.isOpened():
        raise SystemExit(f"{video}: no audio stream (OpenCV MSMF backend)")
    sr = cap.get(cv2.CAP_PROP_AUDIO_SAMPLES_PER_SECOND)
    base = int(cap.get(cv2.CAP_PROP_AUDIO_BASE_INDEX))
    shift = cap.get(cv2.CAP_PROP_AUDIO_SHIFT_NSEC) * 1e-9          # > 0: audio starts after the first video frame
    chunks = []
    while cap.grab():
        ok, a = cap.retrieve(None, base)
        if ok and a is not None:
            chunks.append(a.reshape(-1))
    x = np.concatenate(chunks).astype(float) if chunks else np.zeros(0)
    return x, float(sr), float(shift)


def band_level_db(x, sr, win_s=0.02, band=(200.0, 4000.0)):
    """Window centre times (s) and band energy (dB) of non-overlapping windows; the band skips hum and most speech."""
    n = max(16, int(round(win_s * sr)))
    m = len(x) // n
    if m == 0:
        return np.zeros(0), np.zeros(0)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    sel = (f >= band[0]) & (f <= band[1])
    spec = np.abs(np.fft.rfft(x[:m * n].reshape(m, n) * np.hanning(n), axis=1)) ** 2
    return (np.arange(m) + 0.5) * n / sr, 10 * np.log10(spec[:, sel].sum(axis=1) + 1e-12)


def onset(t, level, rise_db=12.0, hold_s=0.5, frac=0.8, pre_q=20):
    """First time `level` rises rise_db above its quiet floor (pre_q percentile) and stays there for at least `frac`
    of the next hold_s: a motor spool-up holds, a clap or a word does not. None if never."""
    if len(t) < 2:
        return None
    up = level > np.percentile(level, pre_q) + rise_db
    w = max(1, int(round(hold_s / np.median(np.diff(t)))))
    run = np.convolve(up.astype(float), np.ones(w) / w, mode="full")[w - 1:]   # mean of up[k:k+w]
    k = np.flatnonzero(up & (run >= frac))
    return float(t[k[0]]) if len(k) else None


def audio_onset(video, **kw):
    x, sr, shift = audio_track(video)
    t, lv = band_level_db(x, sr)
    o = onset(t, lv, **kw)
    return None if o is None else o + shift


def motor_onset(t, motors, frac=0.05, hold_s=0.5):
    """First telemetry time the mean motor command leaves its resting value by `frac` of its range and stays out for
    hold_s (motors are commanded to spin at idle-enable or take-off)."""
    m = np.nanmean(np.asarray(motors, float).reshape(len(t), -1), axis=1)
    base, top = np.nanpercentile(m, 2), np.nanpercentile(m, 98)
    if not top > base:
        return None
    return onset(np.asarray(t, float), m, rise_db=frac * (top - base), hold_s=hold_s, frac=0.9, pre_q=2)


def audio_offset(video, csv_path, t0=None):
    """Telemetry minus video time (the `sync` of `video_truth run`) from motor sound vs motor command. t0: telemetry
    time origin (run uses the first locx tick); default the first motor tick."""
    tel = vt.read_livewatch(csv_path, list(MOTORS))
    t = tel["t"] - (tel["t"][0] if t0 is None else t0)
    mo, ao = motor_onset(t, np.c_[[tel[n] for n in MOTORS]].T), audio_onset(video)
    if mo is None or ao is None:
        raise SystemExit(f"audio sync: no onset found (telemetry {mo}, audio {ao})")
    return mo - ao


# ---------------------------------------------------------------- smoothing

def rts_ca(t, z, sigma, q):
    """Constant-acceleration Kalman filter + Rauch-Tung-Striebel smoother on one axis. t increasing (irregular steps
    fine), z with NaN gaps, sigma = measurement std, q = white-jerk spectral density. Returns position, velocity,
    acceleration and their stds, each (n,)."""
    t, z = np.asarray(t, float), np.asarray(z, float)
    n = len(t)
    ok = np.isfinite(z)
    if not ok.any():
        nan = np.full(n, np.nan)
        return nan, nan, nan, nan, nan, nan
    k0 = int(np.argmax(ok))
    x, P = np.array([z[k0], 0.0, 0.0]), np.diag([sigma ** 2, 1.0, 10.0])
    H, r = np.array([1.0, 0.0, 0.0]), sigma ** 2
    xs, Ps, xp, Pp, Fs = (np.zeros((n, 3)), np.zeros((n, 3, 3)), np.zeros((n, 3)), np.zeros((n, 3, 3)),
                          np.zeros((n, 3, 3)))
    for k in range(n):
        dt = 0.0 if k == 0 else t[k] - t[k - 1]
        F = np.array([[1, dt, dt * dt / 2], [0, 1, dt], [0, 0, 1]])
        Q = q * np.array([[dt ** 5 / 20, dt ** 4 / 8, dt ** 3 / 6], [dt ** 4 / 8, dt ** 3 / 3, dt ** 2 / 2],
                          [dt ** 3 / 6, dt ** 2 / 2, dt]])
        x, P = F @ x, F @ P @ F.T + Q
        xp[k], Pp[k], Fs[k] = x, P, F
        if ok[k] and k >= k0:
            s = H @ P @ H + r
            g = P @ H / s
            x, P = x + g * (z[k] - H @ x), P - np.outer(g, H @ P)
        xs[k], Ps[k] = x, P
    for k in range(n - 2, -1, -1):
        C = Ps[k] @ Fs[k + 1].T @ np.linalg.inv(Pp[k + 1])
        xs[k] = xs[k] + C @ (xs[k + 1] - xp[k + 1])
        Ps[k] = Ps[k] + C @ (Ps[k + 1] - Pp[k + 1]) @ C.T
    sd = np.sqrt(np.maximum(np.diagonal(Ps, axis1=1, axis2=2), 0))
    xs[:k0], sd[:k0] = np.nan, np.nan                                  # nothing before the first measurement
    return xs[:, 0], xs[:, 1], xs[:, 2], sd[:, 0], sd[:, 1], sd[:, 2]


# ---------------------------------------------------------------- metrics + TUM

def quat_xyzw(R) -> np.ndarray:
    R = np.asarray(R, float)
    w = np.sqrt(max(0.0, 1 + R[0, 0] + R[1, 1] + R[2, 2])) / 2
    x = np.copysign(np.sqrt(max(0.0, 1 + R[0, 0] - R[1, 1] - R[2, 2])) / 2, R[2, 1] - R[1, 2])
    y = np.copysign(np.sqrt(max(0.0, 1 - R[0, 0] + R[1, 1] - R[2, 2])) / 2, R[0, 2] - R[2, 0])
    z = np.copysign(np.sqrt(max(0.0, 1 - R[0, 0] - R[1, 1] + R[2, 2])) / 2, R[1, 0] - R[0, 1])
    return np.array([x, y, z, w])


def rot_rpy(r, p, y) -> np.ndarray:
    cr, sr, cp, sp, cy, sy = np.cos(r), np.sin(r), np.cos(p), np.sin(p), np.cos(y), np.sin(y)
    return np.array([[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                     [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                     [-sp, cp * sr, cp * cr]])


def write_tum(path, t, p, rpy_rad=None):
    """TUM trajectory: `t x y z qx qy qz qw` per line, finite rows only (evo reads it as is)."""
    with open(path, "w") as f:
        for k in range(len(t)):
            a = np.zeros(3) if rpy_rad is None else np.nan_to_num(rpy_rad[k])
            if np.isfinite(p[k]).all():
                f.write(f"{t[k]:.6f} {p[k][0]:.5f} {p[k][1]:.5f} {p[k][2]:.5f} "
                        + " ".join(f"{v:.6f}" for v in quat_xyzw(rot_rpy(*a))) + "\n")


def umeyama(src, dst, scale=False):
    """R, t, s minimising sum |dst - (s R src + t)|^2 (Umeyama 1991), rows are points."""
    src, dst = np.asarray(src, float), np.asarray(dst, float)
    ms, md = src.mean(axis=0), dst.mean(axis=0)
    a, b = src - ms, dst - md
    U, D, Vt = np.linalg.svd(b.T @ a / len(src))
    S = np.eye(src.shape[1])
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[-1, -1] = -1
    R = U @ S @ Vt
    s = float(np.trace(np.diag(D) @ S) / (a ** 2).sum(axis=1).mean()) if scale else 1.0
    return R, md - s * R @ ms, s


def ate_rpe(t, truth, est, scale=False, delta_s=1.0) -> dict:
    """ATE after Umeyama alignment of est onto truth (same timestamps, NaN rows dropped), per-axis rms, and RPE: the
    rms translation error of est's delta_s-second displacement against truth's (drift per second)."""
    ok = np.isfinite(truth).all(axis=1) & np.isfinite(est).all(axis=1)
    t, a, b = np.asarray(t)[ok], truth[ok], est[ok]
    R, tr, s = umeyama(b, a, scale)
    e = (s * b @ R.T + tr) - a
    j = np.searchsorted(t, t + delta_s)
    v = j < len(t)
    d = (s * (b[j[v]] - b[v]) @ R.T) - (a[j[v]] - a[v])
    return {"n": int(ok.sum()), "ate_rms_m": round(float(np.sqrt(np.mean(np.sum(e ** 2, axis=1)))), 4),
            "ate_max_m": round(float(np.max(np.linalg.norm(e, axis=1))), 4),
            "ate_axis_rms_m": [round(float(v_), 4) for v_ in np.sqrt(np.mean(e ** 2, axis=0))],
            f"rpe_{delta_s:g}s_rms_m": round(float(np.sqrt(np.mean(np.sum(d ** 2, axis=1)))), 4) if v.any() else None,
            "align_rot_deg": round(float(np.degrees(_rot_angle(np.eye(len(R)), R))), 2), "scale": round(s, 4)}


def yaw_compare(t, yaw_truth, t_est, yaw_est_deg) -> dict:
    """Truth yaw (rad, world) vs the estimator's yaw (deg, its own frame and sign): best sign, constant offset and the
    rms of what is left."""
    ye = np.radians(np.interp(t, t_est, np.degrees(np.unwrap(np.radians(yaw_est_deg)))))
    ok = np.isfinite(yaw_truth) & np.isfinite(ye)
    best = None
    for sgn in (1.0, -1.0):
        d = np.angle(np.exp(1j * (yaw_truth[ok] - sgn * ye[ok])))
        off = np.angle(np.mean(np.exp(1j * d)))
        res = np.angle(np.exp(1j * (d - off)))
        rms = float(np.degrees(np.sqrt(np.mean(res ** 2))))
        if best is None or rms < best["rms_deg"]:
            best = {"sign": sgn, "offset_deg": round(float(np.degrees(off)), 2), "rms_deg": round(rms, 2),
                    "max_deg": round(float(np.degrees(np.max(np.abs(res)))), 2), "n": int(ok.sum())}
    return best


# ---------------------------------------------------------------- the pose stage

def pose_run(video, cam, marks, csv_path, run_dir, spacing_m, odd_deg, guard_offset=0.0, stride=1, static_t=(),
             sync=None, sigma_m=0.01, q_pos=200.0, q_yaw=2000.0, max_reproj_px=4.0) -> dict:
    """Second stage after `video_truth run`: PnP pose per frame -> RTS -> pose.csv, truth.tum / est.tum, pose_report.
    sync None takes the run's offset; "audio" uses the motor sound; a number is used as is."""
    run_dir = Path(run_dir)
    rep0 = json.loads((run_dir / "report.json").read_text())
    cache = run_dir / "pose_track.npz"
    if cache.exists():
        tr = np.load(cache)["rows"]
    else:
        tr = track_pose(video, cam, marks, spacing_m, odd_deg, stride, static_t, max_reproj_px=max_reproj_px)
        np.savez(cache, rows=tr)
    tel = vt.read_livewatch(csv_path, [vt.X_CM, vt.Y_CM, vt.Z_M, vt.X_V, vt.Y_V])
    t0 = tel["t"][0]
    te = tel["t"] - t0
    off = (rep0["sync_offset_s"] if sync is None else audio_offset(video, csv_path, t0) if sync == "audio"
           else float(sync))
    if sync == "audio" and np.isfinite(tr[:, 8]).any():
        off += float(np.nanmedian(tr[:, 8])) / 343.0                    # the mic hears the motors range/c late
    tv = tr[:, 0] + off
    fr = rep0["frame"]
    win = (tv >= fr["t_to"] - 1.0) & (tv <= fr["t_end"] + 1.0)
    tv, P, A, rp, rng = tv[win], tr[win, 1:4], tr[win, 4:7], tr[win, 7], tr[win, 8]
    P[:, 2] -= guard_offset                                            # guard plane -> the height telemetry reports
    sm = [rts_ca(tv, P[:, i], sigma_m, q_pos) for i in range(3)]
    yaw_u = A[:, 2].copy()
    okyaw = np.isfinite(yaw_u)
    yaw_u[okyaw] = np.unwrap(yaw_u[okyaw])
    sy = rts_ca(tv, yaw_u, np.radians(1.0), q_yaw)
    pos, vel, psd = np.c_[[s[0] for s in sm]].T, np.c_[[s[1] for s in sm]].T, np.c_[[s[3] for s in sm]].T
    est = np.c_[np.interp(tv, te, tel[vt.X_CM]) / 100.0, np.interp(tv, te, tel[vt.Y_CM]) / 100.0,
                np.interp(tv, te, np.nan_to_num(tel[vt.Z_M]))]
    estv = np.c_[np.interp(tv, te, tel[vt.X_V]), np.interp(tv, te, tel[vt.Y_V])] / 100.0
    att = np.c_[A[:, 0], A[:, 1], sy[0]]
    with open(run_dir / "pose.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_tel", "x_m", "y_m", "z_m", "vx_ms", "vy_ms", "vz_ms", "sx_m", "sy_m", "sz_m",
                    "roll_deg", "pitch_deg", "yaw_deg", "yaw_rate_dps", "reproj_px", "range_m", "raw"])
        for k in range(len(tv)):
            w.writerow([f"{tv[k]:.3f}", *(f"{v:.4f}" for v in (*pos[k], *vel[k], *psd[k])),
                        *(f"{v:.2f}" for v in np.degrees([*att[k], sy[1][k]])), f"{rp[k]:.2f}", f"{rng[k]:.3f}",
                        int(np.isfinite(P[k]).all())])
    write_tum(run_dir / "truth.tum", tv, pos, att)
    write_tum(run_dir / "est.tum", tv, est)
    fly = (tv >= fr["t_to"]) & (tv <= fr["t_end"])
    raw_ok = np.isfinite(P).all(axis=1)
    K = np.asarray(cam["K"], float)
    zerr = P[fly & raw_ok, 2] - est[fly & raw_ok, 2]
    R_al, _, _ = umeyama(est[fly & raw_ok][:, :2], pos[fly & raw_ok][:, :2])
    ve = (estv[fly] @ R_al.T) - vel[fly][:, :2]
    dt = np.diff(tr[:, 0]) * 1000.0                                    # phones drop to VFR in dim light: check the spacing
    rep = {
        "frame_dt_ms": {"median": round(float(np.median(dt)), 2), "max": round(float(dt.max()), 1),
                        "frac_gt_1p5x": round(float(np.mean(dt > 1.5 * np.median(dt))), 4)} if len(dt) else None,
        "frames": int(len(tr)), "frames_in_flight": int(fly.sum()), "pose_ok_frac": round(float(raw_ok[fly].mean()), 3)
        if fly.any() else 0.0, "sync_offset_s": round(off, 3), "sync": "run" if sync is None else str(sync),
        "reproj_px_median": round(float(np.nanmedian(rp)), 2) if np.isfinite(rp).any() else None,
        "range_m_median": round(float(np.nanmedian(rng)), 3) if np.isfinite(rng).any() else None,
        "depth_sigma_m_pred": round(depth_sigma(np.nanmedian(rng), K[0, 0], spacing_m, np.nanmedian(rp)), 4)
        if np.isfinite(rng).any() else None,
        "height_vs_telemetry": {"bias_m": round(float(np.mean(zerr)), 4), "rms_m": round(float(np.sqrt(np.mean(zerr ** 2))), 4)}
        if len(zerr) else None,
        "ate_se3": ate_rpe(tv[fly], pos[fly], est[fly]) if fly.sum() > 10 else None,
        "ate_sim3": ate_rpe(tv[fly], pos[fly], est[fly], scale=True) if fly.sum() > 10 else None,
        "velocity_xy_rms_ms": round(float(np.sqrt(np.nanmean(np.sum(ve ** 2, axis=1)))), 4) if len(ve) else None,
        "evo": [f"evo_ape tum {run_dir / 'truth.tum'} {run_dir / 'est.tum'} -a --plot",
                f"evo_rpe tum {run_dir / 'truth.tum'} {run_dir / 'est.tum'} -a --delta 1 --delta_unit m"],
    }
    try:
        ty = vt.read_livewatch(csv_path, [EST_YAW])
        rep["yaw_vs_estimator"] = yaw_compare(tv[fly], sy[0][fly], ty["t"] - t0, ty[EST_YAW])
    except SystemExit:
        rep["yaw_vs_estimator"] = None
    (run_dir / "pose_report.json").write_text(json.dumps(rep, indent=2))
    return rep


# ---------------------------------------------------------------- side by side

def side_by_side(a, a_t0, b, b_t0, out_mp4, dur_s, height=720, labels=("A", "B")) -> int:
    """Two clips next to each other, each starting at its own video time t0 (e.g. the traj_go moment), for dur_s."""
    cv2 = vt._cv2()
    caps = [cv2.VideoCapture(str(v)) for v in (a, b)]
    for c, t0 in zip(caps, (a_t0, b_t0)):
        c.set(cv2.CAP_PROP_POS_MSEC, t0 * 1000.0)
    fps = min(c.get(cv2.CAP_PROP_FPS) or 30 for c in caps)
    sizes = [(int(round(c.get(cv2.CAP_PROP_FRAME_WIDTH) * height / c.get(cv2.CAP_PROP_FRAME_HEIGHT))), height)
             for c in caps]
    w = cv2.VideoWriter(str(out_mp4), cv2.VideoWriter_fourcc(*"mp4v"), fps, (sum(s[0] for s in sizes), height))
    last, n = [None, None], 0
    for i in range(int(dur_s * fps)):
        tau = i / fps
        for j, (c, t0) in enumerate(zip(caps, (a_t0, b_t0))):
            while last[j] is None or c.get(cv2.CAP_PROP_POS_MSEC) / 1000.0 < t0 + tau:
                ok, f = c.read()
                if not ok:
                    break
                last[j] = cv2.resize(f, sizes[j], interpolation=cv2.INTER_AREA)
        if any(f is None for f in last):
            break
        img = np.hstack(last)
        x = 0
        for (wd, _), lab in zip(sizes, labels):
            cv2.putText(img, f"{lab}  t+{tau:4.1f}s", (x + 20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3,
                        cv2.LINE_AA)
            x += wd
        w.write(img)
        n += 1
    w.release()
    return n


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("pose", help="6-DoF pose + RTS + TUM/ATE, after `video_truth run`")
    o.add_argument("video"); o.add_argument("--cam", required=True); o.add_argument("--marks", required=True)
    o.add_argument("--csv", required=True); o.add_argument("--run", required=True)
    o.add_argument("--spacing", type=float, required=True, help="guard centre-to-centre spacing, adjacent guards, m")
    o.add_argument("--odd-deg", type=float, required=True, help="body angle of the white guard from the nose, CCW, deg")
    o.add_argument("--guard-offset", type=float, default=0.0); o.add_argument("--stride", type=int, default=1)
    o.add_argument("--static", default="", help="comma list of video times (s) for the static masks")
    o.add_argument("--sync", default=None, help="omit: the run's offset; audio; or seconds")
    o.add_argument("--sigma", type=float, default=0.01); o.add_argument("--max-reproj", type=float, default=4.0)
    s = sub.add_parser("audio", help="telemetry - video offset from the motor sound")
    s.add_argument("video"); s.add_argument("--csv", required=True)
    b = sub.add_parser("sbs", help="side-by-side clip of two runs")
    b.add_argument("a"); b.add_argument("a_t0", type=float); b.add_argument("b"); b.add_argument("b_t0", type=float)
    b.add_argument("--out", required=True); b.add_argument("--dur", type=float, default=25.0)
    b.add_argument("--labels", default="PID,vpNN"); b.add_argument("--height", type=int, default=720)
    a = p.parse_args(argv)
    if a.cmd == "pose":
        cam, marks = vt.load_cam(a.cam, vt.video_size(a.video)), json.loads(Path(a.marks).read_text())
        st = tuple(float(v) for v in a.static.split(",") if v)
        sync = None if a.sync is None else a.sync if a.sync == "audio" else float(a.sync)
        print(json.dumps(pose_run(a.video, cam, marks, a.csv, a.run, a.spacing, a.odd_deg, a.guard_offset, a.stride,
                                  st, sync, a.sigma, max_reproj_px=a.max_reproj), indent=2))
    elif a.cmd == "audio":
        print(json.dumps({"sync_offset_s": round(audio_offset(a.video, a.csv), 3)}))
    else:
        print(side_by_side(a.a, a.a_t0, a.b, a.b_t0, a.out, a.dur, a.height, tuple(a.labels.split(","))), "frames")


if __name__ == "__main__":
    main()
