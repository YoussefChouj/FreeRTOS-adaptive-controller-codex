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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from ground_station.analysis import video_truth as vt
from ground_station.comm.ext_pos_encoder import (ext_pos_decode, ext_pos_delay_frame, ext_pos_fix_frame,
                                                 ext_pos_mode_frame)


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


class WindowSource:
    """Live frames from a window's client area (the scrcpy mirror, e.g. `scrcpy --window-title cam`), Windows only:
    mss grabs the screen rectangle user32 reports. The window must show the whole video at its aspect (no letterbox
    bars), or the K rescale in load_cam is wrong. cv2.VideoCapture interface: read() -> (ok, bgr), release()."""

    def __init__(self, title: str):
        import ctypes
        from ctypes import wintypes
        import mss                                        # pip install mss
        self._ct, self._wt, self.u32 = ctypes, wintypes, ctypes.windll.user32
        try:
            self.u32.SetProcessDPIAware()                 # physical pixels, as mss grabs them
        except Exception:
            pass
        self.hwnd = self.u32.FindWindowW(None, title)
        if not self.hwnd:
            raise RuntimeError(f"no window titled {title!r}")
        self.sct = mss.mss()

    def rect(self) -> dict:
        r, p = self._wt.RECT(), self._wt.POINT(0, 0)
        self.u32.GetClientRect(self.hwnd, self._ct.byref(r))
        self.u32.ClientToScreen(self.hwnd, self._ct.byref(p))
        return {"left": p.x, "top": p.y, "width": r.right - r.left, "height": r.bottom - r.top}

    def size(self) -> tuple:
        r = self.rect()
        return r["width"], r["height"]

    def read(self):
        r = self.rect()
        if r["width"] <= 0 or r["height"] <= 0:
            return False, None
        return True, np.ascontiguousarray(np.asarray(self.sct.grab(r))[..., :3])   # BGRA -> BGR

    def get(self, prop) -> float:
        return 0.0

    def release(self) -> None:
        self.sct.close()


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
               mode: int | None = None, delay_ms: float | None = None, fix_log: list | None = None) -> dict:
    """Read frames from `source` (file, device index or window:<title>), emit fixes. For a file the capture time is
    the frame pts and the send time is that pts too (age = camera latency only); live, both come from `clock`.
    `ring`: a pixel tracker with .step(frame) -> uv (RingTracker, CoralTracker), else the guard detector with
    `static` (a video_truth.static_mask). `gate` drops jumps (the tracker is re-centred on the last accepted uv).
    With `send`, the delay and mode frames go first and again every 2 s while live. `fix_log` collects
    (t_read, t_fix, frame bytes) per fix."""
    cv2 = vt._cv2()
    win = str(source).startswith("window:")
    cap = WindowSource(str(source)[7:]) if win else cv2.VideoCapture(int(source) if str(source).isdigit() else source)
    live = win or str(source).isdigit()

    def hello():
        if send and delay_ms is not None:
            send(ext_pos_delay_frame(delay_ms))
        if send and mode is not None:
            send(ext_pos_mode_frame(mode))

    while start_s and not live and cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0 < start_s and cap.grab():
        pass                                              # frame-exact; set(POS_MSEC) lands seconds off on scrcpy mkv
    clock = clock or time.monotonic
    sched = sched or FixScheduler()
    rows, n, n_det, uv = [], 0, 0, None
    t_rd = t_det = 0.0
    hello()
    t_hello = clock()
    while True:
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
            xy = None if uv is None else ring_xy(uv, cam, z)
        else:
            xy = detect_xy(frame, cam, z, static)
        if xy is not None and gate is not None and not gate.check(t_cap, xy, uv):
            if ring is not None and hasattr(ring, "recentre") and gate.last[2] is not None:
                ring.recentre(gate.last[2])
            xy = None
        t_rd, t_det = t_rd + (p1 - p0), t_det + (time.perf_counter() - p1)
        if xy is not None:
            n_det += 1
            sched.push(t_cap, xy)
        fix = sched.due(t_cap)                            # capture clock: a latency > period would empty the window
        if fix is None:
            continue
        t_fix, med = fix
        e = align.apply(med)
        frame_b = ext_pos_fix_frame(e[0], e[1], t_read - t_fix)
        if fix_log is not None:
            fix_log.append((t_read, t_fix, frame_b))
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
    return {"frames": n, "detections": n_det, "fixes": sched.n_sent, "skipped": sched.n_skip,
            "gate_rej": gate.n_rej if gate else 0, "reseeds": gate.reseeds if gate else 0,
            "read_ms": 1000 * t_rd / max(n, 1), "detect_ms": 1000 * t_det / max(n, 1)}


def replay(name: str, links=(0.15, 0.3), delay_err_s: float = 0.0, period: float = 0.1, n_med: int = 3,
           window_s: float = 0.2, gate: bool = True, cam_path: str = "docs/video-truth/cam_r4_lenswave_2026-10-11.json",
           root: str = "logs/video_truth", max_frames: int = 0, gain: float = vt.EXT_POS["gain"],
           slew_m: float = vt.EXT_POS["slew_m"]) -> dict:
    """Real-time replay of one R4 flight: the online CoralTracker (+ JumpGate) on cam.mkv, fixes packed exactly as
    sent, decoded as the firmware does (value -> cm, age byte), relative to the pad spot, arriving link s after
    the frame on the telemetry clock (truth4 offset); the drone dates each fix with delay_ms = link + delay_err_s.
    vt.ext_pos_replay on the earth estimate, scored by vt.drift_score (tag _live_<ms>ms). CIRCULAR: same camera,
    lens, floor pose, sync and earth rotation as truth4 (the rotation is an oracle the live run must fit)."""
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
    age = np.array([f[2][3] for f in fixes]) * 0.01
    xy = np.array([ext_pos_decode(struct.unpack("<f", bytes(f[2][4:8]))[0]) for f in fixes], float) / 100.0
    pad = np.array([f[1] for f in fixes]) < summ["takeoff_video_s"] - 1.0
    xy -= np.median(xy[pad], axis=0)                      # truth4 is relative to the pad spot too
    earth = np.c_[tab["earth_x"], tab["earth_y"]]
    out = {"name": name, "tracker": st, "circular": True, "delay_err_s": delay_err_s, "gain": gain,
           "slew_cm": slew_m * 100, "spread_ms": vt.EXT_POS["spread_s"] * 1000, "links": {}}
    sfx = vt.ext_pos_sfx(gain, slew_m, vt.EXT_POS["spread_s"])
    for L in links:
        arr = t_read + o + L
        fused, n = vt.ext_pos_replay(tt, earth, arr - age - (L + delay_err_s), xy, 0.0,
                                     dict(vt.EXT_POS, gain=gain, slew_m=slew_m), arr_t=arr)
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
    ap.add_argument("--period", type=float, default=0.1)
    ap.add_argument("--n-med", type=int, default=3)
    ap.add_argument("--window-s", type=float, default=0.2)
    ap.add_argument("--no-gate", action="store_true")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--gain", type=float, default=vt.EXT_POS["gain"], help="c step per accepted fix, x innovation")
    ap.add_argument("--slew-cm", type=float, default=vt.EXT_POS["slew_m"] * 100, help="max c step per fix, cm")
    a = ap.parse_args(argv)
    for nm in a.names:
        r = replay(nm, a.link, a.delay_err, a.period, a.n_med, a.window_s, not a.no_gate, max_frames=a.max_frames,
                   gain=a.gain, slew_m=a.slew_cm / 100)
        print(json.dumps(r, default=float))
    return 0


def main(argv=None) -> int:
    import sys
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["replay"]:
        return replay_main(argv[1:])
    ap = argparse.ArgumentParser(description="Live camera tracker -> CMD 0x2C fixes (dry run without --send). "
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
    ap.add_argument("--out", required=True, help="fixes CSV")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--start-s", type=float, default=0.0, help="file only: seek before reading")
    ap.add_argument("--detector", choices=["ring", "coral", "guards"], default="ring",
                    help="ring: R3 red ring (default); coral: R4 guard ring (needs --seed-px live)")
    ap.add_argument("--period", type=float, default=0.1, help="s between fixes (fuse4 replay: 10 Hz)")
    ap.add_argument("--n-med", type=int, default=3)
    ap.add_argument("--window-s", type=float, default=0.2)
    ap.add_argument("--gate", action="store_true", help="JumpGate on detections (PROPOSED thresholds)")
    ap.add_argument("--send", default="", help="udp:PORT (stream_log --relay-port) or udp:HOST:PORT; default: no send")
    ap.add_argument("--mode", type=int, choices=[0, 1, 2], default=None, help="EXT_POS mode frame: 0 off, 1 shadow, 2 closed")
    ap.add_argument("--delay-ms", type=float, default=None, help="0xFE frame: firmware back-dating delay (measure it)")
    ap.add_argument("--seed-px", default="", help="u,v of the drone on the pad; default the ArUco id0 centre")
    ap.add_argument("--static-t", default="", help='file only, "90,140,190": mask guard-colour pixels present at all these times')
    a = ap.parse_args(argv)
    if (a.height_m is None) == (not a.tel_npz):
        ap.error("give exactly one of --height-m or --tel-npz")
    height = tel_height(a.tel_npz, tel_offset_s=a.tel_offset_s, ring_offset_m=a.ring_offset_m) if a.tel_npz else a.height_m
    src_live = str(a.source).isdigit() or a.source.startswith("window:")
    size = (WindowSource(a.source[7:]).size() if a.source.startswith("window:")
            else None if str(a.source).isdigit() else vt.video_size(a.source))
    cam = vt.load_cam(a.cam, size)
    align = EarthAlign(a=complex(np.exp(1j * np.radians(a.yaw_deg))), mirror=a.mirror)
    times = [float(x) for x in a.static_t.split(",") if x]
    static = vt.static_mask(a.source, times) if times and not src_live else None
    ring = None
    if a.detector in ("ring", "coral"):
        if a.seed_px:
            seed = [float(x) for x in a.seed_px.split(",")]
        else:
            with open(a.cam) as f:
                seed = np.mean(json.load(f)["aruco_id0_corners_px"], axis=0)
        ring = RingTracker(seed) if a.detector == "ring" else CoralTracker(
            seed, None if src_live else vt.coral_static(a.source))
    print(run_source(a.source, cam, align, height, a.cam_latency_s, udp_sender(a.send) if a.send else None, a.out,
                     sched=FixScheduler(a.period, a.n_med, window_s=a.window_s), max_frames=a.max_frames,
                     start_s=a.start_s, static=static, ring=ring, gate=JumpGate() if a.gate else None,
                     mode=a.mode, delay_ms=a.delay_ms))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
