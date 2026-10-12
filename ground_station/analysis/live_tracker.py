"""Live camera tracker -> CMD 0x2C EXT_POS fixes (design docs/video-truth/live_camera_fusion_design_2026-10-11.md sec 1).

Pipeline per frame: guard blobs (video_truth.detect_guards) -> floor plane at the guard height (pixel_to_plane) ->
drone centre (drone_centre), pad frame, m. Every `period` s the median of the last `n_med` detections becomes one
fix, mapped to the EKF earth frame (cm) by an EarthAlign and packed with ground_station/comm/ext_pos_encoder.py.
Firmware is in shadow mode: it only logs the innovation (g_ext_pos.*).

Not measured yet (pass them in, R4 measures them with one sharp event seen by camera and telemetry):
camera latency (photon -> frame read), link delay (send -> firmware receipt, the 0xFE delay frame), guard height
offset. Without --send the CLI is a dry run (fixes and frames to CSV). With --send udp:PORT it sends the raw
frames to `stream_log --relay-port PORT`, which forwards them on the logging socket (MicoAir sends telemetry to the
source of the last uplink datagram, so a second socket would steal the downlink); --mode / --delay-ms frames go
first and are repeated every 2 s while live. Sources: a video file, a device index, or window:<title> (the scrcpy
mirror, grabbed with mss). `replay` runs the online coral tracker on an R4 video and scores the fused estimate
(video_truth.ext_pos_replay) against truth4 at given link delays.
"""
from __future__ import annotations

import argparse
import csv
import json
import socket
import struct
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from ground_station.analysis import video_truth as vt
from ground_station.comm.ext_pos_encoder import (ext_pos_decode, ext_pos_delay_frame, ext_pos_encode,
                                                 ext_pos_fix_frame, ext_pos_mode_frame)


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


class CoralTracker:
    """video_truth.track_coral run online (the R4 truth tracker): coral guard-ring pixels (vt.coral_mask, minus the
    `static` scenery mask from vt.coral_static) in a square ROI around the last fix; their median is the drone. A
    miss grows the ROI 1.5x per frame up to the whole frame."""

    def __init__(self, seed_uv, static=None, roi: float = 140.0, min_px: int = 40):   # track_coral defaults
        self.c, self.static, self.roi, self.min_px = np.asarray(seed_uv, float), static, float(roi), min_px
        self.r = self.roi

    def step(self, frame_bgr) -> Optional[np.ndarray]:
        cv2 = vt._cv2()
        H, W = frame_bgr.shape[:2]
        u0, v0 = int(max(0, self.c[0] - self.r)), int(max(0, self.c[1] - self.r))
        u1, v1 = int(min(W, self.c[0] + self.r)), int(min(H, self.c[1] + self.r))
        if u1 > u0 and v1 > v0:
            m = vt.coral_mask(cv2.cvtColor(frame_bgr[v0:v1, u0:u1], cv2.COLOR_BGR2HSV))
            if self.static is not None:
                m &= ~self.static[v0:v1, u0:u1]
            vv, uu = np.nonzero(m)
            if len(uu) >= self.min_px:
                self.c, self.r = np.array([np.median(uu) + u0, np.median(vv) + v0]), self.roi
                return self.c.copy()
        self.r = min(self.r * 1.5, float(max(W, H)))
        return None

    def recentre(self, uv) -> None:
        self.c, self.r = np.asarray(uv, float), self.roi


class JumpGate:
    """Velocity gate on detections (pad frame, m): one farther than v_max * dt + slack from the last accepted one
    is a wrong blob and is dropped (R4 b8/b10/b12 offline coral tracks jumped 4-7 m/s); dt is capped at dt_max so a
    long wrong lock does not open the gate by itself. n_reseed rejects in a row that agree with each other (all
    within cluster_m of their median) re-seed the gate there: the drone moved while it was lost. A static wrong
    blob agrees with itself too, so re-seeds are counted and reported. Thresholds PROPOSED, not tuned on flights."""

    def __init__(self, v_max: float = 1.5, slack_m: float = 0.05, dt_max: float = 0.5, n_reseed: int = 10,
                 cluster_m: float = 0.05):
        self.v_max, self.slack, self.dt_max, self.n_reseed, self.cluster = v_max, slack_m, dt_max, n_reseed, cluster_m
        self.last: Optional[tuple] = None                 # (t, xy, uv) of the last accepted detection
        self.rej: list = []
        self.n_rej = self.reseeds = 0

    def check(self, t: float, xy, uv=None) -> bool:
        xy = np.asarray(xy, float)
        if self.last is None or (np.linalg.norm(xy - self.last[1])
                                 <= self.v_max * min(max(t - self.last[0], 0.0), self.dt_max) + self.slack):
            self.last, self.rej = (t, xy, uv), []
            return True
        self.n_rej += 1
        self.rej.append(xy)
        r = np.array(self.rej[-self.n_reseed:])
        if len(r) >= self.n_reseed and np.all(np.linalg.norm(r - np.median(r, axis=0), axis=1) <= self.cluster):
            self.reseeds += 1
            self.last, self.rej = (t, xy, uv), []
            return True
        return False


def fit_frame(frame: np.ndarray, size: tuple | None) -> np.ndarray:
    """Resize a grab to the size the run started with (K, seed and floor scale were set for it): a restore or a
    window resize mid-run would otherwise feed the tracker a different pixel scale."""
    if size is None or (frame.shape[1], frame.shape[0]) == tuple(size):
        return frame
    return vt._cv2().resize(frame, tuple(int(v) for v in size), interpolation=vt._cv2().INTER_AREA)


class WindowSource:
    """Live frames from a window's client area (the scrcpy mirror, e.g. `scrcpy --window-title cam`), Windows only:
    mss grabs the screen rectangle user32 reports. The window must show the whole video at its aspect (no letterbox
    bars), or the K rescale in load_cam is wrong. cv2.VideoCapture interface: read() -> (ok, bgr), release()."""

    def __init__(self, title: str, wait_s: float = 30.0, size: tuple | None = None):
        import ctypes
        from ctypes import wintypes
        import mss                                        # pip install mss
        self._ct, self._wt, self.u32 = ctypes, wintypes, ctypes.windll.user32
        try:
            self.u32.SetProcessDPIAware()                 # physical pixels, as mss grabs them
        except Exception:
            pass
        self.title, self.fixed = title, size             # size: every grab is resized to it (fit_frame)
        self.hwnd = self._find(wait_s)
        if not self.hwnd:
            raise RuntimeError(f"no window titled {title!r} after {wait_s:.0f} s")
        self.sct = mss.mss()

    def _find(self, wait_s: float) -> int:
        """Wait up to wait_s for the window (scrcpy still starting, or restarting after Android dropped the camera)."""
        t_end = time.monotonic() + wait_s
        while True:
            hwnd = self.u32.FindWindowW(None, self.title)
            if hwnd or time.monotonic() >= t_end:
                return hwnd
            time.sleep(0.2)

    def rect(self) -> dict:
        r, p = self._wt.RECT(), self._wt.POINT(0, 0)
        self.u32.GetClientRect(self.hwnd, self._ct.byref(r))
        self.u32.ClientToScreen(self.hwnd, self._ct.byref(p))
        return {"left": p.x, "top": p.y, "width": r.right - r.left, "height": r.bottom - r.top}

    def size(self) -> tuple:
        r = self.rect()
        return r["width"], r["height"]

    def read(self):
        t_end = time.monotonic() + 15.0                   # a live run survives a lost/zero-size window for 15 s
        while True:
            if not self.u32.IsWindow(self.hwnd):          # scrcpy restarted into a new part: same title, new window
                self.hwnd = self.u32.FindWindowW(None, self.title)
            if self.hwnd and self.u32.IsIconic(self.hwnd):    # minimized (seen ~2.5 s after start): 0x0 client rect
                print(f"window {self.title!r} minimized, restoring", flush=True)
                self.u32.ShowWindow(self.hwnd, 4)         # SW_SHOWNOACTIVATE
                time.sleep(0.3)
            r = self.rect() if self.hwnd else {"width": 0, "height": 0}
            if r["width"] > 0 and r["height"] > 0:
                return True, fit_frame(np.ascontiguousarray(np.asarray(self.sct.grab(r))[..., :3]), self.fixed)
            if time.monotonic() >= t_end:
                print(f"window {self.title!r} lost for 15 s", flush=True)
                return False, None
            time.sleep(0.2)

    def ready(self, wait_s: float = 30.0, min_mean: float = 8.0) -> tuple:
        """Wait until the window shows video (not minimized, not the black frame scrcpy opens with) and return its
        size. r5_live_1 read the size and floor check before the first frame: 0 markers, then 0 detections live
        while the same video replayed from file gave 1500/1500."""
        t_end = time.monotonic() + wait_s
        while True:
            ok, f = self.read()
            if ok and float(f.mean()) > min_mean:
                print(f"window {self.title!r} ready {f.shape[1]}x{f.shape[0]}", flush=True)
                return f.shape[1], f.shape[0]
            if time.monotonic() >= t_end:
                raise RuntimeError(f"window {self.title!r} shows no video after {wait_s:.0f} s")
            time.sleep(0.2)

    def get(self, prop) -> float:
        return 0.0

    def release(self) -> None:
        self.sct.close()


def floor_from_dir(video_dir, cam_path) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, dict]:
    """Floor pose of a fixed phone from an earlier capture with the same placement: id0 median pose in its aruco.npz
    (vt.floor_from_marker; R, t do not depend on the image scale). Returns R, t, the id0 centre (px of that video),
    the video width (to rescale pixels to the live window) and the scan's static corners {id: (4, 2) px}."""
    vd = Path(video_dir)
    size = vt.video_size(str(vd / "cam.mkv"))
    scan = dict(np.load(vd / "aruco.npz"))
    R, t, _ = vt.floor_from_marker(scan, vt.load_cam(cam_path, size))
    ref = {int(k[1:]): vt.static_corners(scan[k])[0] for k in scan if k.startswith("c")}
    return R, t, ref[0].mean(0), float(size[0]), {k: v for k, v in ref.items() if v is not None}


def floor_shift_px(frame_bgr, ref: dict, scale: float) -> tuple[float, int]:
    """Median corner shift (px, live window) of the markers seen now against the reference scan: the phone moved if
    it is more than a few px. Returns (shift, markers matched); (nan, 0) when none of them is visible."""
    cv2 = vt._cv2()
    c, ids, _ = vt.aruco_detector().detectMarkers(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY))
    d = [np.linalg.norm(cc.reshape(4, 2) - ref[int(m)] * scale, axis=1)
         for cc, m in zip(c, ids.ravel() if ids is not None else []) if int(m) in ref]
    return (float(np.median(np.concatenate(d))), len(d)) if d else (float("nan"), 0)


class TelHeight:
    """Ring-plane height from the live telemetry: tails the stream_log slot CSV (line-buffered) for `key` (Z, m) and
    returns ring_h + max(0, z), the truth4 plane. `ground` is z < ground_m. Older than stale_s (or no row yet): the
    fallback height (None = no plane, the frame gives no fix). `extra` columns (e.g. ano_of.earth_x/y for --align
    auto) are kept in `v`, read from the same rows."""

    def __init__(self, path, key: str = "Ctrler.Z_posPID.FB", ring_h_m: float = 0.057, ground_m: float = 0.03,
                 stale_s: float = 1.0, fallback_m: float | None = None, clock: Callable[[], float] = time.monotonic,
                 extra: tuple = ()):
        self.path, self.key, self.ring_h, self.ground_m = Path(path), key, ring_h_m, ground_m
        self.stale_s, self.fallback, self.clock = stale_s, fallback_m, clock
        self.f, self.col, self.buf, self.z, self.t_z = None, None, "", None, -1e9
        self.extra, self.cols, self.v = tuple(extra), {}, {}

    def poll(self) -> None:
        if self.f is None:
            if not self.path.exists():
                return
            self.f = self.path.open("r", newline="", encoding="utf-8")
        chunk = self.f.read()
        if not chunk:
            return
        *lines, self.buf = (self.buf + chunk).split("\n")
        for ln in lines:
            cells = ln.rstrip("\r").split(",")
            if self.col is None:
                for k in (self.key, *self.extra):
                    if k not in cells:
                        raise SystemExit(f"{k} not in {self.path}")
                self.col = cells.index(self.key)
                self.cols = {k: cells.index(k) for k in self.extra}
                continue
            try:
                z = float(cells[self.col])
                v = {k: float(cells[c]) for k, c in self.cols.items()}
            except (ValueError, IndexError):
                continue
            if np.isfinite(z):
                self.z, self.t_z, self.v = z, self.clock(), v

    @property
    def fresh(self) -> bool:
        return self.z is not None and self.clock() - self.t_z <= self.stale_s

    @property
    def ground(self) -> bool:
        return self.fresh and self.z < self.ground_m

    def vec(self, keys) -> np.ndarray | None:
        """The latest `extra` values (polled, fresh) as an array, else None."""
        self.poll()
        return np.array([self.v[k] for k in keys]) if self.fresh and all(k in self.v for k in keys) else None

    def __call__(self, t_cap: float) -> float:
        self.poll()
        if self.fresh:
            return self.ring_h + max(0.0, self.z)
        return float("nan") if self.fallback is None else self.fallback


class PadOrigin:
    """Fixes are pad-relative: the firmware innovation is z - (p + c) and earth_x/y zero at arming, on the spot the
    drone stands on. Until the first take-off the origin follows the median of the last n detections; then it is
    frozen for the session (no arm flag in the log: a re-arm elsewhere is the firmware re-anchor's job, n_anchor).
    ground None (no telemetry): the first n detections. Until the first detection nothing is sent."""

    def __init__(self, n: int = 20):
        self.n, self.hist, self.xy, self.frozen = n, [], None, False

    def apply(self, xy, ground: bool | None):
        if ground is None:
            ground = len(self.hist) < self.n
        if ground and not self.frozen:
            self.hist = (self.hist + [np.asarray(xy, float)])[-self.n:]
            self.xy = np.median(self.hist, axis=0)
        elif self.xy is not None:
            self.frozen = True
        return None if self.xy is None else np.asarray(xy, float) - self.xy


class Preview:
    """--show: the grabbed frame, the tracked ring, its pad-frame xy and the fix counters, redrawn at most `hz` per s
    at `scale` (resize + imshow + waitKey(1)). Placed right of (or below) the grab rect: a preview over the scrcpy
    window would be grabbed itself."""

    def __init__(self, hz: float = 10.0, scale: float = 0.6, rect: dict | None = None, title: str = "live_tracker"):
        self.cv2, self.dt, self.scale, self.title, self.t_last, self.ms = vt._cv2(), 1.0 / hz, scale, title, -1e9, 0.0
        self.n = 0
        self.cv2.namedWindow(title, self.cv2.WINDOW_AUTOSIZE)
        if rect:                                          # right of the grab rect if it fits on screen, else below
            import ctypes
            sw = ctypes.windll.user32.GetSystemMetrics(0)
            right = rect["left"] + rect["width"] + 8
            fits = right + scale * rect["width"] <= sw
            self.cv2.moveWindow(title, right if fits else rect["left"],
                                rect["top"] if fits else rect["top"] + rect["height"] + 40)

    def __call__(self, now: float, frame, uv, xy, lines) -> None:
        if now - self.t_last < self.dt:
            return
        p0 = time.perf_counter()
        cv2, s = self.cv2, self.scale
        img = cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_NEAREST)
        if uv is not None:
            c = (int(uv[0] * s), int(uv[1] * s))
            cv2.circle(img, c, 14, (0, 255, 0), 2)
            if xy is not None:
                cv2.putText(img, f"{xy[0] * 100:+.0f},{xy[1] * 100:+.0f} cm", (c[0] + 16, c[1]),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
        for i, txt in enumerate(lines):
            cv2.putText(img, txt, (8, 20 + 18 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(img, txt, (8, 20 + 18 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.imshow(self.title, img)
        cv2.waitKey(1)
        self.t_last, self.n, self.ms = now, self.n + 1, self.ms + 1000 * (time.perf_counter() - p0)


def udp_sender(spec: str) -> Callable[[bytes], None]:
    """'udp:PORT' or 'udp:HOST:PORT' -> send(bytes), one datagram per frame (stream_log --relay-port)."""
    parts = spec.split(":")
    if parts[0] != "udp" or len(parts) not in (2, 3):
        raise ValueError(f"--send wants udp:PORT or udp:HOST:PORT, got {spec!r}")
    addr = (parts[1] if len(parts) == 3 else "127.0.0.1", int(parts[-1]))
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return lambda b: s.sendto(bytes(b), addr)


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
    def fit(cls, cam_xy_m, earth_cm, mirror: bool | None = None) -> "EarthAlign":
        """Least-squares rotation + offset over paired samples (option b: the first 1 m out-and-back), both
        handednesses tried (or only `mirror`), the lower rms kept. Needs real motion: a hover alone leaves the
        rotation undecided, a straight line the handedness."""
        c = np.asarray(cam_xy_m, float); e = np.asarray(earth_cm, float)
        zc, ze = c[:, 0] + 1j * c[:, 1], (e[:, 0] + 1j * e[:, 1]) / 100.0
        best = None
        for mirror in ((False, True) if mirror is None else (mirror,)):
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


class AutoAlign:
    """--align auto. r6 flew every fusion run at the default --yaw-deg 0 while `fit` measured pad -> earth ~90 deg:
    the camera loop pushed sideways (no restoring force), the drift the operator held with RC in modes 2 and 3.
    Until locked the tracker sends mode 1 (shadow) whatever --mode says, and airborne fix/EKF pairs collect (earth =
    ano_of.earth_x/y, the raw EKF path in shadow). Every refit_s the last window_s are fitted (EarthAlign.fit, mirror
    held at the --mirror value: a straight out-and-back leaves it undecided). Lock: the camera path spans span_cm,
    scale and rms inside limits, and two fits in a row agree within agree_deg (limits PROPOSED). The offset is then
    set from the last hold_s so the fix equals the EKF now (e ~ 0, a bumpless switch) and `mode` becomes `target`."""

    def __init__(self, align: EarthAlign, target: int, span_cm: float = 80.0, max_rms_cm: float = 20.0,
                 scale: tuple = (0.6, 1.5), agree_deg: float = 15.0, window_s: float = 30.0, refit_s: float = 2.0,
                 hold_s: float = 1.0):
        self.align, self.target, self.span_cm, self.max_rms_cm, self.scale = align, target, span_cm, max_rms_cm, scale
        self.agree_deg, self.window_s, self.refit_s, self.hold_s = agree_deg, window_s, refit_s, hold_s
        self.mode, self.locked, self.prev, self.t_fit, self.t_lock = 1, False, None, -1e9, None
        self.rows: list = []
        self.status = "align WAIT airborne"

    def push(self, t: float, cam_xy_m, earth_cm) -> bool:
        """One airborne pair (pad m as fed to `align`, EKF earth cm); True on the push that locks."""
        if self.locked:
            return False
        self.rows.append((t, *np.asarray(cam_xy_m, float)[:2], *np.asarray(earth_cm, float)[:2]))
        while self.rows[0][0] < t - self.window_s:
            self.rows.pop(0)
        if t - self.t_fit < self.refit_s or len(self.rows) < 20:
            return False
        self.t_fit = t
        r = np.array(self.rows)
        cam, earth = r[:, 1:3], r[:, 3:5]
        span = float(np.ptp(cam, axis=0).max()) * 100.0
        if span < self.span_cm:
            self.prev, self.status = None, f"align WAIT span {span:.0f}/{self.span_cm:.0f} cm"
            return False
        f = EarthAlign.fit(cam, earth, mirror=self.align.mirror)
        ok = self.scale[0] <= f.scale <= self.scale[1] and f.rms_cm <= self.max_rms_cm
        prev, self.prev = self.prev, (f if ok else None)
        self.status = (f"align {np.degrees(np.angle(f.a)):.0f} deg scale {f.scale:.2f} rms {f.rms_cm:.0f} cm"
                       + ("" if ok else " REJ"))
        if not ok or prev is None or abs(np.degrees(np.angle(f.a / prev.a))) > self.agree_deg:
            return False
        h = r[:, 0] >= t - self.hold_s
        zc = cam[h, 0] + 1j * cam[h, 1]
        ze = earth[h, 0] + 1j * earth[h, 1]
        self.align.a, self.align.mirror, self.align.cam0 = f.a, f.mirror, 0j
        self.align.earth0 = complex(np.mean(ze - 100.0 * f.a * (zc.conjugate() if f.mirror else zc)))
        self.align.scale, self.align.rms_cm = f.scale, f.rms_cm
        self.locked, self.mode, self.t_lock = True, self.target, t
        self.status = f"align LOCK {np.degrees(np.angle(f.a)):.0f} deg -> mode {self.target}"
        return True


def shadow_pairs(stem: str, min_h_m: float = 0.15):
    """Shadow or live log -> (t_s, cam_m, earth_cm, run), one row per slot2 sample with a newly scored fix. ekf_of.c
    keeps e = z - (p + c) with p = ano_of.earth_x - a_x, so z = e + earth + (c - a) ~ e + earth is the fix as sent
    (cm), paired with the raw EKF path earth - c (a, the applied offset, is not logged; it trails c by <= 0.5 cm per
    tick). In shadow c = a = 0. Device clock only: no host sync. run numbers the airborne stretches (Z FB > min_h_m).
    On a live log the fit reads the residual rotation/scale left after --yaw-deg (0 deg, 1.0 if aligned)."""
    import pandas as pd
    s1 = pd.read_csv(f"{stem}.slot1.csv", usecols=["t_src_ms", "ano_of.earth_x", "ano_of.earth_y", "Ctrler.Z_posPID.FB"])
    s2 = pd.read_csv(f"{stem}.slot2.csv", usecols=["t_src_ms", "g_ext_pos.e_x", "g_ext_pos.e_y", "g_ext_pos.c_x",
                                                   "g_ext_pos.c_y", "g_ext_pos.n_ok", "g_ext_pos.n_rej"])
    t1, t2 = s1["t_src_ms"].to_numpy(float), s2["t_src_ms"].to_numpy(float)
    air = s1["Ctrler.Z_posPID.FB"].to_numpy(float) > min_h_m
    lab = np.cumsum(np.r_[air[0], air[1:] & ~air[:-1]]) * air         # 1, 2, ... per airborne stretch, 0 on ground
    run = lab[np.clip(np.searchsorted(t1, t2), 0, len(t1) - 1)]
    n = (s2["g_ext_pos.n_ok"] + s2["g_ext_pos.n_rej"]).to_numpy(float)
    k = np.r_[False, np.diff(n) > 0] & (run > 0)
    earth = np.c_[np.interp(t2, t1, s1["ano_of.earth_x"]), np.interp(t2, t1, s1["ano_of.earth_y"])]
    z = s2[["g_ext_pos.e_x", "g_ext_pos.e_y"]].to_numpy(float) + earth
    raw = earth - s2[["g_ext_pos.c_x", "g_ext_pos.c_y"]].to_numpy(float)
    return t2[k] / 1000.0, z[k] / 100.0, raw[k], run[k]


def offset_noise(t_s, z_cm, y_cm, dt_fix: float = 0.04):
    """One axis at successive fixes: z = camera (cm), y = camera - raw EKF path = the offset the firmware tracks
    (e + c). Camera sd from second differences (white noise: var = 6 sd^2, smooth motion cancels; MAD = robust).
    Offset drift = |y(t + 1 s) - y(t)| (cm/s). A constant gain K per fix lags a drift v by v dt (1 - K) / K and
    passes sd sqrt(K / (2 - K)) of camera noise: K is the grid minimum of their quadrature sum at the p90 drift.
    Returns (camera sd, drift p50, drift p90, K)."""
    z, y, t = (np.asarray(v, float) for v in (z_cm, y_cm, t_s))
    d2 = z[2:] - 2.0 * z[1:-1] + z[:-2]
    sd = 1.4826 * np.median(np.abs(d2 - np.median(d2))) / np.sqrt(6.0)
    g = np.arange(t[0], t[-1], dt_fix)
    v = np.abs(np.diff(np.interp(g, t, y)[::int(round(1.0 / dt_fix))]))
    p50, p90 = np.percentile(v, [50, 90])
    K = np.linspace(0.05, 1.0, 96)
    cost = (p90 * dt_fix * (1.0 - K) / K) ** 2 + sd ** 2 * K / (2.0 - K)
    return sd, p50, p90, float(K[np.argmin(cost)])


def fit_main(argv) -> int:
    ap = argparse.ArgumentParser(prog="live_tracker fit", description="Shadow run (sent at --yaw-deg 0) -> "
                                 "--yaw-deg/--mirror per airborne stretch, from the firmware's own innovations")
    ap.add_argument("stem", help="stream_log stem, e.g. logs/r5_shadow_3")
    ap.add_argument("--fit-s", type=float, default=30.0, help="also fit the first s of each stretch (EKF drift grows)")
    ap.add_argument("--min-h-m", type=float, default=0.15)
    ap.add_argument("--window-s", type=float, default=0.0, help="also fit back-to-back windows of s (yaw drift)")
    ap.add_argument("--auto", action="store_true", help="replay --align auto per stretch: lock time/yaw, residual")
    ap.add_argument("--span-cm", type=float, default=80.0, help="--align auto lock span (PROPOSED)")
    a = ap.parse_args(argv)
    t, cam, earth, run = shadow_pairs(a.stem, a.min_h_m)
    for r in np.unique(run):
        k = run == r
        if a.auto:
            aa = AutoAlign(EarthAlign(), target=2, span_cm=a.span_cm)
            i_lock = next((i for i in np.flatnonzero(k) if aa.push(t[i], cam[i], earth[i])), None)
            if i_lock is None:
                print(f"run {r} t {t[k][0]:.0f}-{t[k][-1]:.0f} s: no lock ({aa.status})")
            else:
                post = k & (t >= t[i_lock]) & (t < t[i_lock] + 30.0)
                res = np.array([aa.align.apply(c) for c in cam[post]]) - earth[post]
                print(f"run {r} t {t[k][0]:.0f}-{t[k][-1]:.0f} s: lock at +{t[i_lock] - t[k][0]:.1f} s {aa.status}  "
                      f"step {np.hypot(*res[0]):.1f} cm  next 30 s rms {np.sqrt((res ** 2).sum(1).mean()):.1f} cm "
                      f"(n {post.sum()})")
        wins = [(f"first {a.fit_s:.0f} s", k & (t <= t[k][0] + a.fit_s)), ("all", k)]
        if a.window_s > 0:
            wins += [(f"win {w:.0f} s", k & (t >= w) & (t < w + a.window_s))
                     for w in np.arange(t[k][0], t[k][-1], a.window_s)]
        for label, m in wins:
            if m.sum() < 20:
                continue
            span = float(np.ptp(cam[m], axis=0).max()) * 100.0
            try:
                f = EarthAlign.fit(cam[m], earth[m])
            except ValueError as e:
                print(f"run {r} {label}: {e}")
                continue
            print(f"run {r} t {t[m][0]:.0f}-{t[m][-1]:.0f} s {label:>10s}: n {m.sum():4d} span {span:4.0f} cm  "
                  f"yaw_deg {np.degrees(np.angle(f.a)):7.1f}  mirror {f.mirror}  scale {f.scale:.2f}  rms {f.rms_cm:5.1f} cm")
        if k.sum() < 50 or t[k][-1] - t[k][0] < 3.0:              # offset_noise needs >= 2 s of fixes
            continue
        y = cam[k] * 100.0 - earth[k]
        nz = [offset_noise(t[k], cam[k][:, i] * 100.0, y[:, i]) for i in (0, 1)]
        print(f"run {r} noise (fix dt {np.median(np.diff(t[k])) * 1e3:.0f} ms): " + "  ".join(
            f"{ax} cam sd {sd:3.1f} cm, offset drift p50 {p50:4.1f} p90 {p90:4.1f} cm/s, K {kk:.2f}"
            for ax, (sd, p50, p90, kk) in zip("xy", nz)))
    return 0


class FixScheduler:
    """One fix every `period` s from the last `n_med` detections inside that period: detections farther than
    `max_spread_m` from their median are dropped (a bad blob), and the fix is the median of the rest. Skipped when
    fewer than `min_in` remain or fewer than n_med arrived. `window_s` (default period) is how far back a detection
    may be: at 30 fps a 0.1 s period holds only 3 frames, so one dropped frame would skip the fix."""

    def __init__(self, period: float = 1.0, n_med: int = 5, max_spread_m: float = 0.05, min_in: int = 3,
                 window_s: Optional[float] = None):
        self.period, self.n_med, self.max_spread, self.min_in = period, n_med, max_spread_m, min(min_in, n_med)
        self.window = period if window_s is None else window_s
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
        recent = [d for d in self.buf if d[0] > now - self.window][-self.n_med:]
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


def tel_height(tel_npz, key: str = "slot0.Ctrler.Z_posPID.FB", tel_offset_s: float = 0.0,
               ring_offset_m: float = 0.0) -> Callable[[float], float]:
    """Plane height for frame time t: telemetry Z (m) at t - tel_offset_s, plus the ring's height above the Z
    reference. `tel_npz` holds `key` and `key|t` (the R3 tel_r3.npz layout). R3 used offset 15.05 s and ring
    offset 0.05 m; neither is measured (the offset came from a fit, the ring offset is a guess)."""
    d = np.load(tel_npz) if isinstance(tel_npz, str) else tel_npz
    tz, z = np.asarray(d[key + "|t"], float), np.asarray(d[key], float)
    return lambda t: float(np.interp(t - tel_offset_s, tz, z)) + ring_offset_m


def run_source(source, cam: dict, align: EarthAlign, height_m: float | Callable[[float], float],
               cam_latency_s: float = 0.0, send: Callable[[bytes], None] | None = None, out_csv: str | None = None,
               sched: FixScheduler | None = None, clock: Callable[[], float] | None = None, max_frames: int = 0, start_s: float = 0.0,
               static=None, ring=None, uv_log: list | None = None, gate: JumpGate | None = None,
               mode: int | None = None, delay_ms: float | None = None, fix_log: list | None = None,
               pad: PadOrigin | None = None, ground: Callable[[], bool] | None = None,
               show: Preview | None = None, stop_file: str | None = None, size: tuple | None = None,
               auto: AutoAlign | None = None, earth: Callable[[], np.ndarray | None] | None = None,
               air_m: float = 0.15) -> dict:
    """Read frames from `source` (file, device index or window:<title>), emit fixes. For a file the capture time is
    the frame pts and the send time is that pts too (age = camera latency only); live, both come from `clock`.
    `ring`: a pixel tracker with .step(frame) -> uv (RingTracker, CoralTracker), else the guard detector with
    `static` (a video_truth.static_mask). `gate` drops jumps (the tracker is re-centred on the last accepted uv).
    With `send`, the delay and mode frames go first and again every 2 s while live. `fix_log` collects
    (t_read, t_fix, frame bytes) per fix. `pad` makes the fixes pad-relative (`ground()`: telemetry on-ground flag,
    None = first detections); a NaN height (stale telemetry) gives no fix. `show` draws each frame (throttled).
    `auto` (--align auto): mode 1 is sent until it locks, fed each fix taken above `air_m` with a fresh `earth()`
    (EKF cm); on lock the mode frame goes out at once."""
    cv2 = vt._cv2()
    win = str(source).startswith("window:")
    cap = WindowSource(str(source)[7:], size=size) if win else cv2.VideoCapture(int(source) if str(source).isdigit() else source)
    live = win or str(source).isdigit()

    def hello():
        if send and delay_ms is not None:
            send(ext_pos_delay_frame(delay_ms))
        if send and mode is not None:
            send(ext_pos_mode_frame(auto.mode if auto else mode))

    while start_s and not live and cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0 < start_s and cap.grab():
        pass                                              # frame-exact; set(POS_MSEC) lands seconds off on scrcpy mkv
    clock = clock or time.monotonic
    sched = sched or FixScheduler()
    n, n_det, uv = 0, 0, None
    f_out = open(out_csv, "w", newline="", buffering=1) if out_csv else None   # line-buffered: a killed run keeps its fixes
    w_out = csv.writer(f_out) if f_out else None
    if w_out:
        w_out.writerow(["t_send", "t_cap", "pad_x_m", "pad_y_m", "earth_x_cm", "earth_y_cm", "frame_hex"])
    t_rd = t_det = 0.0
    hello()
    t_hello = clock()
    while True:
        if stop_file and n % 10 == 0 and Path(stop_file).exists():
            break
        p0 = time.perf_counter()
        ok, frame = cap.read()
        p1 = time.perf_counter()
        if not ok or (max_frames and n >= max_frames):
            break
        n += 1
        t_read = clock() if live else cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if live and t_read - t_hello >= 2.0:
            hello()
            t_hello = t_read
        t_cap = t_read - cam_latency_s
        z = height_m(t_cap) if callable(height_m) else height_m
        if ring is not None:
            uv = ring.step(frame)
            if uv_log is not None:
                uv_log.append((t_read, *(uv if uv is not None else (np.nan, np.nan))))
            xy = None if uv is None or not np.isfinite(z) else ring_xy(uv, cam, z)
        else:
            xy = detect_xy(frame, cam, z, static) if np.isfinite(z) else None
        if xy is not None and gate is not None and not gate.check(t_cap, xy, uv):
            if ring is not None and hasattr(ring, "recentre") and gate.last[2] is not None:
                ring.recentre(gate.last[2])
            xy = None
        if xy is not None and pad is not None:
            xy = pad.apply(xy, ground() if ground else None)
        t_rd, t_det = t_rd + (p1 - p0), t_det + (time.perf_counter() - p1)
        if show is not None:
            show(t_read, frame, uv, xy, [f"mode {auto.mode if auto else mode}  z {z:.2f} m  fixes {sched.n_sent}  det {n_det}/{n}",
                                         f"gate rej {gate.n_rej if gate else 0}  pad {'set' if pad is None or pad.xy is not None else 'WAIT'}"
                                         f"{' frozen' if pad is not None and pad.frozen else ''}"]
                 + ([auto.status] if auto else []))
        if xy is not None:
            n_det += 1
            sched.push(t_cap, xy)
        fix = sched.due(t_cap)                            # capture clock: a latency > period would empty the window
        if fix is None:
            continue
        t_fix, med = fix
        if auto is not None and not auto.locked and np.isfinite(z) and z >= air_m and earth is not None:
            ev = earth()
            if ev is not None and auto.push(t_read, med, ev):
                print(f"{auto.status} at t {t_read:.1f} s", flush=True)
                if send:
                    send(ext_pos_mode_frame(auto.mode))
        e = align.apply(med)
        frame_b = ext_pos_fix_frame(e[0], e[1], t_read - t_fix)
        if fix_log is not None:
            fix_log.append((t_read, t_fix, frame_b))
        if send:
            send(frame_b)
        if w_out:
            w_out.writerow([round(t_read, 3), round(t_fix, 3), round(med[0], 4), round(med[1], 4), round(e[0], 1),
                            round(e[1], 1), frame_b.hex()])
    cap.release()
    if f_out:
        f_out.close()
    return {"frames": n, "detections": n_det, "fixes": sched.n_sent, "skipped": sched.n_skip,
            "gate_rej": gate.n_rej if gate else 0, "reseeds": gate.reseeds if gate else 0,
            "read_ms": 1000 * t_rd / max(n, 1), "detect_ms": 1000 * t_det / max(n, 1),
            "show_ms": show.ms / max(show.n, 1) if show else 0.0,
            "pad_xy": None if pad is None or pad.xy is None else [round(float(v), 4) for v in pad.xy],
            "align": auto.status if auto else "fixed"}


REPO = Path(__file__).resolve().parents[2]
FW_DRIVER = REPO / "API" / "tests" / "ext_pos_replay_host.c"


def fw_build(out_dir: Path = REPO / "logs" / "fw_host") -> Path:
    """gcc the firmware camera-fix path (API/ekf_of.c) around FW_DRIVER; returns the exe."""
    out_dir.mkdir(parents=True, exist_ok=True)
    exe = out_dir / "ext_pos_replay.exe"
    cmd = ["gcc", "-std=c99", "-O2", "-ffp-contract=off", str(FW_DRIVER), str(REPO / "API" / "ekf_of.c"),
           "-I", str(REPO / "API"), "-I", str(REPO / "API" / "tests" / "stubs_wp14"), "-lm", "-o", str(exe)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(res.stderr)
    return exe


def fw_fusion(t, est, arr_t, idx, xy, delay_ms: float, exe: Path | None = None) -> tuple[np.ndarray, dict]:
    """The firmware mode-2 path (fw_build) on est (m, at t): 5 ms control ticks, fixes (xy m, encoded with
    ext_pos_encode, age byte idx) drained by the 10 ms command loop at the first tick >= arr_t, dated by the drone as
    drain - (idx * 10 + delay_ms). Returns est + a(t) and the firmware counters (as vt.ext_pos_replay)."""
    exe = exe or fw_build()
    shift = 100000 - int(np.floor(t[0] * 1000))                     # keep the uint32 clock well above 0
    ms = np.arange(int(np.ceil((t[0] * 1000 + shift) / 10)) * 10, int(t[-1] * 1000 + shift), 5)
    ts = (ms - shift) / 1000.0
    ex, ey = np.interp(ts, t, est[:, 0]) * 100, np.interp(ts, t, est[:, 1]) * 100
    drain = np.ceil((np.asarray(arr_t) * 1000 + shift) / 10).astype(np.int64) * 10
    fx = {}
    for d, i, (x, y) in zip(drain, idx, xy):
        fx.setdefault(int(d), []).append(f"F {int(d)} {int(i)} {int(ext_pos_encode(x * 100, y * 100))}")
    lines = [f"D {delay_ms:.3f}"]
    for m, a, b in zip(ms, ex, ey):
        lines += fx.get(int(m), [])
        lines.append(f"T {int(m)} {a:.4f} {b:.4f}")
    res = subprocess.run([str(exe)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True)
    rows = res.stdout.split("\n")
    tail = [r for r in rows if r.startswith("N ")][0].split()[1:]
    a = np.array([r.split() for r in rows if r.strip() and not r.startswith("N ")], float) / 100.0
    out = est + np.c_[np.interp(t, ts, a[:, 0]), np.interp(t, ts, a[:, 1])]
    return out, dict(zip(("ok", "gated", "old", "reanchor", "disagree"), map(int, tail)))


def replay(name: str, links=(0.15, 0.3), delay_err_s: float = 0.0, period: float = 0.05, n_med: int = 2,
           window_s: float = 0.1, gate: bool = True, cam_path: str = "docs/video-truth/cam_r4_lenswave_2026-10-11.json",
           root: str = "logs/video_truth", max_frames: int = 0, gain: float = vt.EXT_POS["gain"],
           slew_m: float = vt.EXT_POS["slew_m"], beta: float = vt.EXT_POS["beta"], fw: bool = False) -> dict:
    """Real-time replay of one R4 flight: the online CoralTracker (+ JumpGate) on cam.mkv, fixes packed exactly as
    sent, decoded as the firmware does (value -> cm, age byte), relative to the pad spot, arriving link s after
    the frame on the telemetry clock (truth4 offset); the drone dates each fix with delay_ms = link + delay_err_s.
    vt.ext_pos_replay on the earth estimate, scored by vt.drift_score (tag _live_<ms>ms). CIRCULAR: same camera,
    lens, floor pose, sync and earth rotation as truth4 (the rotation is an oracle the live run must fit).
    Fix rate default 20 Hz (period 0.05, median of 2, 0.1 s window; PROPOSED 2026-10-12): 10 Hz missed the budget on
    the b16 trajectory. Score tags carry _pNNNnNwNNN unless the rate is the old 10 Hz (0.1 / 3 / 0.2).
    fw: the firmware C path (fw_fusion: API/ekf_of.c, 5 ms ticks, 10 ms command drain, float32; the built-in
    gain/slew/spread, so gain/slew_m/beta must stay default) instead of the Python mirror; tag _fw."""
    vd = Path(root) / name
    summ = json.loads((vd / "truth4.json").read_text())
    tab = np.genfromtxt(vd / "truth4.csv", delimiter=",", names=True)
    video = str(vd / "cam.mkv")
    cam = vt.load_cam(cam_path, vt.video_size(video))
    scan = dict(np.load(vd / "aruco.npz"))
    R, tw, _ = vt.floor_from_marker(scan, cam)
    cam = dict(cam, R=R, t=tw)
    o, h, tt, z = summ["offset_s"], summ["h_ring_m"], tab["t_tel"], tab["z"]
    seed = vt.static_corners(scan["c0"])[0].mean(0)
    fixes: list = []
    st = run_source(video, cam, EarthAlign(), lambda tv: h + max(0.0, float(np.interp(tv + o, tt, z, left=0, right=0))),
                    sched=FixScheduler(period, n_med, window_s=window_s), max_frames=max_frames,
                    ring=CoralTracker(seed, vt.coral_static(video)), gate=JumpGate() if gate else None, fix_log=fixes)
    t_read = np.array([f[0] for f in fixes])
    idx = np.array([f[2][3] for f in fixes])
    age = idx * 0.01
    xy = np.array([ext_pos_decode(struct.unpack("<f", bytes(f[2][4:8]))[0]) for f in fixes], float) / 100.0
    pad = np.array([f[1] for f in fixes]) < summ["takeoff_video_s"] - 1.0
    xy -= np.median(xy[pad], axis=0)                      # truth4 is relative to the pad spot too
    earth = np.c_[tab["earth_x"], tab["earth_y"]]
    out = {"name": name, "tracker": st, "circular": True, "delay_err_s": delay_err_s, "gain": gain,
           "slew_cm": slew_m * 100, "spread_ms": vt.EXT_POS["spread_s"] * 1000, "beta": beta, "period_s": period, "n_med": n_med, "window_s": window_s, "fw": fw, "links": {}}
    sfx = vt.ext_pos_sfx(gain, slew_m, vt.EXT_POS["spread_s"], beta)
    if (period, n_med, window_s) != (0.1, 3, 0.2):
        sfx += f"_p{round(period * 1000):03d}n{n_med}w{round(window_s * 1000):03d}"
    if fw:
        if (gain, slew_m, beta) != (vt.EXT_POS["gain"], vt.EXT_POS["slew_m"], vt.EXT_POS["beta"]):
            raise ValueError("fw runs the built-in API/ekf_of.h constants; keep gain/slew/beta default")
        sfx += "_fw"
        exe = fw_build()
    if delay_err_s:
        sfx += f"_de{'p' if delay_err_s > 0 else 'm'}{round(abs(delay_err_s) * 1000):03d}"
    for L in links:
        arr = t_read + o + L
        if fw:
            fused, n = fw_fusion(tt, earth, arr, idx, xy, (L + delay_err_s) * 1000, exe)
        else:
            fused, n = vt.ext_pos_replay(tt, earth, arr - age - (L + delay_err_s), xy, 0.0,
                                         dict(vt.EXT_POS, gain=gain, slew_m=slew_m, beta=beta), arr_t=arr)
        r = vt.drift_score(name, ests={"earth": earth, "fused": fused}, tag=f"_live_{int(L * 1000)}ms{sfx}")
        out["links"][L] = {"counts": n, "est": {k: {q: e.get(q) for q in ("rms_cm", "drift60_p90_cm", "drift60s_p90_cm",
                                                                         "hf_rms_cm")}
                                               for k, e in r["est"].items()}}
    return out


def replay_main(argv) -> int:
    ap = argparse.ArgumentParser(prog="live_tracker replay", description=replay.__doc__)
    ap.add_argument("names", nargs="+", help="logs/video_truth/<name> dirs with truth4.csv")
    ap.add_argument("--link", type=float, nargs="+", default=[0.15, 0.3], help="frame -> drone delay(s), s")
    ap.add_argument("--delay-err", type=float, default=0.0, help="delay_ms the drone uses minus the true link, s")
    ap.add_argument("--period", type=float, default=0.05, help="s between fixes (20 Hz, PROPOSED)")
    ap.add_argument("--n-med", type=int, default=2)
    ap.add_argument("--window-s", type=float, default=0.1)
    ap.add_argument("--no-gate", action="store_true")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--gain", type=float, default=vt.EXT_POS["gain"], help="c step per accepted fix, x innovation")
    ap.add_argument("--slew-cm", type=float, default=vt.EXT_POS["slew_m"] * 100, help="max c step per fix, cm")
    ap.add_argument("--beta", type=float, default=vt.EXT_POS["beta"], help="offset-rate gain per fix (0 = off)")
    ap.add_argument("--fw", action="store_true", help="run the firmware C path (gcc API/ekf_of.c), tag _fw")
    a = ap.parse_args(argv)
    for nm in a.names:
        r = replay(nm, a.link, a.delay_err, a.period, a.n_med, a.window_s, not a.no_gate, max_frames=a.max_frames,
                   gain=a.gain, slew_m=a.slew_cm / 100, beta=a.beta, fw=a.fw)
        print(json.dumps(r, default=float))
    return 0


def main(argv=None) -> int:
    import sys
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["replay"]:
        return replay_main(argv[1:])
    if argv[:1] == ["fit"]:
        return fit_main(argv[1:])
    ap =argparse.ArgumentParser(description="Live camera tracker -> CMD 0x2C fixes (dry run without --send). "
                                             "`replay <names>`: online tracker + fusion replay on R4 videos.")
    ap.add_argument("--source", required=True, help="video file, a capture device index, or window:<title> (scrcpy)")
    ap.add_argument("--cam", required=True, help="floor pose json (e.g. docs/video-truth/cam_r3_selfcal_2026-10-11.json)")
    ap.add_argument("--height-m", type=float, default=None, help="fixed plane height above the floor, m")
    ap.add_argument("--tel-npz", default="", help="telemetry npz with Z (tel_r3.npz layout); replaces --height-m")
    ap.add_argument("--tel-offset-s", type=float, default=0.0, help="video time - telemetry time; R3: 15.05 (fit)")
    ap.add_argument("--ring-offset-m", type=float, default=0.05, help="ring above the Z reference; R3 guess, UNVERIFIED")
    ap.add_argument("--cam-latency-s", type=float, default=0.0, help="photon -> frame read; UNMEASURED until R4")
    ap.add_argument("--yaw-deg", type=float, default=0.0, help="pad -> earth rotation until a fit or marker exists")
    ap.add_argument("--mirror", action="store_true")
    ap.add_argument("--align", choices=["fixed", "auto"], default="fixed",
                    help="auto: send mode 1 and fit the rotation in flight (fly ~1 m out-and-back + sideways), then "
                         "switch to --mode bumplessly; needs --tel-csv with ano_of.earth_x/y")
    ap.add_argument("--align-span-cm", type=float, default=80.0, help="--align auto: camera path span to lock (PROPOSED)")
    ap.add_argument("--align-min-h-m", type=float, default=0.15, help="--align auto: only fixes above this height")
    ap.add_argument("--out", required=True, help="fixes CSV")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--start-s", type=float, default=0.0, help="file only: seek before reading")
    ap.add_argument("--detector", choices=["ring", "coral", "guards"], default="ring",
                    help="ring: R3 red ring (default); coral: R4 guard ring (needs --seed-px live)")
    ap.add_argument("--period", type=float, default=0.05, help="s between fixes (20 Hz, PROPOSED; 10 Hz missed on b16)")
    ap.add_argument("--n-med", type=int, default=2)
    ap.add_argument("--window-s", type=float, default=0.1)
    ap.add_argument("--gate", action="store_true", help="JumpGate on detections (PROPOSED thresholds)")
    ap.add_argument("--send", default="", help="udp:PORT (stream_log --relay-port) or udp:HOST:PORT; default: no send")
    ap.add_argument("--mode", type=int, choices=[0, 1, 2, 3], default=None,
                    help="EXT_POS mode frame: 0 off, 1 shadow, 2 offset outside the EKF, 3 in-EKF fix")
    ap.add_argument("--delay-ms", type=float, default=None, help="0xFE frame: firmware back-dating delay (measure it)")
    ap.add_argument("--seed-px", default="", help="u,v of the drone on the pad; default the ArUco id0 centre")
    ap.add_argument("--floor", default="", help="logs/video_truth/<dir> of an earlier capture, same phone placement: "
                    "floor pose + pad seed from its aruco.npz id0 (replaces the cam json R, t)")
    ap.add_argument("--tel-csv", default="", help="live height: tail this stream_log slot CSV (line-buffered)")
    ap.add_argument("--tel-key", default="Ctrler.Z_posPID.FB")
    ap.add_argument("--ring-h-m", type=float, default=0.057, help="ring plane on the pad (r5_marks_1 truth4 h_ring_m)")
    ap.add_argument("--tel-stale-s", type=float, default=1.0, help="older telemetry: --height-m, or no fix")
    ap.add_argument("--no-pad-origin", action="store_true", help="send floor-frame xy (default: pad-relative)")
    ap.add_argument("--show", action="store_true", help="preview window (frame, ring, xy, counters), throttled")
    ap.add_argument("--stop-file", default="", help="end the run cleanly when this file appears (camera_truth.sh stop)")
    ap.add_argument("--static-t", default="", help='file only, "90,140,190": mask guard-colour pixels present at all these times')
    a = ap.parse_args(argv)
    if a.align == "auto" and not (a.tel_csv and a.mode in (2, 3)):
        ap.error("--align auto needs --tel-csv and --mode 2 or 3")
    if a.tel_npz and a.tel_csv or not (a.tel_npz or a.tel_csv or a.height_m is not None):
        ap.error("give --tel-csv (with an optional --height-m fallback), --tel-npz or --height-m")
    ek = ("ano_of.earth_x", "ano_of.earth_y") if a.align == "auto" else ()
    tel = TelHeight(a.tel_csv, a.tel_key, a.ring_h_m, stale_s=a.tel_stale_s, fallback_m=a.height_m,
                    extra=ek) if a.tel_csv else None
    height = tel or (tel_height(a.tel_npz, tel_offset_s=a.tel_offset_s, ring_offset_m=a.ring_offset_m)
                     if a.tel_npz else a.height_m)
    src_live = str(a.source).isdigit() or a.source.startswith("window:")
    size = (WindowSource(a.source[7:]).ready() if a.source.startswith("window:")
            else None if str(a.source).isdigit() else vt.video_size(a.source))
    cam = vt.load_cam(a.cam, size)
    seed = [float(x) for x in a.seed_px.split(",")] if a.seed_px else None
    s = 1.0
    if a.floor:
        R, t, c0, w_ref, ref = floor_from_dir(a.floor, a.cam)
        cam, s = dict(cam, R=R, t=t), (size[0] / w_ref if size else 1.0)
        seed = seed if seed is not None else list(c0 * s)
        if a.source.startswith("window:"):
            ok, f0 = WindowSource(a.source[7:], size=size).read()
            d, k = floor_shift_px(f0, {i: c for i, c in ref.items() if i}, s) if ok else (float("nan"), 0)
            print(f"floor check: {k} markers, median shift {d:.1f} px" + ("  WARNING phone moved? (> 5 px, PROPOSED)"
                                                                         if d > 5 else ""), flush=True)
    align = EarthAlign(a=complex(np.exp(1j * np.radians(a.yaw_deg))), mirror=a.mirror)
    auto = AutoAlign(align, a.mode, span_cm=a.align_span_cm) if a.align == "auto" else None
    times = [float(x) for x in a.static_t.split(",") if x]
    static = vt.static_mask(a.source, times) if times and not src_live else None
    ring = None
    if a.detector in ("ring", "coral"):
        if seed is None:
            with open(a.cam) as f:
                seed = np.mean(json.load(f)["aruco_id0_corners_px"], axis=0)
        ring = RingTracker(seed) if a.detector == "ring" else CoralTracker(   # track_coral sizes are 1920 px
            seed, None if src_live else vt.coral_static(a.source), roi=140.0 * s, min_px=max(10, round(40 * s * s)))
    show = Preview(rect=WindowSource(a.source[7:]).rect() if a.source.startswith("window:") else None) if a.show else None
    print(run_source(a.source, cam, align, height, a.cam_latency_s, udp_sender(a.send) if a.send else None, a.out,
                     sched=FixScheduler(a.period, a.n_med, window_s=a.window_s), max_frames=a.max_frames,
                     start_s=a.start_s, static=static, ring=ring, gate=JumpGate() if a.gate else None,
                     mode=a.mode, delay_ms=a.delay_ms, pad=None if a.no_pad_origin else PadOrigin(),
                     ground=(lambda: tel.ground if tel.fresh else None) if tel else None, show=show,
                     stop_file=a.stop_file or None, size=size if a.source.startswith("window:") else None,
                     auto=auto, earth=(lambda: tel.vec(ek)) if auto else None,
                     air_m=a.ring_h_m + a.align_min_h_m), flush=True)          # z there is the ring plane
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
