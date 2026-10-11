"""Live camera tracker -> CMD 0x2C EXT_POS fixes (design docs/video-truth/live_camera_fusion_design_2026-10-11.md sec 1).

Pipeline per frame: guard blobs (video_truth.detect_guards) -> floor plane at the guard height (pixel_to_plane) ->
drone centre (drone_centre), pad frame, m. Every `period` s the median of the last `n_med` detections becomes one
fix, mapped to the EKF earth frame (cm) by an EarthAlign and packed with ground_station/comm/ext_pos_encoder.py.
Firmware is in shadow mode: it only logs the innovation (g_ext_pos.*).

Not measured yet (pass them in, R4 measures them with one sharp event seen by camera and telemetry):
camera latency (photon -> frame read), link delay (send -> firmware receipt, the 0xFE delay frame), guard height
offset. The CLI is a dry run: it writes the fixes and frames to CSV and sends nothing. A live sender takes a
`send(bytes)` callable for the uplink.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from ground_station.analysis import video_truth as vt
from ground_station.comm.ext_pos_encoder import ext_pos_fix_frame


def detect_xy(frame_bgr, cam: dict, z_m: float, static=None) -> Optional[np.ndarray]:
    """Drone centre on the floor frame (pad frame, m) at guard height z_m, or None when the guards do not resolve."""
    b = vt.detect_guards(frame_bgr, static=static)
    if len(b) < 2:
        return None
    K, dist = np.asarray(cam["K"], float), np.asarray(cam["dist"], float)
    R, t = np.asarray(cam["R"], float), np.asarray(cam["t"], float)
    c = vt.drone_centre(vt.pixel_to_plane(b[:, :2], K, dist, R, t, z_m), b[:, 2])
    return None if c is None else np.asarray(c, float)[:2]


class RingTracker:
    """The R3 detector run online (bright-red ring, docs/video-truth/r3_drift_truth_2026-10-11.md): ring mask on 4x4
    pixel blocks, scenery blocks removed, mean-shift around the predicted position. Seeded with the drone on the pad:
    the lit component nearest `seed_px` is the drone, every other block lit in that first frame is scenery (the
    offline R3 run removed blocks lit in most frames instead, which needs the whole video)."""
    B = 4

    def __init__(self, seed_px, radius_px: float = 150.0, min_blocks: int = 12):   # R3 offline: 150
        self.seed, self.r, self.min_blocks = np.asarray(seed_px, float), radius_px, min_blocks
        self.static = None
        self.c = self.vel = None
        self.lost = 0

    def mask(self, frame_bgr) -> np.ndarray:
        cv2 = vt._cv2()
        h = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        m = (((h[..., 0] <= 10) | (h[..., 0] >= 165)) & (h[..., 1] >= 90) & (h[..., 2] >= 195)).astype(np.uint8)
        H, W = (m.shape[0] // self.B) * self.B, (m.shape[1] // self.B) * self.B
        return m[:H, :W].reshape(H // self.B, self.B, W // self.B, self.B).sum((1, 3)) >= 3

    def _seed(self, m) -> bool:
        cv2 = vt._cv2()
        n, lab = cv2.connectedComponents(cv2.dilate(m.astype(np.uint8), np.ones((7, 7), np.uint8)))
        if n < 2:
            return False
        yy, xx = np.nonzero(m)
        px = np.c_[xx, yy] * self.B + self.B / 2.0
        k = lab[yy, xx][np.argmin(np.linalg.norm(px - self.seed, axis=1))]
        drone = (lab == k) & m
        self.static = cv2.dilate((m & ~drone).astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        self.c, self.vel = px[lab[yy, xx] == k].mean(0), np.zeros(2)
        return True

    def step(self, frame_bgr) -> Optional[np.ndarray]:
        """Ring centre (u, v) px in this frame, or None while coasting."""
        m = self.mask(frame_bgr)
        if self.c is None:
            return self.c.copy() if self._seed(m) else None
        yy, xx = np.nonzero(m & ~self.static)
        pts = np.c_[xx, yy] * self.B + self.B / 2.0
        p, rr = self.c + self.vel, self.r + 15.0 * min(self.lost, 10)
        inside = np.zeros(len(pts), bool)
        for _ in range(6):
            inside = np.sum((pts - p) ** 2, axis=1) < rr * rr
            if inside.sum() < self.min_blocks:
                break
            p = pts[inside].mean(0)
        inside = np.sum((pts - p) ** 2, axis=1) < rr * rr
        if inside.sum() >= self.min_blocks:
            self.vel, self.c, self.lost = 0.6 * self.vel + 0.4 * (p - self.c), p, 0
            return p.copy()
        self.lost += 1
        self.vel *= 0.8
        self.c = self.c + self.vel
        return None


def ring_xy(uv, cam: dict, z_m: float) -> np.ndarray:
    K, dist = np.asarray(cam["K"], float), np.asarray(cam["dist"], float)
    R, t = np.asarray(cam["R"], float), np.asarray(cam["t"], float)
    return np.asarray(vt.pixel_to_plane(np.asarray(uv, float)[None, :], K, dist, R, t, z_m)[0], float)[:2]


@dataclass
class EarthAlign:
    """Pad frame (m) -> EKF earth frame (cm): earth = 100 * a * m(cam - cam0) + earth0, m = conj when mirrored.
    |a| = 1 (rotation only, both frames are metric); `scale` is the fitted |a| before normalising, a check."""
    a: complex = 1 + 0j
    mirror: bool = False
    cam0: complex = 0j
    earth0: complex = 0j
    scale: float = 1.0
    rms_cm: float = 0.0

    def apply(self, xy_m) -> np.ndarray:
        z = complex(*np.asarray(xy_m, float)[:2]) - self.cam0
        e = 100.0 * self.a * (z.conjugate() if self.mirror else z) + self.earth0
        return np.array([e.real, e.imag])

    @classmethod
    def fit(cls, cam_xy_m, earth_cm) -> "EarthAlign":
        """Least-squares rotation + offset over paired samples (option b: the first 1 m out-and-back), both
        handednesses tried, the lower rms kept. Needs real motion: a hover alone leaves the rotation undecided."""
        c = np.asarray(cam_xy_m, float); e = np.asarray(earth_cm, float)
        zc, ze = c[:, 0] + 1j * c[:, 1], (e[:, 0] + 1j * e[:, 1]) / 100.0
        best = None
        for mirror in (False, True):
            b = zc.conj() if mirror else zc
            bm, em = b.mean(), ze.mean()
            db, de = b - bm, ze - em
            if np.vdot(db, db).real < 1e-6:
                raise ValueError("camera path spans < 1 mm: fit needs motion")
            a = np.vdot(db, de) / np.vdot(db, db)
            u = a / abs(a)
            rms = float(np.sqrt(np.mean(np.abs(de - u * db) ** 2))) * 100.0
            if best is None or rms < best.rms_cm:
                c0 = bm.conjugate() if mirror else bm           # cam0 in the unmirrored pad frame
                best = cls(a=complex(u), mirror=mirror, cam0=complex(c0), earth0=complex(em * 100.0),
                           scale=float(abs(a)), rms_cm=rms)
        return best


class FixScheduler:
    """One fix every `period` s from the last `n_med` detections inside that period: detections farther than
    `max_spread_m` from their median are dropped (a bad blob), and the fix is the median of the rest. Skipped when
    fewer than `min_in` remain or fewer than n_med arrived."""

    def __init__(self, period: float = 1.0, n_med: int = 5, max_spread_m: float = 0.05, min_in: int = 3):
        self.period, self.n_med, self.max_spread, self.min_in = period, n_med, max_spread_m, min(min_in, n_med)
        self.buf: list[tuple[float, np.ndarray]] = []
        self.next_t: Optional[float] = None
        self.n_sent = self.n_skip = 0

    def push(self, t_cap: float, xy_m) -> None:
        self.buf.append((t_cap, np.asarray(xy_m, float)))
        if len(self.buf) > 4 * self.n_med:
            del self.buf[0]

    def due(self, now: float) -> Optional[tuple[float, np.ndarray]]:
        if self.next_t is None:
            self.next_t = now + self.period
            return None
        if now < self.next_t:
            return None
        self.next_t += self.period * max(1, int((now - self.next_t) // self.period) + 1)
        recent = [d for d in self.buf if d[0] > now - self.period][-self.n_med:]
        if len(recent) < self.n_med:
            self.n_skip += 1
            return None
        xy, tc = np.array([d[1] for d in recent]), np.array([d[0] for d in recent])
        keep = np.linalg.norm(xy - np.median(xy, axis=0), axis=1) <= self.max_spread
        if keep.sum() < self.min_in:
            self.n_skip += 1
            return None
        self.n_sent += 1
        return float(np.median(tc[keep])), np.median(xy[keep], axis=0)


def run_source(source, cam: dict, align: EarthAlign, height_m: float | Callable[[float], float],
               cam_latency_s: float = 0.0, send: Callable[[bytes], None] | None = None, out_csv: str | None = None,
               sched: FixScheduler | None = None, clock: Callable[[], float] | None = None, max_frames: int = 0, start_s: float = 0.0,
               static=None, ring: RingTracker | None = None, uv_log: list | None = None) -> dict:
    """Read frames from `source` (file or device index), emit fixes. For a file the capture time is the frame pts and
    the send time is that pts too (age = camera latency only); live, both come from `clock`. `static`: a
    video_truth.static_mask of guard-colour scenery; without it a fixed coloured object can win the blob ranking."""
    cv2 = vt._cv2()
    cap = cv2.VideoCapture(int(source) if str(source).isdigit() else source)
    live = str(source).isdigit()
    while start_s and not live and cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0 < start_s and cap.grab():
        pass                                              # frame-exact; set(POS_MSEC) lands seconds off on scrcpy mkv
    clock = clock or time.monotonic
    sched = sched or FixScheduler()
    rows, n, n_det = [], 0, 0
    while True:
        ok, frame = cap.read()
        if not ok or (max_frames and n >= max_frames):
            break
        n += 1
        t_read = clock() if live else cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        t_cap = t_read - cam_latency_s
        z = height_m(t_cap) if callable(height_m) else height_m
        if ring is not None:
            uv = ring.step(frame)
            if uv_log is not None:
                uv_log.append((t_read, *(uv if uv is not None else (np.nan, np.nan))))
            xy = None if uv is None else ring_xy(uv, cam, z)
        else:
            xy = detect_xy(frame, cam, z, static)
        if xy is not None:
            n_det += 1
            sched.push(t_cap, xy)
        fix = sched.due(t_read)
        if fix is None:
            continue
        t_fix, med = fix
        e = align.apply(med)
        frame_b = ext_pos_fix_frame(e[0], e[1], t_read - t_fix)
        if send:
            send(frame_b)
        rows.append([round(t_read, 3), round(t_fix, 3), round(med[0], 4), round(med[1], 4), round(e[0], 1),
                     round(e[1], 1), frame_b.hex()])
    cap.release()
    if out_csv:
        with open(out_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_send", "t_cap", "pad_x_m", "pad_y_m", "earth_x_cm", "earth_y_cm", "frame_hex"])
            w.writerows(rows)
    return {"frames": n, "detections": n_det, "fixes": sched.n_sent, "skipped": sched.n_skip}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Live camera tracker, dry run (writes fixes, sends nothing).")
    ap.add_argument("--source", required=True, help="video file, or a capture device index")
    ap.add_argument("--cam", required=True, help="floor pose json (e.g. docs/video-truth/cam_r3_selfcal_2026-10-11.json)")
    ap.add_argument("--height-m", type=float, required=True, help="guard plane height above the floor, m")
    ap.add_argument("--cam-latency-s", type=float, default=0.0, help="photon -> frame read; UNMEASURED until R4")
    ap.add_argument("--yaw-deg", type=float, default=0.0, help="pad -> earth rotation until a fit or marker exists")
    ap.add_argument("--mirror", action="store_true")
    ap.add_argument("--out", required=True, help="fixes CSV")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--start-s", type=float, default=0.0, help="file only: seek before reading")
    ap.add_argument("--detector", choices=["ring", "guards"], default="ring", help="ring: R3 red ring (default)")
    ap.add_argument("--seed-px", default="", help="u,v of the drone on the pad; default the ArUco id0 centre")
    ap.add_argument("--static-t", default="", help='file only, "90,140,190": mask guard-colour pixels present at all these times')
    a = ap.parse_args(argv)
    cam = vt.load_cam(a.cam, None if str(a.source).isdigit() else vt.video_size(a.source))
    align = EarthAlign(a=complex(np.exp(1j * np.radians(a.yaw_deg))), mirror=a.mirror)
    times = [float(x) for x in a.static_t.split(",") if x]
    static = vt.static_mask(a.source, times) if times and not str(a.source).isdigit() else None
    ring = None
    if a.detector == "ring":
        with open(a.cam) as f:
            raw = json.load(f)
        seed = [float(x) for x in a.seed_px.split(",")] if a.seed_px else np.mean(raw["aruco_id0_corners_px"], axis=0)
        ring = RingTracker(seed)
    print(run_source(a.source, cam, align, a.height_m, a.cam_latency_s, None, a.out, max_frames=a.max_frames,
                     start_s=a.start_s, static=static, ring=ring))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
