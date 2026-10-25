"""D5: build truth4 (offline guard-ring camera truth) for every flight of one lab day, so nn_io can train on it.

A flight qualifies when logs/video_truth/<name>/ has cam.mkv + fixes.csv + scrcpy_start_host_time.txt, no truth4.json yet,
and its camera started on DAY. The telemetry stem is the first logs/sessions/<DAY>-HHMMSS-lab-* dir that starts within
15 min after the camera and whose name holds every '-' token of the deck id (the part after '-lab-'; the *-alone dirs say
e-exp-pid-u0, their sessions e-exp-pid-3l-u0). Session stamps are in the frame of the ISO field of
scrcpy_start_host_time.txt, not localtime(epoch) (5 h apart on this laptop). The floor is the last floor-<DAY>* scan whose
cam.mkv ended before the camera epoch; a scan that never saw the origin marker 0 (10-21: -6, -11) is replaced by the
latest earlier scan that did, only when their shared markers moved under MAX_SHIFT_PX (the camera did not move).
A day whose only scan came after its flights (10-24) uses that scan only when the static markers in the first HEAD_S s
of each flight's own video sit within MAX_SHIFT_PX of where the scan saw them (cached as <flight>/aruco_head.npz).
Run from the repo root: PYTHONPATH=. python tools/d5_truth4_batch.py [--day 2026-10-21] [--run]   (no --run = print the map)
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from ground_station.analysis.video_truth import aruco_scan, start_file, static_corners, video_file

MAX_SHIFT_PX = 3.0
HEAD_S = 20.0            # flight video head scanned for static markers: the drone is still parked
HEAD_MAX_MAD_PX = 1.0    # a marker counts as static in that head when its corners jitter under this (PROPOSED)


def corners(d, npz="aruco.npz", max_mad=np.inf):
    z = np.load(f"{d}/{npz}")
    out = {}
    for k in z.files:
        if k[:1] == "c" and k[1:].isdigit():
            c, mad = static_corners(z[k])
            if c is not None and mad <= max_mad:
                out[k] = c
    return out


def head_corners(vd):
    """Markers that held still in the first HEAD_S s of a flight video (drone parked), cached as aruco_head.npz."""
    if not os.path.exists(f"{vd}/aruco_head.npz"):
        aruco_scan(video_file(vd), step=5, t1=HEAD_S, out=f"{vd}/aruco_head.npz")
    return corners(vd, "aruco_head.npz", HEAD_MAX_MAD_PX)


def after_floor(floors, t0, vd):
    """(floor dir, note) when every scan came after the flight (10-24): the earliest later scan with marker 0, only
    if the static markers of this flight's own video sit where that scan saw them (the camera did not move)."""
    for d in [d for t, d in floors if t > t0]:
        c = corners(d)
        if "c0" not in c:
            continue
        v = head_corners(vd)
        shared = sorted(set(c) & set(v))
        if len(shared) < 2:
            return None, f"no scan before the camera; only {len(shared)} static marker shared with {Path(d).name}"
        shift = float(np.median([np.linalg.norm(c[k] - v[k], axis=-1) for k in shared]))
        if shift > MAX_SHIFT_PX:
            return None, f"no scan before the camera; camera moved {shift:.1f} px before {Path(d).name}"
        return d, f"later scan {Path(d).name}: {len(shared)} static markers of this video moved {shift:.1f} px"
    return None, "no scan before the camera and no later scan has marker 0"


def pick_floor(floors, t0, vd=None):
    """(floor dir, note): the last scan before t0, or the latest earlier one with marker 0 if the camera held still;
    with no scan before t0, a later one if the flight video proves the camera held still (after_floor)."""
    before = [d for t, d in floors if t <= t0]
    if not before:
        return after_floor(floors, t0, vd) if vd else (None, "no scan before the camera")
    last = corners(before[-1])
    if "c0" in last:
        return before[-1], ""
    for d in reversed(before[:-1]):
        c = corners(d)
        if "c0" not in c:
            continue
        shared = sorted(set(c) & set(last))
        if not shared:
            return None, f"{Path(before[-1]).name} lacks marker 0, no shared marker with {Path(d).name}"
        shift = float(np.median([np.linalg.norm(c[k] - last[k], axis=-1) for k in shared]))
        if shift > MAX_SHIFT_PX:
            return None, f"{Path(before[-1]).name} lacks marker 0, camera moved {shift:.1f} px since {Path(d).name}"
        return d, f"marker 0 from {Path(d).name}, {len(shared)} shared markers moved {shift:.1f} px"
    return None, f"{Path(before[-1]).name} lacks marker 0 and no earlier scan has it"


def plan(day):
    ymd = day.replace("-", "")
    floors = sorted((os.path.getmtime(f"{d}/cam.mkv"), d) for d in glob.glob(f"logs/video_truth/floor-{day}*")
                    if os.path.exists(f"{d}/aruco.npz") and os.path.exists(f"{d}/cam.mkv"))
    sess = []
    for s in glob.glob(f"logs/sessions/{ymd}-*"):
        sess.append((datetime.strptime(Path(s).name[:15], "%Y%m%d-%H%M%S"), s))
    sess.sort()
    out = []
    for vd in sorted(glob.glob("logs/video_truth/*/")):
        vd = vd.rstrip("/\\")
        name = Path(vd).name
        st = Path(vd, "scrcpy_start_host_time.txt")
        if "-lab-" not in name or not (Path(vd, "cam.mkv").exists() and Path(vd, "fixes.csv").exists() and st.exists()):
            continue
        if Path(vd, "truth4.json").exists():
            continue
        ep, iso = start_file(vd).read_text().split()[:2]   # a restarted part (cam_k.mkv) has its own start stamp
        t0, d0 = float(ep), datetime.fromisoformat(iso)
        if d0.strftime("%Y-%m-%d") != day:
            continue
        toks = set(name.split("-lab-", 1)[1].split("-"))
        stem = next((s for t, s in sess if d0 - timedelta(seconds=60) <= t <= d0 + timedelta(minutes=15)
                     and toks <= set(Path(s).name.split("_")[0].split("-"))), None)
        floor, note = pick_floor(floors, t0, vd)
        out.append((name, stem, floor, d0, note))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="2026-10-21")
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    rows = plan(a.day)
    for name, stem, floor, t0, note in rows:
        print(f"{t0:%H:%M:%S}  {name:45} {stem or 'NO STEM'}  {floor or 'NO FLOOR'}  {note}", flush=True)
    if not a.run:
        return
    for name, stem, floor, _, _ in rows:
        if not (stem and floor):
            continue
        t = time.time()
        r = subprocess.run([sys.executable, "-m", "ground_station.analysis.video_truth", "truth4", name, "--stem", stem,
                            "--floor", floor, "--fixes"], capture_output=True, text=True)
        # the reason is the last stderr line that is neither an ffmpeg demuxer note nor a numpy warning
        err = [ln for ln in (r.stderr + r.stdout).splitlines() if ln.strip() and "matroska" not in ln.lower()
               and "Warning" not in ln and not ln.startswith("  ")]
        tail = err[-1:] if r.returncode else []
        js = Path(f"logs/video_truth/{name}/truth4.json")
        keys = ("flight_s", "coral_lost_frac", "pad_jitter_cm", "truth_land_cm", "sync_anchor")
        got = {k: json.loads(js.read_text()).get(k) for k in keys} if js.exists() else {}
        print(f"truth4 {name}: rc={r.returncode} {time.time() - t:.0f}s {got} {' '.join(tail)}", flush=True)


if __name__ == "__main__":
    main()
