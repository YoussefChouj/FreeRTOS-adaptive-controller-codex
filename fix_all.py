import re

with open("ground_station/analysis/live_tracker.py") as f:
    lt = f.read()

# 1. CoralTracker timing
lt = lt.replace(
    "self.zoom, self.land_zoom, self.land_z_m, self.active_zoom = float(zoom), float(land_zoom), float(land_z_m), 1.0\n        self._acc",
    "self.zoom, self.land_zoom, self.land_z_m, self.active_zoom = float(zoom), float(land_zoom), float(land_z_m), 1.0\n        self.last_ms = {}\n        self._acc"
)

step_search = """        if u1 > u0 and v1 > v0:
            crop = frame_bgr[v0:v1, u0:u1]
            if z_eff > 1.01:
                crop = cv2.resize(crop, (0, 0), fx=z_eff, fy=z_eff, interpolation=cv2.INTER_LINEAR)
            m = vt.coral_mask(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV))
            if self.static is not None:
                st_crop = self.static[v0:v1, u0:u1]
                if z_eff > 1.01:
                    st_crop = cv2.resize(st_crop.astype(np.uint8), (m.shape[1], m.shape[0]),
                                         interpolation=cv2.INTER_NEAREST) > 0
                m &= ~st_crop
            vv, uu = np.nonzero(m)
            if len(uu) >= self.min_px * (z_eff * z_eff):
                self.c = np.array([(np.median(uu) + 0.5) / z_eff - 0.5 + u0,
                                   (np.median(vv) + 0.5) / z_eff - 0.5 + v0])
                self.r = self.roi
                return self.c.copy()
        self.r = min(self.r * 1.5, float(max(W, H)))"""

step_replace = """        if u1 > u0 and v1 > v0:
            import time
            t0 = time.perf_counter()
            crop = frame_bgr[v0:v1, u0:u1]
            if z_eff > 1.01:
                crop = cv2.resize(crop, (0, 0), fx=z_eff, fy=z_eff, interpolation=cv2.INTER_LINEAR)
            t1 = time.perf_counter()
            self.last_ms["crop"] = t1 - t0
            m = vt.coral_mask(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV))
            if self.static is not None:
                st_crop = self.static[v0:v1, u0:u1]
                if z_eff > 1.01:
                    st_crop = cv2.resize(st_crop.astype(np.uint8), (m.shape[1], m.shape[0]),
                                         interpolation=cv2.INTER_NEAREST) > 0
                m &= ~st_crop
            vv, uu = np.nonzero(m)
            if len(uu) >= self.min_px * (z_eff * z_eff):
                self.c = np.array([(np.median(uu) + 0.5) / z_eff - 0.5 + u0,
                                   (np.median(vv) + 0.5) / z_eff - 0.5 + v0])
                self.r = self.roi
                self.last_ms["detect"] = time.perf_counter() - t1
                return self.c.copy()
            self.last_ms["detect"] = time.perf_counter() - t1
        self.r = min(self.r * 1.5, float(max(W, H)))"""
lt = lt.replace(step_search, step_replace)

# 2. pip_label
pip_label_code = """
def pip_label(z_eff: float, z_m: float) -> str:
    \"\"\"Pure function formatting the landing zoom PiP text.\"\"\"
    return f"LAND ZOOM {z_eff:.1f}x z={z_m*100:.0f}cm"

def shape_xy"""
lt = lt.replace("def shape_xy", pip_label_code)

# 3. Preview headless
lt = lt.replace(
    "width: int | None = None):\n        self.cv2",
    "width: int | None = None, headless: bool = False):\n        self.cv2"
)
lt = lt.replace(
    "self.placed = self.full = self.closed = False\n        self.cv2.namedWindow(title, self.cv2.WINDOW_NORMAL | self.cv2.WINDOW_KEEPRATIO)",
    "self.placed = self.full = self.closed = False\n        self.headless = headless\n        if not self.headless:\n            self.cv2.namedWindow(title, self.cv2.WINDOW_NORMAL | self.cv2.WINDOW_KEEPRATIO)"
)
lt = lt.replace(
    "if not self.closed:\n            if not self.placed:",
    "if not self.closed and not self.headless:\n            if not self.placed:"
)

# 4. _draw_landing_pip + __call__ z_eff passing
draw_pip_search = """    def _draw_landing_pip(self, img: np.ndarray, frame: np.ndarray) -> None:
        \"\"\"Magnified 2.5x Picture-in-Picture inset around the drone/pad during landing phase or when key z is active.\"\"\"
        if not self.uvs:
            return
        cv2 = self.cv2
        H, W = frame.shape[:2]
        ih, iw = img.shape[:2]
        u_c, v_c = self.uvs[-1][1], self.uvs[-1][2]
        pip_side = max(110, min(200, int(0.32 * ih)))
        half_src = max(36, int(pip_side / (2.5 * max(self.scale, 0.25))))
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
        cv2.putText(pip, f"LAND ZOOM 2.5x z={self.z*100:.0f}cm", (5, 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (0, 215, 255), 1, cv2.LINE_AA)
        y1, x1 = ih - 8, iw - 8
        img[y1 - pip_side:y1, x1 - pip_side:x1] = pip"""

draw_pip_replace = """    def _draw_landing_pip(self, img: np.ndarray, frame: np.ndarray) -> None:
        \"\"\"Magnified Picture-in-Picture inset around the drone/pad during landing phase or when key z is active.\"\"\"
        if not self.uvs:
            return
        cv2 = self.cv2
        H, W = frame.shape[:2]
        ih, iw = img.shape[:2]
        u_c, v_c = self.uvs[-1][1], self.uvs[-1][2]
        pip_side = max(110, min(200, int(0.32 * ih)))
        z_eff = getattr(self, "active_zoom", 1.0)
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
        img[y1 - pip_side:y1, x1 - pip_side:x1] = pip"""
lt = lt.replace(draw_pip_search, draw_pip_replace)

lt = lt.replace(
    "def __call__(self, now: float, frame, uv, xy, lines, z: float | None = None) -> None:",
    "def __call__(self, now: float, frame, uv, xy, lines, z: float | None = None, z_eff: float = 1.0) -> None:"
)
lt = lt.replace(
    "self.z_max = max(self.z_max, self.z)\n        if uv is not None:",
    "self.z_max = max(self.z_max, self.z)\n        self.active_zoom = float(z_eff)\n        if uv is not None:"
)
lt = lt.replace(
    "+ ([auto.status] if auto else []), z=z_agl)",
    "+ ([auto.status] if auto else []), z=z_agl, z_eff=getattr(ring, \"active_zoom\", 1.0) if ring else 1.0)"
)

# 5. CLI args
lt = lt.replace(
    "ap.add_argument(\"--static-t\", default=\"\", help='file only, \"90,140,190\": mask guard-colour pixels present at all these times')\n    a = ap.parse_args(argv)",
    "ap.add_argument(\"--static-t\", default=\"\", help='file only, \"90,140,190\": mask guard-colour pixels present at all these times')\n    ap.add_argument(\"--zoom\", type=float, default=1.0, help=\"base tracking zoom >= 1.0 (default 1.0)\")\n    ap.add_argument(\"--land-zoom\", type=float, default=2.0, help=\"tracking zoom when below land_z_m (default 2.0)\")\n    ap.add_argument(\"--land-z-m\", type=float, default=0.30, help=\"apply land_zoom when 0 < z_agl <= this, m (default 0.30)\")\n    a = ap.parse_args(argv)\n    if a.zoom < 1.0:\n        ap.error(\"--zoom must be >= 1.0\")"
)
lt = lt.replace(
    "min_px=max(10, round(40 * s * s)), learn_n=a.static_n if cs == \"start\" else 0)",
    "min_px=max(10, round(40 * s * s)), learn_n=a.static_n if cs == \"start\" else 0,\n            zoom=a.zoom, land_zoom=a.land_zoom, land_z_m=a.land_z_m)"
)

# 6. ab_replay
ab_replay_code = """def ab_replay(video: str, z_csv: str | None = None) -> int:
    import csv
    from pathlib import Path

    import cv2
    out_dir = Path(video).parent

    if z_csv and Path(z_csv).exists():
        with open(z_csv) as f:
            reader = csv.DictReader(f)
            ts_list, zs_list = [], []
            for row in reader:
                ts_list.append(float(row["t_s"]))
                zs_list.append(float(row["z_m"]))
        if ts_list:
            height_fn = lambda t: float(np.interp(t, ts_list, zs_list, left=0.0, right=0.0))
        else:
            height_fn = lambda t: 0.15
    else:
        height_fn = lambda t: 0.15

    seed = (320, 240)

    cam_json = out_dir / "cam.json"
    if cam_json.exists():
        with open(cam_json) as f:
            cam_data = json.load(f)
            if "aruco_id0_corners_px" in cam_data:
                seed = tuple(np.mean(cam_data["aruco_id0_corners_px"], axis=0))

    def run_with_timing(zoom, land_zoom):
        ring = CoralTracker(seed, zoom=zoom, land_zoom=land_zoom, land_z_m=0.30)

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
            if not ok: break
            decode_t.append(dt1 - dt0)

            t_cap = n_frames / fps
            z = height_fn(t_cap)

            uv = ring.step(frame, z_agl=z)
            if "crop" in ring.last_ms:
                crop_t.append(ring.last_ms["crop"])
            if "detect" in ring.last_ms:
                det_t.append(ring.last_ms["detect"])

            t_draw_start = time.perf_counter()
            prev_t_last = show.t_last
            show(t_cap, frame, uv, None, [], z=z, z_eff=getattr(ring, "active_zoom", 1.0))
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
        if len(vu) < 3: return 0.0
        return float(np.sqrt(np.mean(np.diff(vu, n=2)**2 + np.diff(vv, n=2)**2)))

    jit_a, jit_b = jitter(ua, va), jitter(ub, vb)

    mask = np.isfinite(ua) & np.isfinite(ub)
    disag = float(np.mean(np.hypot(ua[mask] - ub[mask], va[mask] - vb[mask]))) if np.any(mask) else 0.0

    lines = [
        "## A/B Replay Summary",
        f"- **Detection Rate**: A = {rate_a:.2%} | B = {rate_b:.2%}",
        f"- **Track Losses**: A = {losses_a} | B = {losses_b}",
        f"- **UV Jitter (RMS)**: A = {jit_a:.2f} px | B = {jit_b:.2f} px",
        f"- **A-vs-B UV Disagreement**: {disag:.2f} px",
        f"- **Implied FPS**: A = {na/dur_a:.1f} | B = {nb/dur_b:.1f}",
        "### Stage Timings (Run B, ms)",
        f"- **Decode**: p50 = {np.percentile(dec_b, 50)*1000:.1f} | p95 = {np.percentile(dec_b, 95)*1000:.1f}",
        f"- **Zoom/Crop**: p50 = {np.percentile(c_b, 50)*1000:.1f} | p95 = {np.percentile(c_b, 95)*1000:.1f}" if len(c_b) > 0 else "- **Zoom/Crop**: N/A",
        f"- **Detect**: p50 = {np.percentile(dt_b, 50)*1000:.1f} | p95 = {np.percentile(dt_b, 95)*1000:.1f}" if len(dt_b) > 0 else "- **Detect**: N/A",
        f"- **Draw**: p50 = {np.percentile(dr_b, 50)*1000:.1f} | p95 = {np.percentile(dr_b, 95)*1000:.1f}" if len(dr_b) > 0 else "- **Draw**: N/A"
    ]
    with open(out_dir / "ab_summary.md", "w") as f:
        f.write("\\n".join(lines))

    with open(out_dir / "ab_frames.csv", "w", newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["t_s", "u_a", "v_a", "u_b", "v_b", "z_eff_b"])
        for i in range(na):
            z_m = height_fn(uvs_a[i][0])
            z_eff_b = 2.0 if (0.0 < z_m <= 0.30) else 1.0
            writer.writerow([uvs_a[i][0], ua[i], va[i], ub[i], vb[i], z_eff_b])

    print(f"A/B replay complete: wrote {out_dir}/ab_summary.md and ab_frames.csv")
    return 0


def replay_main(argv) -> int:
    if argv and len(argv) >= 1 and (argv[0].endswith(".mkv") or argv[0].endswith(".avi") or argv[0].endswith(".mp4")):
        ap = argparse.ArgumentParser(prog="live_tracker replay <video>", description="A/B replay")
        ap.add_argument("video", help="Video file to replay")
        ap.add_argument("--z-csv", help="CSV with z heights")
        a = ap.parse_args(argv)
        return ab_replay(a.video, a.z_csv)

    ap = argparse.ArgumentParser(prog="live_tracker replay", description=replay.__doc__)"""

lt = lt.replace("def replay_main(argv) -> int:\n    ap = argparse.ArgumentParser(prog=\"live_tracker replay\", description=replay.__doc__)", ab_replay_code)

with open("ground_station/analysis/live_tracker.py", "w") as f:
    f.write(lt)


# test_live_tracker.py
with open("ground_station/analysis/tests/test_live_tracker.py") as f:
    ts = f.read()

ts += """

def test_pip_label():
    from ground_station.analysis.live_tracker import pip_label
    assert pip_label(1.5, 0.45) == "LAND ZOOM 1.5x z=45cm"
    assert pip_label(3.0, 0.2) == "LAND ZOOM 3.0x z=20cm"

def test_preview_headless_does_not_call_cv2_window_functions(monkeypatch):
    from ground_station.analysis.live_tracker import Preview
    import cv2
    import numpy as np

    def raise_err(*args, **kwargs):
        raise RuntimeError("Window function called in headless mode!")

    monkeypatch.setattr(cv2, "namedWindow", raise_err)
    monkeypatch.setattr(cv2, "imshow", raise_err)
    monkeypatch.setattr(cv2, "resizeWindow", raise_err)
    monkeypatch.setattr(cv2, "moveWindow", raise_err)

    p = Preview(headless=True)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    p(1.0, frame, None, None, [])

def test_cli_flags_reach_coral_tracker_and_zoom_validated(monkeypatch, tmp_path):
    from ground_station.analysis.live_tracker import main
    import ground_station.analysis.live_tracker as lt

    captured_ring = None

    def mock_run_source(*args, **kwargs):
        nonlocal captured_ring
        captured_ring = kwargs.get("ring")
        return {}

    monkeypatch.setattr(lt, "run_source", mock_run_source)

    cam_json = tmp_path / "cam.json"
    cam_json.write_text('{"K": [[1,0,0], [0,1,0], [0,0,1]], "dist": [0,0,0,0,0], "R": [[1,0,0], [0,1,0], [0,0,1]], "t": [0,0,0], "aruco_id0_corners_px": [[0,0],[1,0],[1,1],[0,1]]}')

    main([
        "--source", "0",
        "--cam", str(cam_json),
        "--out", str(tmp_path / "out.csv"),
        "--detector", "coral",
        "--zoom", "1.5",
        "--land-zoom", "3.0",
        "--land-z-m", "0.4",
        "--height-m", "0.0"
    ])

    assert captured_ring is not None
    assert getattr(captured_ring, "zoom") == 1.5
    assert getattr(captured_ring, "land_zoom") == 3.0
    assert getattr(captured_ring, "land_z_m") == 0.4

def test_ab_replay_produces_summary_and_handles_flags_correctly(tmp_path):
    import cv2
    import json
    from ground_station.analysis.live_tracker import replay_main
    from ground_station.analysis.tests.test_live_tracker import _coral_frame
    p = str(tmp_path / "test_ab.avi")
    w = cv2.VideoWriter(p, cv2.VideoWriter_fourcc(*"MJPG"), 30.0, (640, 480))
    for k in range(30):
        w.write(_coral_frame((300 + k, 260)))
    w.release()

    cam = {"K": [[500.0, 0, 320], [0, 500.0, 240], [0, 0, 1]], "dist": [0.0] * 5,
           "R": [[1.0, 0, 0], [0, -1.0, 0], [0, 0, -1.0]], "t": [0.0, 0.0, 2.0]}
    with open(tmp_path / "cam.json", "w") as f:
        json.dump(cam, f)

    z_csv = tmp_path / "z.csv"
    with open(z_csv, "w") as f:
        f.write("t_s,z_m\\n0.0,0.4\\n0.5,0.2\\n1.0,0.1\\n")

    ret = replay_main([p, "--z-csv", str(z_csv)])
    assert ret == 0

    md_text = (tmp_path / "ab_summary.md").read_text()
    assert "A/B Replay Summary" in md_text
    assert "Detection Rate**: A =" in md_text
    assert "Zoom/Crop**:" in md_text
    assert "Draw**:" in md_text

    import pandas as pd
    df = pd.read_csv(tmp_path / "ab_frames.csv")
    assert "z_eff_b" in df.columns
    # Both runs detect the ring on at least 90% of frames
    lines = md_text.split("\\n")
    det_line = next(line for line in lines if "Detection Rate**: A =" in line)
    a_rate = float(det_line.split("A = ")[1].split("%")[0])
    b_rate = float(det_line.split("B = ")[1].split("%")[0])
    assert a_rate >= 90.0 and b_rate >= 90.0

    # Zoom B is land_zoom exactly when z <= land_z_m and zoom when z > land_z_m
    # Test script used zoom=1.0, land_zoom=2.0, land_z_m=0.30
    assert (df.loc[df["z_eff_b"] == 2.0, "t_s"] > 0.0).any()
    z_m_list = [0.4, 0.2, 0.1]
    def get_z_m(t_s):
        import numpy as np
        ts_list = [0.0, 0.5, 1.0]
        return np.interp(t_s, ts_list, z_m_list)

    df["z_m"] = df["t_s"].apply(get_z_m)
    assert (df.loc[df["z_m"] <= 0.30, "z_eff_b"] == 2.0).all()
    assert (df.loc[df["z_m"] > 0.30, "z_eff_b"] == 1.0).all()
"""
with open("ground_station/analysis/tests/test_live_tracker.py", "w") as f:
    f.write(ts)

print("done")
