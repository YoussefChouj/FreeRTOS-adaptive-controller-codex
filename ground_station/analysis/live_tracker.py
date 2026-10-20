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
import time
import http.client
import json
import socket
import struct
import subprocess
import cv2
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

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
    miss grows the ROI 1.5x per frame up to the whole frame.
    Live there is no whole video to learn `static` from, so with learn_n > 0 it is learned from the start: coral in
    >= frac of learn_n frames taken every learn_every frames (drone on the pad; a keep_r disc, 1.3 roi by default, around
    the seed is the drone and stays out), grown by grow_px, as vt.coral_static does. Without it a coral-coloured object near
    the path held the r6_f4 fixes still at one spot for 120 s (nightly-2026-10-13 item 1).
    `scenery` (coral with the pad clear: vt.coral_static of the floor scan's video, frame-sized) splits the keep disc: a
    pixel static at the start that is also scenery is learned static there too. Lab 10-18: the fire station printed on
    the mat next to the pad sat inside the keep disc and the ROI, pulled every ground dot ~55 px off the drone, and the
    pad origin frozen from those dots turned it into a steady ~30 cm fix offset in flight.
    While the start frames are learned the scenery masks the ROI too, and `learned` flips once they are done (the ROI is
    then re-centred on the seed, keep_r wide). Lab 10-19: before that the print pulled the first ~5 s of dots onto a
    guard corner, the ROI stayed there and the trail kept a line from the corner to the drone."""

    def __init__(self, seed_uv, static=None, roi: float = 140.0, min_px: int = 40,   # track_coral defaults
                 learn_n: int = 0, learn_every: int = 5, frac: float = 0.7, grow_px: int = 7,
                 keep_r: float | None = None, zoom: float = 1.0, land_zoom: float = 2.0, land_z_m: float = 0.30,
                 scenery=None):
        self.c, self.static, self.roi, self.min_px = np.asarray(seed_uv, float), static, float(roi), min_px
        self.r = self.roi
        self.seed0, self.learn_n, self.learn_every, self.frac, self.grow = self.c.copy(), learn_n, learn_every, frac, grow_px
        self.keep_r = 1.3 * self.roi if keep_r is None else float(keep_r)   # r6_f4 pad: drone coral to ~170 px of 1920
        self.zoom, self.land_zoom, self.land_z_m, self.active_zoom = float(zoom), float(land_zoom), float(land_z_m), 1.0
        self.last_s, self.scenery, self.learned = {}, scenery, False
        self._acc, self._k, self._i = None, 0, 0

    def _learn(self, frame_bgr) -> None:
        cv2 = vt._cv2()
        self._i += 1
        if (self._i - 1) % self.learn_every:
            return
        m = vt.coral_mask(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)).astype(np.uint16)
        self._acc, self._k = (m if self._acc is None else self._acc + m), self._k + 1
        if self._k < self.learn_n:
            return
        st = (self._acc >= self.frac * self._k).astype(np.uint8)
        yy, xx = np.ogrid[:st.shape[0], :st.shape[1]]
        keep = (xx - self.seed0[0]) ** 2 + (yy - self.seed0[1]) ** 2 <= self.keep_r ** 2
        kept = 0
        if self.scenery is not None and self.scenery.shape == st.shape:
            kept = int((st.astype(bool) & keep & self.scenery).sum())
            keep &= ~self.scenery                      # coral there with the pad clear too: scenery, not the drone
        elif self.scenery is not None:
            print(f"coral scenery ignored: {self.scenery.shape} vs frame {st.shape}", flush=True)
        st[keep] = 0
        learned = cv2.dilate(st, np.ones((self.grow, self.grow), np.uint8)) > 0
        self.static = learned if self.static is None else self.static | learned
        self.learn_n, self._acc, self.learned = 0, None, True
        self.c, self.r = self.seed0.copy(), self.keep_r   # the drone is on the pad: drop a ROI the scenery dragged off
        print(f"coral static learned: {int(learned.sum())} px from {self._k} frames"
              + (f" ({kept} px near the pad kept as floor-scan scenery)" if self.scenery is not None else ""), flush=True)

    def step(self, frame_bgr, z_agl: float | None = None) -> Optional[np.ndarray]:
        cv2 = vt._cv2()
        if self.learn_n:
            self._learn(frame_bgr)
        H, W = frame_bgr.shape[:2]
        z_eff = self.land_zoom if (z_agl is not None and np.isfinite(z_agl) and 0.0 < z_agl <= self.land_z_m) else self.zoom
        z_eff = max(1.0, float(z_eff))
        self.active_zoom = z_eff
        u0, v0 = int(max(0, self.c[0] - self.r)), int(max(0, self.c[1] - self.r))
        u1, v1 = int(min(W, self.c[0] + self.r)), int(min(H, self.c[1] + self.r))
        if u1 > u0 and v1 > v0:
            t0 = time.perf_counter()
            crop = frame_bgr[v0:v1, u0:u1]
            if z_eff > 1.01:
                crop = cv2.resize(crop, (0, 0), fx=z_eff, fy=z_eff, interpolation=cv2.INTER_LINEAR)
            t1 = time.perf_counter()
            self.last_s["crop"] = t1 - t0
            m = vt.coral_mask(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV))
            st_crop = None if self.static is None else self.static[v0:v1, u0:u1]
            if self.learn_n and self.scenery is not None and self.scenery.shape == (H, W):   # start frames: no static yet
                sc = self.scenery[v0:v1, u0:u1]
                st_crop = sc if st_crop is None else st_crop | sc
            if st_crop is not None:
                if z_eff > 1.01:
                    st_crop = cv2.resize(st_crop.astype(np.uint8), (m.shape[1], m.shape[0]),
                                         interpolation=cv2.INTER_NEAREST) > 0
                m &= ~st_crop
            vv, uu = np.nonzero(m)
            if len(uu) >= self.min_px * (z_eff * z_eff):
                self.c = np.array([(np.median(uu) + 0.5) / z_eff - 0.5 + u0,
                                   (np.median(vv) + 0.5) / z_eff - 0.5 + v0])
                self.r = self.roi
                self.last_s["detect"] = time.perf_counter() - t1
                return self.c.copy()
            self.last_s["detect"] = time.perf_counter() - t1
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
                 cluster_m: float = 0.05, lost_s: float = 1.0):
        self.v_max, self.slack, self.dt_max, self.n_reseed, self.cluster = v_max, slack_m, dt_max, n_reseed, cluster_m
        self.lost_s = lost_s       # 2026-10-15 lab: a static wrong blob won 10 frames in a row and the track ping-ponged
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
        if len(r) >= self.n_reseed and t - self.last[0] >= self.lost_s and np.all(np.linalg.norm(r - np.median(r, axis=0), axis=1) <= self.cluster):
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
    PrintWindow(PW_CLIENTONLY | PW_RENDERFULLCONTENT) copies the client area even under other windows (2026-10-13 on
    scrcpy direct3d11: 18 ms median vs mss 32 ms, 0/39 identical frame pairs while covered), so --show can cover
    the raw mirror; mss (screen rectangle) is the fallback. The window must show the whole video at its aspect (no
    letterbox bars), or the K rescale in load_cam is wrong. cv2.VideoCapture interface: read() -> (ok, bgr), release()."""

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
        self.via, self._gdi = "", None                    # via: "print" (occlusion-proof) or "mss" (last read)
        vp, g32 = ctypes.c_void_p, ctypes.windll.gdi32
        self.g32 = g32
        for f, at, rt in [(self.u32.GetDC, [vp], vp), (self.u32.ReleaseDC, [vp, vp], ctypes.c_int),
                          (g32.CreateCompatibleDC, [vp], vp), (g32.CreateCompatibleBitmap, [vp, ctypes.c_int, ctypes.c_int], vp),
                          (g32.SelectObject, [vp, vp], vp), (g32.DeleteObject, [vp], ctypes.c_int), (g32.DeleteDC, [vp], ctypes.c_int),
                          (self.u32.PrintWindow, [vp, vp, ctypes.c_uint], ctypes.c_int),
                          (g32.GetDIBits, [vp, vp, ctypes.c_uint, ctypes.c_uint, vp, vp, ctypes.c_uint], ctypes.c_int)]:
            f.argtypes, f.restype = at, rt

    def _free(self) -> None:
        if self._gdi:
            self.g32.DeleteObject(self._gdi[3])
            self.g32.DeleteDC(self._gdi[2])
            self._gdi = None

    def _print(self, w: int, h: int):
        """Client area via PrintWindow into a 32-bit top-down DIB, or None (then read() falls back to mss)."""
        if not self._gdi or self._gdi[:2] != (w, h) or self._gdi[6] != self.hwnd:
            self._free()
            dc = self.u32.GetDC(self.hwnd)
            mdc = self.g32.CreateCompatibleDC(dc)
            bmp = self.g32.CreateCompatibleBitmap(dc, w, h)
            self.u32.ReleaseDC(self.hwnd, dc)
            self.g32.SelectObject(mdc, bmp)
            bi = (self._ct.c_uint32 * 10)(40, w, (-h) & 0xFFFFFFFF, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
            self._gdi = (w, h, mdc, bmp, bi, np.empty((h, w, 4), np.uint8), self.hwnd)
        _, _, mdc, bmp, bi, buf, _ = self._gdi
        if not self.u32.PrintWindow(self.hwnd, mdc, 3):   # PW_CLIENTONLY | PW_RENDERFULLCONTENT (Win 8.1+)
            return None
        if self.g32.GetDIBits(mdc, bmp, 0, h, buf.ctypes.data, self._ct.byref(bi), 0) != h:
            return None
        return buf[..., :3]

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
                f = self._print(r["width"], r["height"])
                self.via = "mss" if f is None else "print"
                if f is None and r.get("left", 0) >= 0:   # mss reads the screen: scrcpy parked off screen (x=-3000) is black there
                    f = np.asarray(self.sct.grab(r))[..., :3]
                if f is not None and float(f.mean()) > 2.0:   # 2026-10-15 lab: black frames when PrintWindow failed once
                    return True, fit_frame(np.ascontiguousarray(f), self.fixed)
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

    def outer(self) -> dict:
        r = self._wt.RECT()
        self.u32.GetWindowRect(self.hwnd, self._ct.byref(r))
        return {"left": r.left, "top": r.top, "width": r.right - r.left, "height": r.bottom - r.top}

    def release(self) -> None:
        self._free()
        self.sct.close()


def floor_from_dir(video_dir, cam_path) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, dict]:
    """Floor pose of a fixed phone from an earlier capture with the same placement: id0 median pose in its aruco.npz
    (vt.floor_from_marker; R, t do not depend on the image scale). Returns R, t, the id0 centre (px of that video),
    the video width (to rescale pixels to the live window) and the scan's static corners {id: (4, 2) px}."""
    vd = Path(video_dir)
    size = vt.video_size(vt.video_file(vd))
    scan = dict(np.load(vd / "aruco.npz"))
    R, t, _ = vt.floor_from_marker(scan, vt.load_cam(cam_path, size))
    ref = {int(k[1:]): vt.static_corners(scan[k])[0] for k in scan if k.startswith("c")}
    return R, t, ref[0].mean(0), float(size[0]), {k: v for k, v in ref.items() if v is not None}


def floor_scenery(video_dir) -> Optional[np.ndarray]:
    """Coral scenery with the pad clear (the `floor` scan needs the pad marker in view, so the drone is off the pad):
    vt.coral_static of the scan's cam.mkv, frame-sized bool. Cached next to it as a PNG whose name carries the coral
    thresholds (a threshold change computes it again) and redone when the video is newer (a re-scan); computing takes
    ~16 s on a 10 s scan, so camera_truth.sh floor makes it at scan time. None when the scan has no video."""
    cv2 = vt._cv2()
    d = Path(video_dir)
    mkv, png = d / "cam.mkv", d / ("coral_scenery_" + "-".join(map(str, (*vt.CORAL_LO, *vt.CORAL_HI))) + ".png")
    if png.exists() and (not mkv.exists() or png.stat().st_mtime >= mkv.stat().st_mtime):
        return cv2.imread(str(png), cv2.IMREAD_GRAYSCALE) > 0
    if not mkv.exists():
        return None
    m = vt.coral_static(str(mkv))
    cv2.imwrite(str(png), m.astype(np.uint8) * 255)
    return m


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
    returns ring_h + max(0, z), the truth4 plane. `ground` is z < z_min + ground_m, z_min the lowest z seen: r7_f2 read
    z 0.050 m on the pad (r7_f1 0.000), so an absolute z < ground_m never held, PadOrigin never set and 0 fixes were sent. Older than stale_s (or no row yet): the
    fallback height (None = no plane, the frame gives no fix). `extra` columns (e.g. ano_of.earth_x/y for --align
    auto) are kept in `v`, read from the same rows."""

    def __init__(self, path, key: str = "Ctrler.Z_posPID.FB", ring_h_m: float = 0.057, ground_m: float = 0.03,
                 stale_s: float = 1.0, fallback_m: float | None = None, clock: Callable[[], float] = time.monotonic,
                 extra: tuple = ()):
        self.path, self.key, self.ring_h, self.ground_m = Path(path), key, ring_h_m, ground_m
        self.stale_s, self.fallback, self.clock = stale_s, fallback_m, clock
        self.f, self.col, self.buf, self.z, self.t_z, self.z_min = None, None, "", None, -1e9, float("inf")
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
                self.z, self.t_z, self.v, self.z_min = z, self.clock(), v, min(self.z_min, z)

    @property
    def fresh(self) -> bool:
        return self.z is not None and self.clock() - self.t_z <= self.stale_s

    @property
    def ground(self) -> bool:
        return self.fresh and self.z < self.z_min + self.ground_m

    def vec(self, keys) -> np.ndarray | None:
        """The latest `extra` values (polled, fresh) as an array, else None."""
        self.poll()
        return np.array([self.v[k] for k in keys]) if self.fresh and all(k in self.v for k in keys) else None

    def __call__(self, t_cap: float) -> float:
        self.poll()
        if self.fresh:
            return self.ring_h + max(0.0, self.z)
        return float("nan") if self.fallback is None else self.fallback


class TelHeightHttp(TelHeight):
    """TelHeight fed by the 8081 service (GET /api/values) instead of a slot CSV: with 8081 up the campaign capture
    owns the logging, its CSV name is not known before the flight and PlainWriter flushes about once a second. Same
    z, ground and `extra` values; the sample age is the service's. Polled at most every min_dt_s."""

    def __init__(self, url: str, *args, min_dt_s: float = 0.02, timeout_s: float = 1.0, **kw):
        super().__init__(url, *args, **kw)
        self.url, self.min_dt, self.timeout = url.rstrip("/"), min_dt_s, timeout_s
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # the system proxy 502s 127.0.0.1:
                                                                                    # r9_a1 sent 0 fixes (2026-10-14)
        self.worker, self.got = None, None

    # The request runs on its own thread: a 0.1 s timeout inside the frame loop gave up on a busy 8081 and left
    # 19 ConnectionAborted in svc8081.log (2026-10-14); waiting longer there would stall the frames instead.
    def _loop(self, q: str) -> None:
        while True:
            t0 = time.monotonic()
            try:
                with self.http.open(f"{self.url}/api/values?names={q}", timeout=self.timeout) as r:
                    self.got = (json.load(r)["values"], self.clock())
            except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException):  # IncompleteRead is no OSError
                pass
            time.sleep(max(0.0, self.min_dt - (time.monotonic() - t0)))

    def poll(self) -> None:
        if self.worker is None:
            q = urllib.parse.quote(",".join((self.key, *self.extra)), safe=",")
            self.worker = threading.Thread(target=self._loop, args=(q,), daemon=True)
            self.worker.start()
        got, self.got = self.got, None
        if got is None:
            return
        vals, t_recv = got
        try:
            z, age = float(vals[self.key]["value"]), float(vals[self.key]["age_s"])
        except (ValueError, KeyError, TypeError):
            return
        if np.isfinite(z):
            self.z, self.t_z, self.z_min = z, t_recv - age, min(self.z_min, z)
            self.v = {k: float(vals[k]["value"]) for k in self.extra if k in vals}


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


def traj_xy(spec: str) -> np.ndarray:
    """--traj: a preset trajectory_pipeline shape as 'circle:radius_m=0.5' (k=v floats, comma-separated) or a .json
    {"shape": ..., "params": {...}} -> closed (N, 2) xy in metres from the start point (shape_xy starts at 0,0), drawn
    from the drift anchor on the --show map (the pad origin until the first fix)."""
    from ground_station.service.trajectory_pipeline import shape_xy
    if spec.endswith(".json"):
        with open(spec) as f:
            d = json.load(f)
        shape, params = d["shape"], d.get("params", {})
    else:
        shape, _, kv = spec.partition(":")
        params = {k: float(v) for k, v in (p.split("=") for p in kv.split(",") if p)}
    return np.asarray(shape_xy(shape, params), float)


def pip_label(z_eff: float, z_m: float) -> str:
    """Pure function formatting the landing zoom PiP text."""
    return f"LAND ZOOM {z_eff:.1f}x z={z_m*100:.0f}cm"


class Preview:
    """--show: the tracker window, the only always-on-top window (the scrcpy mirror sits off screen: the PrintWindow
    grab still reads it). Left: the grabbed frame, the tracked ring, its persistent uv trail, landing zoom PiP and
    counters. Right: a 3D pad-frame trajectory viewer (x, y, z) that automatically centers the 3D path and selects
    the best viewing angle (broadside to principal horizontal axis, elevation adapted to z-vs-xy span), with 0.5 m
    floor grid, ground shadow, vertical drop-stems, preset trajectory and drift pass circle. Keys (window focused):
    r record on/off, a anchor here, c clear trails, v cycle 3D view (auto/orbit/iso/top), z toggle zoom PiP, f full."""

    KEYS = "keys: r rec  a anchor  c clear  v 3d-view  z zoom  f full"
    HOLD_S = 0.5      # r7_f6: 3056 detections in 3970 frames, so ~1 draw in 4 had no ring (it blinked)
    VIEWS = ("auto", "orbit", "iso", "top")

    def __init__(self, hz: float = 30.0, scale: float = 0.6, rect: dict | None = None, title: str = "live_tracker",
                 traj: np.ndarray | None = None, rec: str | None = None, pass_cm: float = 20.0, trail_s: float = 0.0,
                 width: int | None = None, headless: bool = False):
        self.cv2, self.dt, self.scale, self.title, self.t_last, self.ms = vt._cv2(), 1.0 / hz, scale, title, -1e9, 0.0
        self.width = width    # drawn frame width px (the grab keeps its full size for the tracker); None: `scale`
        self.hz, self.rect, self.traj, self.pass_m, self.trail_s, self.n = hz, rect, traj, pass_cm / 100.0, trail_s, 0
        self.trail, self.uvs, self.anchor, self.max_d, self.xy, self.z = [], [], None, 0.0, None, 0.0
        self.z_max, self.view_idx, self.zoom_pip = 0.0, 0, False
        self.active_zoom = 1.0
        self._az, self._el, self._center = -38.0, 30.0, None
        self.rec, self.recording, self.writer = rec, rec is not None, None
        self.placed = self.full = self.closed = False
        self.headless = headless
        if not self.headless:
            self.cv2.namedWindow(title, self.cv2.WINDOW_NORMAL | self.cv2.WINDOW_KEEPRATIO)

    def display_scale(self, frame_w: int) -> float:
        """Grab px -> drawn px: `width` / frame width (never above 1), else the fixed `scale`. A 1920 px mirror then
        draws at the size the 1194 px one did, while the tracker reads all 1920 px."""
        self.scale = min(1.0, self.width / frame_w) if self.width else self.scale
        return self.scale

    def _place(self, w: int, h: int) -> None:
        """Right of the grab rect, else below, else top-left; clamped to the screen (the mirror is off screen)."""
        import ctypes
        cv2, r = self.cv2, self.rect
        sw, sh = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
        cv2.resizeWindow(self.title, w, h)
        x, y = 0, 0
        if r and r["left"] >= 0 and r["left"] + r["width"] <= sw:
            if r["left"] + r["width"] + 8 + w <= sw:
                x, y = r["left"] + r["width"] + 8, r["top"]
            elif r["top"] + r["height"] + 40 + h <= sh:
                x, y = r["left"], r["top"] + r["height"] + 40
        cv2.moveWindow(self.title, max(0, min(x, sw - w)), max(0, min(y, sh - h - 40)))
        cv2.setWindowProperty(self.title, cv2.WND_PROP_TOPMOST, 1)
        self.placed = True

    def _best_angles(self, tr3: np.ndarray, now: float) -> tuple[float, float]:
        mode = self.VIEWS[self.view_idx % len(self.VIEWS)]
        if mode == "top":
            return 0.0, 88.0
        if mode == "iso":
            return -38.0, 30.0
        if mode == "orbit":
            t0 = self.trail[0][0] if self.trail else now
            return (-38.0 + 14.0 * max(0.0, now - t0)) % 360.0 - 180.0, 28.0
        az_t, el_t = -38.0, 30.0
        if len(tr3) >= 4:
            pts = tr3[-240:]
            span_xy = float(np.ptp(pts[:, :2], axis=0).max())
            span_z = float(np.ptp(pts[:, 2]))
            if span_xy > 0.05:
                centered = pts[:, :2] - pts[:, :2].mean(axis=0)
                cov = (centered.T @ centered) / max(1, len(centered) - 1)
                vals, vecs = np.linalg.eigh(cov)
                v_dom = vecs[:, int(np.argmax(vals))]
                pca_deg = float(np.degrees(np.arctan2(v_dom[1], v_dom[0])))
                # Look ~62 deg oblique to the principal horizontal axis so neither X/Y nor depth foreshortens
                cand1 = (pca_deg + 62.0 + 180.0) % 360.0 - 180.0
                cand2 = (pca_deg - 118.0 + 180.0) % 360.0 - 180.0
                d1 = abs((cand1 - self._az + 180.0) % 360.0 - 180.0)
                d2 = abs((cand2 - self._az + 180.0) % 360.0 - 180.0)
                az_t = cand1 if d1 <= d2 else cand2
            z_ratio = span_z / max(span_xy, 0.20)
            # Lower elevation (22-26 deg) when vertical Z motion dominates; higher (32-42 deg) when XY loop dominates
            el_t = float(np.clip(38.0 - 11.0 * z_ratio, 22.0, 42.0))
        d_az = (az_t - self._az + 180.0) % 360.0 - 180.0
        self._az = (self._az + 0.14 * d_az + 180.0) % 360.0 - 180.0
        self._el = self._el + 0.14 * (el_t - self._el)
        return self._az, self._el

    def _map(self, side: int, now: float = 0.0) -> np.ndarray:
        cv2 = self.cv2
        o2 = np.zeros(2) if self.anchor is None else np.asarray(self.anchor[1:3], float)
        o3 = np.array([o2[0], o2[1], 0.0])
        if self.trail:
            raw = np.asarray(self.trail, float)
            tr3 = np.column_stack([raw[:, 1:3], raw[:, 3] if raw.shape[1] >= 4 else np.zeros(len(raw))])
        else:
            tr3 = np.zeros((0, 3))
        z_ref = float(np.median(tr3[-30:, 2])) if len(tr3) else max(0.0, self.z)
        if self.traj is None or len(self.traj) == 0:
            tj3 = np.zeros((0, 3))
        else:
            tj_arr = np.asarray(self.traj, float)
            tj3 = (np.column_stack([tj_arr[:, :2] + o2, np.full(len(tj_arr), max(0.25, z_ref))])
                   if tj_arr.shape[1] == 2 else tj_arr + o3)
        all_pts = np.vstack([p for p in (tr3, tj3, o3[None, :]) if len(p)])
        pmin, pmax = all_pts.min(axis=0), all_pts.max(axis=0)
        c_tgt = np.array([0.5 * (pmin[0] + pmax[0]), 0.5 * (pmin[1] + pmax[1]),
                          0.45 * max(0.35, float(pmax[2]))])
        self._center = c_tgt if self._center is None else (self._center + 0.20 * (c_tgt - self._center))
        cx, cy, cz = self._center
        half_xy = max(0.5, float(np.abs(all_pts[:, :2] - np.array([cx, cy])).max(initial=0.0)) * 1.20)
        ext_xy = np.ceil(4.0 * half_xy) / 4.0     # 0.25 m steps, min 0.5 m half-span
        ext_z = max(0.5, np.ceil(4.0 * max(0.4, float(pmax[2]) * 1.20)) / 4.0)
        z_scale = min(1.35, max(0.75, 0.85 * ext_xy / max(ext_z, 0.25)))
        az_deg, el_deg = self._best_angles(tr3, now)
        az, el = np.radians(az_deg), np.radians(el_deg)
        ca, sa, ce, se = np.cos(az), np.sin(az), np.cos(el), np.sin(el)
        k = (0.40 * side) / max(ext_xy, 0.5)

        def proj(pts3):
            p = np.atleast_2d(np.asarray(pts3, float))
            dx, dy, dz = p[:, 0] - cx, p[:, 1] - cy, (p[:, 2] - cz) * z_scale
            rx = ca * dx - sa * dy
            ry = sa * dx + ca * dy
            depth = (-ce * ry + se * dz) / max(ext_xy, 0.5)
            persp = 1.0 / np.clip(1.0 + 0.14 * depth, 0.65, 1.55)
            u = side * 0.50 + rx * k * persp
            v = side * 0.54 - (se * ry + ce * dz) * k * persp
            return np.stack([u, v], axis=1).astype(np.int32)

        img = np.full((side, side, 3), 24, np.uint8)
        step_g = 0.25 if ext_xy <= 0.75 else 0.5
        g_min_x, g_max_x = np.floor((cx - ext_xy) / step_g) * step_g, np.ceil((cx + ext_xy) / step_g) * step_g
        g_min_y, g_max_y = np.floor((cy - ext_xy) / step_g) * step_g, np.ceil((cy + ext_xy) / step_g) * step_g
        for gx in np.arange(g_min_x, g_max_x + 1e-9, step_g):
            col = (95, 95, 105) if abs(gx) < 1e-6 else (50, 50, 56)
            cv2.line(img, *map(tuple, proj([[gx, g_min_y, 0.0], [gx, g_max_y, 0.0]])), col, 1, cv2.LINE_AA)
        for gy in np.arange(g_min_y, g_max_y + 1e-9, step_g):
            col = (95, 95, 105) if abs(gy) < 1e-6 else (50, 50, 56)
            cv2.line(img, *map(tuple, proj([[g_min_x, gy, 0.0], [g_max_x, gy, 0.0]])), col, 1, cv2.LINE_AA)

        # Vertical Z-axis post at back corner of floor grid + 0.25 m tick marks
        z_post_xy = [g_min_x, g_max_y]
        cv2.line(img, *map(tuple, proj([[*z_post_xy, 0.0], [*z_post_xy, ext_z]])), (120, 110, 80), 1, cv2.LINE_AA)
        for gz in np.arange(0.0, ext_z + 1e-9, 0.25):
            tp = proj([[*z_post_xy, gz], [z_post_xy[0] + 0.06 * ext_xy, z_post_xy[1], gz]])
            cv2.line(img, tuple(tp[0]), tuple(tp[1]), (140, 130, 95), 1, cv2.LINE_AA)
            if gz > 0:
                cv2.putText(img, f"{gz:.2f}m", (int(tp[0][0]) + 4, int(tp[0][1]) - 2),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.33, (145, 140, 115), 1, cv2.LINE_AA)

        # Pad origin 3D axis triad (+X red, +Y green, +Z cyan) & drift pass circle on z=0 floor
        ax_len = min(0.25, 0.35 * ext_xy)
        triad = proj([o3, o3 + [ax_len, 0, 0], o3 + [0, ax_len, 0], o3 + [0, 0, ax_len]])
        cv2.line(img, tuple(triad[0]), tuple(triad[1]), (70, 80, 235), 2, cv2.LINE_AA)
        cv2.line(img, tuple(triad[0]), tuple(triad[2]), (70, 210, 70), 2, cv2.LINE_AA)
        cv2.line(img, tuple(triad[0]), tuple(triad[3]), (235, 200, 50), 2, cv2.LINE_AA)
        if self.anchor is not None:
            th = np.linspace(0.0, 2.0 * np.pi, 48)
            ring3 = np.column_stack([o2[0] + self.pass_m * np.cos(th), o2[1] + self.pass_m * np.sin(th), np.zeros_like(th)])
            cv2.polylines(img, [proj(ring3)], True, (0, 200, 0) if self.max_d < self.pass_m else (0, 0, 230), 1, cv2.LINE_AA)

        if len(tj3):
            tj_floor = np.column_stack([tj3[:, :2], np.zeros(len(tj3))])
            cv2.polylines(img, [proj(tj_floor)], True, (110, 80, 40), 1, cv2.LINE_AA)
            cv2.polylines(img, [proj(tj3)], True, (255, 170, 60), 1, cv2.LINE_AA)

        if len(tr3) > 1:
            # Ground shadow on z=0 plane
            sh_pts = proj(np.column_stack([tr3[:, :2], np.zeros(len(tr3))]))
            sh_uniq = sh_pts[np.r_[True, (np.diff(sh_pts, axis=0) != 0).any(1)]]
            if len(sh_uniq) > 1:
                cv2.polylines(img, [sh_uniq], False, (82, 78, 72), 1, cv2.LINE_AA)
            # Vertical drop-stems from 3D path to z=0 floor for 3D depth perception
            step_s = max(1, len(tr3) // 18)
            for idx in range(0, len(tr3), step_s):
                p_top, p_bot = proj([tr3[idx], [tr3[idx, 0], tr3[idx, 1], 0.0]])
                cv2.line(img, tuple(p_bot), tuple(p_top), (75, 95, 110), 1, cv2.LINE_AA)
            # 3D path ribbon with dark backing + persistent crisp stroke
            pt3 = proj(tr3)
            keep = np.r_[True, (np.diff(pt3, axis=0) != 0).any(1)]
            pt_u = pt3[keep]
            if len(pt_u) > 1:
                cv2.polylines(img, [pt_u], False, (15, 15, 15), 4, cv2.LINE_AA)
                cv2.polylines(img, [pt_u], False, (0, 215, 255), 2, cv2.LINE_AA)

        if self.xy is not None:
            cur3 = np.array([self.xy[0], self.xy[1], max(0.0, self.z)])
            p_cur, p_gnd = proj([cur3, [self.xy[0], self.xy[1], 0.0]])
            cv2.line(img, tuple(p_gnd), tuple(p_cur), (0, 235, 180), 1, cv2.LINE_AA)
            cv2.circle(img, tuple(p_gnd), 3, (0, 180, 140), 1, cv2.LINE_AA)
            cv2.circle(img, tuple(p_cur), 6, (15, 15, 15), -1, cv2.LINE_AA)
            cv2.circle(img, tuple(p_cur), 5, (0, 255, 0), -1, cv2.LINE_AA)

        mode_name = self.VIEWS[self.view_idx % len(self.VIEWS)]
        xyz_txt = (f"x {self.xy[0]*100:+.0f} y {self.xy[1]*100:+.0f} z {self.z*100:.0f} cm"
                   if self.xy is not None else "no fix")
        cv2.putText(img, f"3D [{mode_name} az {az_deg:+.0f} el {el_deg:.0f}]  {xyz_txt}", (6, 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (220, 220, 220), 1, cv2.LINE_AA)
        cv2.putText(img, f"pad 3D ({step_g:.2f}m grid) +X:red +Y:grn +Z:cyn  span {2*ext_xy:.1f}x{ext_z:.1f}m",
                    (6, side - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (170, 170, 170), 1, cv2.LINE_AA)
        return img

    def _draw_landing_pip(self, img: np.ndarray, frame: np.ndarray) -> None:
        """Magnified Picture-in-Picture inset around the drone/pad during landing phase or when key z is active."""
        if not self.uvs:
            return
        cv2 = self.cv2
        H, W = frame.shape[:2]
        ih, iw = img.shape[:2]
        u_c, v_c = self.uvs[-1][1], self.uvs[-1][2]
        pip_side = max(110, min(200, int(0.32 * ih)))
        z_eff = self.active_zoom
        z_eff = max(1.0, z_eff)
        half_src = max(36, int(pip_side / (z_eff * max(self.scale, 0.25))))
        u0 = int(np.clip(u_c - half_src, 0, max(0, W - 2 * half_src)))
        v0 = int(np.clip(v_c - half_src, 0, max(0, H - 2 * half_src)))
        crop = frame[v0:min(H, v0 + 2 * half_src), u0:min(W, u0 + 2 * half_src)]
        if crop.size == 0:
            return
        pip = cv2.resize(crop, (pip_side, pip_side), interpolation=cv2.INTER_LINEAR)
        cx_p = int((u_c - u0) * pip_side / max(1, crop.shape[1]))
        cy_p = int((v_c - v0) * pip_side / max(1, crop.shape[0]))
        cv2.circle(pip, (cx_p, cy_p), 12, (0, 255, 0), 2, cv2.LINE_AA)
        cv2.line(pip, (cx_p - 18, cy_p), (cx_p + 18, cy_p), (0, 255, 180), 1, cv2.LINE_AA)
        cv2.line(pip, (cx_p, cy_p - 18), (cx_p, cy_p + 18), (0, 255, 180), 1, cv2.LINE_AA)
        cv2.rectangle(pip, (0, 0), (pip_side - 1, pip_side - 1), (0, 215, 255), 2)
        cv2.putText(pip, pip_label(z_eff, self.z), (5, 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (0, 215, 255), 1, cv2.LINE_AA)
        y1, x1 = ih - 8, iw - 8
        img[y1 - pip_side:y1, x1 - pip_side:x1] = pip

    def _key(self, k: int, now: float) -> None:
        cv2 = self.cv2
        if k == ord("r"):
            if self.rec is None:
                self.rec = time.strftime("logs/video_truth/tracker_%Y%m%d_%H%M%S.mp4")
            self.recording = not self.recording
            print("show: recording", "on" if self.recording else "paused", "->", self.rec, flush=True)
        elif k == ord("a") and self.xy is not None:
            self.anchor, self.max_d = (now, *self.xy), 0.0
        elif k == ord("c"):
            self.clear_trail()
        elif k == ord("v"):
            self.view_idx = (self.view_idx + 1) % len(self.VIEWS)
        elif k == ord("z"):
            self.zoom_pip = not self.zoom_pip
        elif k == ord("f"):
            self.full = not self.full
            cv2.setWindowProperty(self.title, cv2.WND_PROP_FULLSCREEN,
                                  cv2.WINDOW_FULLSCREEN if self.full else cv2.WINDOW_NORMAL)

    def clear_trail(self) -> None:
        """Drop both trails (key c; run_source when the coral static is learned and at the first take-off)."""
        self.trail, self.uvs, self.z_max, self._center = [], [], 0.0, None

    def __call__(self, now: float, frame, uv, xy, lines, z: float | None = None, z_eff: float = 1.0) -> None:
        if z is not None and np.isfinite(z):
            self.z = max(0.0, float(z))
            self.z_max = max(self.z_max, self.z)
        self.active_zoom = float(z_eff)
        if uv is not None:
            self.uvs.append((now, float(uv[0]), float(uv[1])))
        if xy is not None:
            self.xy = (float(xy[0]), float(xy[1]))
            self.trail.append((now, self.xy[0], self.xy[1], self.z))
            if self.anchor is None:                       # first fix anchors the drift circle; key a re-anchors
                self.anchor = (now, *self.xy)
            self.max_d = max(self.max_d, float(np.hypot(self.xy[0] - self.anchor[1], self.xy[1] - self.anchor[2])))
        if now - self.t_last < self.dt:
            return
        p0 = time.perf_counter()
        if self.trail_s > 0:
            while self.trail and now - self.trail[0][0] > self.trail_s:
                self.trail.pop(0)
            while self.uvs and now - self.uvs[0][0] > self.trail_s:
                self.uvs.pop(0)
        cv2, s = self.cv2, self.display_scale(frame.shape[1])
        img = cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1.0 else cv2.INTER_NEAREST)
        if len(self.uvs) > 1:
            uv_px = (np.asarray(self.uvs)[:, 1:3] * s).astype(np.int32)
            uv_u = uv_px[np.r_[True, (np.diff(uv_px, axis=0) != 0).any(1)]]
            if len(uv_u) > 1:
                cv2.polylines(img, [uv_u], False, (15, 15, 15), 3, cv2.LINE_AA)
                cv2.polylines(img, [uv_u], False, (0, 215, 255), 2, cv2.LINE_AA)
        if self.uvs and now - self.uvs[-1][0] < self.HOLD_S:   # held ring: no blink on frames without a detection
            c, col = (int(self.uvs[-1][1] * s), int(self.uvs[-1][2] * s)), (0, 255, 0) if uv is not None else (0, 200, 255)
            cv2.circle(img, c, 14, col, 2, cv2.LINE_AA)
            if self.xy is not None:
                cv2.putText(img, f"{self.xy[0] * 100:+.0f},{self.xy[1] * 100:+.0f},{self.z * 100:.0f} cm",
                            (c[0] + 16, c[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA)
        if self.zoom_pip or (self.z_max >= 0.20 and 0.0 < self.z <= 0.30):
            self._draw_landing_pip(img, frame)
        if self.anchor is not None and self.xy is not None:
            d = np.hypot(self.xy[0] - self.anchor[1], self.xy[1] - self.anchor[2])
            lines = [*lines, f"drift {d * 100:.1f} cm  max {self.max_d * 100:.1f}/{self.pass_m * 100:.0f}  "
                             f"hold {now - self.anchor[0]:.1f} s"]
        for i, txt in enumerate([*lines, self.KEYS]):
            cv2.putText(img, txt, (8, 20 + 18 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(img, txt, (8, 20 + 18 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (0, 0, 255) if txt.startswith("NO FIX") else (255, 255, 255), 1, cv2.LINE_AA)
        img = np.hstack([img, self._map(img.shape[0], now)])
        if self.recording:
            if self.writer is None:
                Path(self.rec).parent.mkdir(parents=True, exist_ok=True)
                self.writer = cv2.VideoWriter(self.rec, cv2.VideoWriter_fourcc(*"mp4v"), min(self.hz, 60.0),
                                              (img.shape[1], img.shape[0]))
            self.writer.write(img)
            cv2.circle(img, (img.shape[1] - 16, 16), 7, (0, 0, 255), -1)   # REC dot on screen only
        if not self.closed and not self.headless:
            if not self.placed:
                self._place(img.shape[1], img.shape[0])
            elif cv2.getWindowProperty(self.title, cv2.WND_PROP_VISIBLE) < 1:   # user closed it: stop redrawing
                self.closed = True
            if not self.closed:
                cv2.imshow(self.title, img)
                k = cv2.waitKey(1)
                if k > 0:
                    self._key(k & 0xFF, now)
        self.t_last, self.n, self.ms = now, self.n + 1, self.ms + 1000 * (time.perf_counter() - p0)

    def close(self) -> None:
        if self.writer is not None:
            self.writer.release()
            print(f"show: recorded {self.rec}", flush=True)
        if not self.closed and not self.headless:          # headless: no window to destroy (cv2 raises)
            self.cv2.destroyWindow(self.title)


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
        c = np.asarray(cam_xy_m, float)
        e = np.asarray(earth_cm, float)
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
        self.drift_deg, self.lock_msg = None, ""
        self.status = "align WAIT airborne"

    def push(self, t: float, cam_xy_m, earth_cm) -> bool:
        """One airborne pair (pad m as fed to `align`, EKF earth cm); True on the push that locks. After the lock the
        same fit keeps running every 5 refit_s as a monitor only (nightly item 6): `drift_deg` is the rotation the
        window fits now against the locked one. Not applied: in modes 2/3 earth carries the fixes, so it reads low."""
        self.rows.append((t, *np.asarray(cam_xy_m, float)[:2], *np.asarray(earth_cm, float)[:2]))
        while self.rows[0][0] < t - self.window_s:
            self.rows.pop(0)
        if t - self.t_fit < self.refit_s * (5 if self.locked else 1) or len(self.rows) < 20:
            return False
        self.t_fit = t
        r = np.array(self.rows)
        cam, earth = r[:, 1:3], r[:, 3:5]
        span = float(np.ptp(cam, axis=0).max()) * 100.0
        if span < self.span_cm:
            if not self.locked:
                self.prev, self.status = None, f"align WAIT span {span:.0f}/{self.span_cm:.0f} cm"
            return False
        f = EarthAlign.fit(cam, earth, mirror=self.align.mirror)
        if self.locked:
            self.drift_deg = float(np.degrees(np.angle(f.a / self.align.a)))
            self.status = f"{self.lock_msg} | refit {self.drift_deg:+.0f} deg rms {f.rms_cm:.0f} cm"
            return False
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
        self.status = self.lock_msg = f"align LOCK {np.degrees(np.angle(f.a)):.0f} deg -> mode {self.target}"
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
                drift = []                                # post-lock yaw monitor (item 6), raw EKF in shadow
                for i in np.flatnonzero(k & (t > t[i_lock])):
                    aa.push(t[i], cam[i], earth[i])
                    if aa.drift_deg is not None and (not drift or aa.drift_deg != drift[-1]):
                        drift.append(aa.drift_deg)
                print(f"  post-lock refit every {5 * aa.refit_s:.0f} s (deg): " + " ".join(f"{x:+.0f}" for x in drift))
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
    may be: at 30 fps a 0.1 s period holds only 3 frames, so one dropped frame would skip the fix.
    The window widens to n_med measured detection intervals (x1.5), at most max_window_s: on 2026-10-15 the grab ran at
    ~6 fps (read_ms 98-126 at 1920x1080), a 0.1 s window never held 2 detections and 0-2 fixes went out per flight
    (skipped 99.9%), so the drone flew on flow alone. The fix carries its age, so the firmware back-dates it."""

    def __init__(self, period: float = 1.0, n_med: int = 5, max_spread_m: float = 0.05, min_in: int = 3,
                 window_s: Optional[float] = None, max_window_s: float = 0.5):
        self.period, self.n_med, self.max_spread, self.min_in = period, n_med, max_spread_m, min(min_in, n_med)
        self.window = period if window_s is None else window_s
        self.max_window = max(self.window, max_window_s)
        self.dt: Optional[float] = None                   # EMA of the detection interval
        self.buf: list[tuple[float, np.ndarray]] = []
        self.next_t: Optional[float] = None
        self.n_sent = self.n_skip = 0

    def window_s(self) -> float:
        if self.dt is None:
            return self.window
        return min(self.max_window, max(self.window, 1.5 * self.n_med * self.dt))

    def push(self, t_cap: float, xy_m) -> None:
        if self.buf and 0.0 < t_cap - self.buf[-1][0] < 1.0:
            d = t_cap - self.buf[-1][0]
            self.dt = d if self.dt is None else 0.8 * self.dt + 0.2 * d
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
        recent = [d for d in self.buf if d[0] > now - self.window_s()][-self.n_med:]
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
    (EKF cm); on lock the mode frame goes out at once. When the ring learns its static on the ground, the pad origin
    (not yet frozen) restarts and `show` drops its trails; the first take-off drops them again (lab 10-19: a line
    from the pre-learning guard corner to the drone stayed in the trail and tracker.mp4)."""
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
    nf_seen, t_nf, t_nf_warn = -1, 0.0, -1e9
    learned, took_off = bool(getattr(ring, "learned", False)), False
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
        z_agl = (max(0.0, float(z) - float(getattr(height_m, "ring_h", 0.0)))
                 if z is not None and np.isfinite(z) else 0.0)
        if ring is not None:
            uv = ring.step(frame, z_agl) if isinstance(ring, CoralTracker) else ring.step(frame)
            if uv_log is not None:
                uv_log.append((t_read, *(uv if uv is not None else (np.nan, np.nan))))
            xy = None if uv is None or not np.isfinite(z) else ring_xy(uv, cam, z)
        else:
            xy = detect_xy(frame, cam, z, static) if np.isfinite(z) else None
        if xy is not None and gate is not None and not gate.check(t_cap, xy, uv):
            if ring is not None and hasattr(ring, "recentre") and gate.last[2] is not None:
                ring.recentre(gate.last[2])
            xy = None
        on_ground = ground() if ground else None
        if not learned and getattr(ring, "learned", False):
            learned = True
            if on_ground is not False:
                if pad is not None and not pad.frozen:
                    pad.hist, pad.xy = [], None           # the origin from the static-learned dots only
                if show is not None:
                    show.clear_trail()
                print(f"coral static learned at t {t_read:.1f} s: pad origin and trail restarted", flush=True)
        if on_ground is False and not took_off:
            took_off = True
            if show is not None:
                show.clear_trail()
        if xy is not None and pad is not None:
            xy = pad.apply(xy, on_ground)
        t_rd, t_det = t_rd + (p1 - p0), t_det + (time.perf_counter() - p1)
        if sched.n_sent != nf_seen:                       # the 2026-10-15 window: a perfect track, 0 fixes sent
            nf_seen, t_nf = sched.n_sent, t_read
        no_fix = n_det > 0 and t_read - t_nf >= 3.0
        if no_fix and t_read - t_nf_warn >= 5.0:
            t_nf_warn = t_read
            print(f"WARN no fix sent for {t_read - t_nf:.0f} s: det {n_det}/{n} skipped {sched.n_skip} "
                  f"window {sched.window_s():.2f} s; the drone flies on flow alone", flush=True)
        if show is not None:
            show(t_read, frame, uv, xy, ([f"NO FIX SENT {t_read - t_nf:.0f} s - FLOW ONLY"] if no_fix else []) +
                 [f"mode {auto.mode if auto else mode}  z {z:.2f} m  fixes {sched.n_sent}  det {n_det}/{n}",
                                         f"gate rej {gate.n_rej if gate else 0}  pad {'set' if pad is None or pad.xy is not None else 'WAIT'}"
                                         f"{' frozen' if pad is not None and pad.frozen else ''}"]
                 + ([auto.status] if auto else []), z=z_agl, z_eff=ring.active_zoom if ring else 1.0)
        if xy is not None:
            n_det += 1
            sched.push(t_cap, xy)
        fix = sched.due(t_cap)                            # capture clock: a latency > period would empty the window
        if fix is None:
            continue
        t_fix, med = fix
        if auto is not None and np.isfinite(z) and z >= air_m and earth is not None:
            ev, st = earth(), auto.status
            if ev is not None and auto.push(t_read, med, ev):
                print(f"{auto.status} at t {t_read:.1f} s", flush=True)
                if send:
                    send(ext_pos_mode_frame(auto.mode))
            elif auto.locked and auto.status != st:           # post-lock yaw monitor, one line per refit
                print(f"{auto.status} at t {t_read:.1f} s", flush=True)
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
    if show:
        show.close()
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


def fw_fusion(t, est, arr_t, idx, xy, delay_ms: float, exe: Path | None = None, mode: int = 2) -> tuple[np.ndarray, dict]:
    """The firmware mode-2/3 path (fw_build) on est (m, at t): 5 ms control ticks, fixes (xy m, encoded with
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
    lines = [f"M {mode}", f"D {delay_ms:.3f}"]
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
           slew_m: float = vt.EXT_POS["slew_m"], beta: float = vt.EXT_POS["beta"], fw: bool = False,
           mode: int = 2, fixes: str | None = None) -> dict:
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
    video = vt.video_file(vd)
    o, h, tt, z = summ.get("offset_s", 0.0), summ.get("h_ring_m", 0.15), tab["t_tel"], tab["z"]
    fixes_list: list = []
    if fixes or not Path(video).exists():
        fx_p = Path(fixes) if fixes else (vd / "fixes.csv")
        fx_df = pd.read_csv(fx_p)
        idx = np.array([bytes.fromhex(h)[3] for h in fx_df["frame_hex"]])
        age = idx * 0.01
        codes = [struct.unpack("<f", bytes.fromhex(h)[4:8])[0] for h in fx_df["frame_hex"]]
        xy = np.array([ext_pos_decode(c) for c in codes], float) / 100.0
        stem_candidates = [str(vd.parent.parent / name), f"logs/{name}", f"/home/agent/data/r6/logs/{name}"]
        stem = next((s for s in stem_candidates if Path(f"{s}.slot2.csv").exists()), stem_candidates[0])
        fc = vt.fixes_clock(stem, fx_p)
        t_read = fc["t"]
        st = None
        links = [0.0] if (fixes or not Path(video).exists()) and links == (0.15, 0.3) else links
    else:
        cam = vt.load_cam(cam_path, vt.video_size(video))
        scan = dict(np.load(vd / "aruco.npz"))
        R, tw, _ = vt.floor_from_marker(scan, cam)
        cam = dict(cam, R=R, t=tw)
        seed = vt.static_corners(scan["c0"])[0].mean(0)
        st = run_source(video, cam, EarthAlign(), lambda tv: h + max(0.0, float(np.interp(tv + o, tt, z, left=0, right=0))),
                        sched=FixScheduler(period, n_med, window_s=window_s), max_frames=max_frames,
                        ring=CoralTracker(seed, vt.coral_static(video)), gate=JumpGate() if gate else None, fix_log=fixes_list)
        t_read = np.array([f[0] for f in fixes_list])
        idx = np.array([f[2][3] for f in fixes_list])
        age = idx * 0.01
        xy = np.array([ext_pos_decode(struct.unpack("<f", bytes(f[2][4:8]))[0]) for f in fixes_list], float) / 100.0
        pad = np.array([f[1] for f in fixes_list]) < summ["takeoff_video_s"] - 1.0
        xy -= np.median(xy[pad], axis=0)                      # truth4 is relative to the pad spot too
    earth = np.c_[tab["earth_x"], tab["earth_y"]]
    out = {"name": name, "tracker": st, "circular": True, "delay_err_s": delay_err_s, "gain": gain,
           "slew_cm": slew_m * 100, "spread_ms": vt.EXT_POS["spread_s"] * 1000, "beta": beta, "period_s": period, "n_med": n_med, "window_s": window_s, "fw": fw, "mode": mode, "links": {}}
    sfx = vt.ext_pos_sfx(gain, slew_m, vt.EXT_POS["spread_s"], beta)
    if (period, n_med, window_s) != (0.1, 3, 0.2):
        sfx += f"_p{round(period * 1000):03d}n{n_med}w{round(window_s * 1000):03d}"
    if fw:
        if (gain, slew_m, beta) != (vt.EXT_POS["gain"], vt.EXT_POS["slew_m"], vt.EXT_POS["beta"]):
            raise ValueError("fw runs the built-in API/ekf_of.h constants; keep gain/slew/beta default")
        sfx += f"_fw_m{mode}"
        exe = fw_build()
    if delay_err_s:
        sfx += f"_de{'p' if delay_err_s > 0 else 'm'}{round(abs(delay_err_s) * 1000):03d}"
    for L in links:
        arr = t_read if (fixes or not Path(video).exists()) else (t_read + o + L)
        dly_ms = (L + delay_err_s) * 1000 if Path(video).exists() and not fixes else delay_err_s * 1000
        if fw:
            fused, n = fw_fusion(tt, earth, arr, idx, xy, dly_ms, exe, mode=mode)
        else:
            fused, n = vt.ext_pos_replay(tt, earth, arr - age - (L + delay_err_s), xy, 0.0,
                                         dict(vt.EXT_POS, gain=gain, slew_m=slew_m, beta=beta), arr_t=arr)
        r = vt.drift_score(name, ests={"earth": earth, "fused": fused}, tag=f"_live_{int(L * 1000)}ms{sfx}")
        out["links"][L] = {"counts": n, "est": {k: {q: e.get(q) for q in ("rms_cm", "drift60_p90_cm", "drift60s_p90_cm",
                                                                         "hf_rms_cm")}
                                                for k, e in r["est"].items()}}
    return out


AB_REPLAY_SEED_PX = (320, 240)    # PROPOSED
AB_REPLAY_H_M = 0.15            # PROPOSED


def ab_replay(video: str, z_csv: str | None = None, zoom_a: float = 1.0, zoom_b: float = 1.0, land_zoom_a: float = 1.0, land_zoom_b: float = 2.0, land_z_m: float = 0.30) -> int:
    out_dir = Path(video).parent

    if z_csv and Path(z_csv).exists():
        with open(z_csv) as f:
            reader = csv.DictReader(f)
            ts_list, zs_list = [], []
            for row in reader:
                ts_list.append(float(row["t_s"]))
                zs_list.append(float(row["z_m"]))
        if ts_list:
            def height_fn(t):
                return float(np.interp(t, ts_list, zs_list, left=0.0, right=0.0))
        else:
            def height_fn(t):
                return AB_REPLAY_H_M
    else:
        def height_fn(t):
            return AB_REPLAY_H_M

    seed = AB_REPLAY_SEED_PX

    cam_json = out_dir / "cam.json"
    if cam_json.exists():
        with open(cam_json, encoding="utf-8") as f:
            cam_data = json.load(f)
            if "aruco_id0_corners_px" in cam_data:
                seed = tuple(np.mean(cam_data["aruco_id0_corners_px"], axis=0))

    def run_with_timing(zoom, land_zoom):
        ring = CoralTracker(seed, zoom=zoom, land_zoom=land_zoom, land_z_m=land_z_m)

        cap = cv2.VideoCapture(video)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        n_frames, n_det = 0, 0

        decode_t, crop_t, det_t, draw_t = [], [], [], []
        uvs = []

        show = Preview(headless=True, hz=fps)

        while cap.isOpened():
            dt0 = time.perf_counter()
            ok, frame = cap.read()
            dt1 = time.perf_counter()
            if not ok:
                break
            decode_t.append(dt1 - dt0)

            t_cap = n_frames / fps
            z = height_fn(t_cap)

            uv = ring.step(frame, z_agl=z)
            if "crop" in ring.last_s:
                crop_t.append(ring.last_s["crop"])
            if "detect" in ring.last_s:
                det_t.append(ring.last_s["detect"])

            t_draw_start = time.perf_counter()
            prev_t_last = show.t_last
            show(t_cap, frame, uv, None, [], z=z, z_eff=ring.active_zoom)
            if show.t_last != prev_t_last:
                draw_t.append(time.perf_counter() - t_draw_start)

            if uv is not None:
                n_det += 1
                uvs.append((t_cap, uv[0], uv[1]))
            else:
                uvs.append((t_cap, np.nan, np.nan))
            n_frames += 1

        return n_frames, n_det, uvs, np.array(decode_t), np.array(crop_t), np.array(det_t), np.array(draw_t)

    t0_run = time.perf_counter()
    na, det_a, uvs_a, _dec_a, _c_a, _dt_a, _dr_a = run_with_timing(1.0, 1.0)
    dur_a = time.perf_counter() - t0_run

    t0_run = time.perf_counter()
    nb, det_b, uvs_b, dec_b, c_b, dt_b, dr_b = run_with_timing(1.0, 2.0)
    dur_b = time.perf_counter() - t0_run

    if na == 0:
        return 1

    rate_a, rate_b = det_a / na, det_b / nb
    losses_a = na - det_a
    losses_b = nb - det_b

    ua, va = np.array([u[1] for u in uvs_a]), np.array([u[2] for u in uvs_a])
    ub, vb = np.array([u[1] for u in uvs_b]), np.array([u[2] for u in uvs_b])


    def jitter(u, v):
        vu = u[np.isfinite(u)]
        vv = v[np.isfinite(v)]
        if len(vu) < 3:
            return None
        return float(np.sqrt(np.mean(np.diff(vu, n=2)**2 + np.diff(vv, n=2)**2)))

    jit_a, jit_b = jitter(ua, va), jitter(ub, vb)

    mask = np.isfinite(ua) & np.isfinite(ub)
    disag = float(np.mean(np.hypot(ua[mask] - ub[mask], va[mask] - vb[mask]))) if np.any(mask) else None

    fps_a = f"{na/dur_a:.1f}" if dur_a > 0.0 else "n/a"
    fps_b = f"{nb/dur_b:.1f}" if dur_b > 0.0 else "n/a"
    ja = f"{jit_a:.2f} px" if jit_a is not None else "n/a (< 3 pts)"
    jb = f"{jit_b:.2f} px" if jit_b is not None else "n/a (< 3 pts)"
    da = f"{disag:.2f} px" if disag is not None else "n/a (no overlap)"


    lines = [
        "## A/B Replay Summary",
        f"- **Detection Rate**: A = {rate_a:.2%} | B = {rate_b:.2%}",
        f"- **Track Losses**: A = {losses_a} | B = {losses_b}",
        f"- **UV Jitter (RMS)**: A = {ja} | B = {jb}",
        f"- **A-vs-B UV Disagreement**: {da}",
        f"- **Implied FPS**: A = {fps_a} | B = {fps_b}",
        "### Stage Timings (Run B, ms)",
        f"- **Decode**: p50 = {np.percentile(dec_b, 50)*1000:.1f} | p95 = {np.percentile(dec_b, 95)*1000:.1f}",
        f"- **Zoom/Crop**: p50 = {np.percentile(c_b, 50)*1000:.1f} | p95 = {np.percentile(c_b, 95)*1000:.1f}" if len(c_b) > 0 else "- **Zoom/Crop**: N/A",
        f"- **Detect**: p50 = {np.percentile(dt_b, 50)*1000:.1f} | p95 = {np.percentile(dt_b, 95)*1000:.1f}" if len(dt_b) > 0 else "- **Detect**: N/A",
        f"- **Draw**: p50 = {np.percentile(dr_b, 50)*1000:.1f} | p95 = {np.percentile(dr_b, 95)*1000:.1f}" if len(dr_b) > 0 else "- **Draw**: N/A"
    ]
    with open(out_dir / "ab_summary.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    with open(out_dir / "ab_frames.csv", "w", newline='', encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["t_s", "u_a", "v_a", "u_b", "v_b", "z_eff_b"])
        for i in range(na):
            z_m = height_fn(uvs_a[i][0])
            z_eff_b = land_zoom_b if (0.0 < z_m <= land_z_m) else zoom_b
            writer.writerow([uvs_a[i][0], ua[i], va[i], ub[i], vb[i], z_eff_b])

    print(f"A/B replay complete: wrote {out_dir}/ab_summary.md and ab_frames.csv")
    return 0


def replay_main(argv) -> int:
    ap = argparse.ArgumentParser(prog="live_tracker replay", description=replay.__doc__)
    ap.add_argument("names", nargs="+", help="logs/video_truth/<name> dirs with truth4.csv or video with --ab")
    ap.add_argument("--ab", action="store_true", help="run A/B replay on a video file")
    ap.add_argument("--z-csv", help="CSV with z heights for A/B replay")
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
    ap.add_argument("--mode", type=int, default=2, help="ext_pos mode (2 or 3, default 2)")
    ap.add_argument("--fixes", type=str, default=None, help="replay fixes CSV (e.g. fixes.csv, replay_start_fixes.csv)")

    ap.add_argument("--zoom", type=float, default=1.0)
    ap.add_argument("--land-zoom", type=float, default=2.0)
    ap.add_argument("--land-z-m", type=float, default=0.30)
    a = ap.parse_args(argv)
    if a.ab:
        if a.zoom < 1.0 or a.land_zoom < 1.0 or a.land_z_m <= 0.0:
            ap.error("zoom/land-zoom must be >= 1.0, land-z-m must be > 0.0")
        return ab_replay(a.names[0], a.z_csv, a.zoom, a.zoom, 1.0, a.land_zoom, a.land_z_m)

    for nm in a.names:
        r = replay(nm, a.link, a.delay_err, a.period, a.n_med, a.window_s, not a.no_gate, max_frames=a.max_frames,
                   gain=a.gain, slew_m=a.slew_cm / 100, beta=a.beta, fw=a.fw, mode=a.mode, fixes=a.fixes)
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
    ap.add_argument("--coral-static", choices=["auto", "video", "start", "none"], default="auto",
                    help="coral scenery mask: video = whole file (offline truth), start = learned from the first "
                         "--static-n frames x5 with the drone on the pad (live), none = the pre-2026-10-13 live "
                         "behaviour; auto: video for a file, start live")
    ap.add_argument("--static-n", type=int, default=30, help="--coral-static start: frames sampled (every 5th)")
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
    ap.add_argument("--tel-url", default="", help="live height from the 8081 service instead (http://127.0.0.1:8081): "
                    "use it when 8081 owns the link (campaign_fly)")
    ap.add_argument("--tel-key", default="Ctrler.Z_posPID.FB")
    ap.add_argument("--ring-h-m", type=float, default=0.057, help="ring plane on the pad (r5_marks_1 truth4 h_ring_m)")
    ap.add_argument("--tel-stale-s", type=float, default=1.0, help="older telemetry: --height-m, or no fix")
    ap.add_argument("--no-pad-origin", action="store_true", help="send floor-frame xy (default: pad-relative)")
    ap.add_argument("--show", action="store_true", help="tracker window: frame + ring + trails | pad-frame map, topmost")
    ap.add_argument("--show-hz", type=float, default=30.0, help="--show redraw (and --show-rec fps) rate")
    ap.add_argument("--show-width", type=int, default=716,
                    help="--show frame width px (default 716 = 0.6 x the 1194 px r7 mirror); the tracker uses the full grab")
    ap.add_argument("--show-rec", help="record the --show window to this mp4 from the start (key r pauses/resumes)")
    ap.add_argument("--traj", help="preset trajectory on the --show map: 'circle:radius_m=0.5' or a shape/params json")
    ap.add_argument("--pass-cm", type=float, default=20.0, help="--show drift pass circle radius about the anchor")
    ap.add_argument("--stop-file", default="", help="end the run cleanly when this file appears (camera_truth.sh stop)")
    ap.add_argument("--static-t", default="", help='file only, "90,140,190": mask guard-colour pixels present at all these times')
    ap.add_argument("--zoom", type=float, default=1.0, help="base tracking zoom >= 1.0 (default 1.0)")
    ap.add_argument("--land-zoom", type=float, default=1.0, help="tracking zoom when below land_z_m (default 1.0 = off; 2.0 lost the drone at mem2 lift-off 10-18)")
    ap.add_argument("--land-z-m", type=float, default=0.30, help="apply land_zoom when 0 < z_agl <= this, m (default 0.30)")
    a = ap.parse_args(argv)
    if a.zoom < 1.0 or a.land_zoom < 1.0 or a.land_z_m <= 0.0:
        ap.error("zoom/land-zoom must be >= 1.0, land-z-m must be > 0.0")
    if a.align == "auto" and not ((a.tel_csv or a.tel_url) and a.mode in (2, 3)):
        ap.error("--align auto needs --tel-csv or --tel-url and --mode 2 or 3")
    if sum(map(bool, (a.tel_npz, a.tel_csv, a.tel_url))) > 1 or not (a.tel_npz or a.tel_csv or a.tel_url
                                                                      or a.height_m is not None):
        ap.error("give one of --tel-csv / --tel-url (with an optional --height-m fallback), --tel-npz or --height-m")
    ek = ("ano_of.earth_x", "ano_of.earth_y") if a.align == "auto" else ()
    live = a.tel_csv or a.tel_url
    tel = (TelHeightHttp if a.tel_url else TelHeight)(live, a.tel_key, a.ring_h_m, stale_s=a.tel_stale_s,
                                                      fallback_m=a.height_m, extra=ek) if live else None
    height = tel or (tel_height(a.tel_npz, tel_offset_s=a.tel_offset_s, ring_offset_m=a.ring_offset_m)
                     if a.tel_npz else a.height_m)
    src_live = str(a.source).isdigit() or a.source.startswith("window:")
    size = (WindowSource(a.source[7:]).ready() if a.source.startswith("window:")
            else None if str(a.source).isdigit() else vt.video_size(a.source))
    cam = vt.load_cam(a.cam, size)
    seed = [float(x) for x in a.seed_px.split(",")] if a.seed_px else None
    s, shift = 1.0, None
    if a.floor:
        R, t, c0, w_ref, ref = floor_from_dir(a.floor, a.cam)
        cam, s = dict(cam, R=R, t=t), (size[0] / w_ref if size else 1.0)
        seed = seed if seed is not None else list(c0 * s)
        if a.source.startswith("window:"):
            ok, f0 = WindowSource(a.source[7:], size=size).read()
            d, k = floor_shift_px(f0, {i: c for i, c in ref.items() if i}, s) if ok else (float("nan"), 0)
            shift = d
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
        cs = a.coral_static if a.coral_static != "auto" else ("start" if src_live else "video")
        scenery = None
        if cs == "start" and a.floor:            # same placement as the floor pose assumes, or the scenery is elsewhere
            scenery = floor_scenery(a.floor) if shift is None or shift <= 5 else None
            if scenery is not None and size and scenery.shape != (size[1], size[0]):
                scenery = cv2.resize(scenery.astype(np.uint8), tuple(size), interpolation=cv2.INTER_NEAREST) > 0
            print(f"coral scenery from the floor scan: {'off' if scenery is None else f'{int(scenery.sum())} px'}",
                  flush=True)
        ring = RingTracker(seed) if a.detector == "ring" else CoralTracker(   # track_coral sizes are 1920 px
            seed, vt.coral_static(a.source) if cs == "video" else None, roi=140.0 * s,
            min_px=max(10, round(40 * s * s)), learn_n=a.static_n if cs == "start" else 0,
            zoom=a.zoom, land_zoom=a.land_zoom, land_z_m=a.land_z_m, scenery=scenery)
    show = Preview(hz=a.show_hz, rect=WindowSource(a.source[7:]).rect() if a.source.startswith("window:") else None,
                   traj=traj_xy(a.traj) if a.traj else None, rec=a.show_rec, pass_cm=a.pass_cm,
                   width=a.show_width or None) if a.show else None
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
