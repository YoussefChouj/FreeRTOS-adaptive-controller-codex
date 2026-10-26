"""Camera latency, photon -> tracker frame read (the screen-flash test).

The laptop lies under the phone camera, screen up, and flashes black/white at random moments. The scrcpy window is
read the way live_tracker reads it (WindowSource, time.monotonic() after read()).
latency = t_read of the first frame showing the new state - t_draw. Includes the laptop panel lag (~1 refresh).
This is one part of the tracker's --delay-ms (sent to the firmware in the 0xFE frame for back-dating fixes);
add the tracker's read->send time (fixes.csv t_send - t_cap) to get the full value.

usage (repo dir, scrcpy window r5cam running, see docs/lab/COMMANDS.md "Camera delay"):
    python -X utf8 -m ground_station.lab.camlat [n_flashes=60] [window=r5cam] [out_dir=logs/camlat/<date_time>]
"""
import random
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from ground_station.analysis.live_tracker import WindowSource  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
W, H = 128, 72


def main(argv):
    n = int(argv[0]) if len(argv) > 0 else 60
    win = argv[1] if len(argv) > 1 else "r5cam"
    out = Path(argv[2]) if len(argv) > 2 else REPO / "logs" / "camlat" / datetime.now().strftime("%Y-%m-%d_%H%M")
    out.mkdir(parents=True, exist_ok=True)
    cap = WindowSource(win, wait_s=10)
    frames, t_reads, stop = [], [], threading.Event()

    def reader():
        while not stop.is_set():
            ok, f = cap.read()
            t = time.monotonic()
            if ok and f is not None:
                frames.append(cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (W, H), interpolation=cv2.INTER_AREA))
                t_reads.append(t)
            time.sleep(0.004)

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    cv2.namedWindow("flash", cv2.WND_PROP_FULLSCREEN)
    cv2.setWindowProperty("flash", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    try:
        cv2.setWindowProperty("flash", cv2.WND_PROP_TOPMOST, 1)
    except Exception:
        pass
    img = [np.zeros((720, 1280), np.uint8), np.full((720, 1280), 255, np.uint8)]
    draws, state = [], 0
    cv2.imshow("flash", img[0])
    cv2.waitKey(1)
    time.sleep(2.0)  # settle on black, camera exposure too
    print(f"flashing {n} times (~{n * 1.1:.0f} s), keep still, don't cover the laptop; Esc stops", flush=True)
    for _ in range(n):
        state ^= 1
        cv2.imshow("flash", img[state])
        cv2.waitKey(1)
        draws.append((time.monotonic(), state))
        t_end = time.monotonic() + random.uniform(0.8, 1.4)  # random gaps: no beat with 30 fps
        esc = False
        while time.monotonic() < t_end:
            if cv2.waitKey(20) == 27:
                esc = True
                break
        if esc:
            break
    time.sleep(1.5)
    stop.set()
    th.join(1)
    cv2.destroyAllWindows()
    if len(frames) < 10:
        sys.exit(f"only {len(frames)} frames read from window {win!r}: is scrcpy running?")

    G = np.stack(frames).reshape(len(frames), -1).astype(np.float32)
    tr = np.array(t_reads)
    td, sd = np.array([d[0] for d in draws]), np.array([d[1] for d in draws])

    def state_at(t):  # draw state shown at time t
        i = np.searchsorted(td, t, side="right") - 1
        return np.where(i >= 0, sd[np.clip(i, 0, None)], 0)

    best = (-1, 0, None)
    for lag in np.arange(0, 1.5, 1 / 60):  # the lag that splits pixels best = ROI
        lab = state_at(tr - lag).astype(bool)
        if lab.sum() < 10 or (~lab).sum() < 10:
            continue
        sc = np.abs(G[lab].mean(0) - G[~lab].mean(0))
        s = np.sort(sc)[-max(5, sc.size // 200):].mean()
        if s > best[0]:
            best = (s, lag, sc)
    _, lag0, sc = best
    if sc is None:
        sys.exit(f"no flash seen in {len(tr)} frames: is the laptop screen in the camera view?")
    roi = sc > 0.5 * sc.max()
    sig = G[:, roi].mean(1)
    lo, hi = np.percentile(sig, 10), np.percentile(sig, 90)
    b = (sig > (lo + hi) / 2).astype(int)
    lat = []
    for t, s in zip(td, sd):  # first frame read after the draw in the new state, held 2 frames
        j = np.nonzero((tr > t) & (np.r_[b[1:], b[-1]] == s) & (b == s))[0]
        j = j[tr[j] < t + 2 * 0.8]
        if len(j):
            lat.append(tr[j[0]] - t)
    lat = np.array(lat) * 1000
    dt = np.median(np.diff(tr)) * 1000
    print(f"window {win}: {len(tr)} reads, read dt {dt:.0f} ms; ROI {roi.sum()} px "
          f"(contrast {hi - lo:.0f} grey levels); rough lag {lag0 * 1000:.0f} ms")
    if len(lat):
        print(f"LATENCY photon->frame read: n {len(lat)}/{len(td)}  median {np.median(lat):.0f} ms  mean {lat.mean():.0f}  "
              f"p10 {np.percentile(lat, 10):.0f}  p90 {np.percentile(lat, 90):.0f}  std {lat.std():.0f}  "
              f"min {lat.min():.0f}  max {lat.max():.0f}")
    np.savez(out / "camlat.npz", tr=tr, td=td, sd=sd, sig=sig, lat=lat, roi=roi)
    fig, A = plt.subplots(1, 3, figsize=(16, 4.5), gridspec_kw={"width_ratios": [3, 1.2, 1.2]})
    A[0].plot(tr - td[0], sig, "C0", lw=1, label="camera: laptop screen brightness")
    A[0].step(td - td[0], lo + (hi - lo) * sd, "k", where="post", lw=1, alpha=.6, label="laptop drew (black/white)")
    A[0].set_xlabel("time (s)")
    A[0].set_ylabel("grey level")
    A[0].legend(fontsize=8)
    A[0].set_title("drawn vs seen")
    A[1].hist(lat, bins=20, color="C1")
    A[1].set_xlabel("latency (ms)")
    A[1].set_title(f"photon->read, median {np.median(lat) if len(lat) else 0:.0f} ms")
    A[2].imshow(sc.reshape(H, W), cmap="magma")
    A[2].contour(roi.reshape(H, W), [0.5], colors="c")
    A[2].set_title("where the flash is (ROI)")
    A[2].axis("off")
    fig.tight_layout()
    fig.savefig(out / "camlat.png", dpi=90)
    print("plot", out / "camlat.png")


if __name__ == "__main__":
    main(sys.argv[1:])
