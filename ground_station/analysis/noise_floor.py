"""Still-bench IMU noise floor from a livewatch `watch --csv` log.

    python -m ground_station.analysis.noise_floor logs/livewatch/noise_floor_<stamp>.csv [--min-hz 150]

Reports per gyro/accel column: achieved rate, mean (residual bias), std (noise), and the Allan deviation
minimum (bias instability) with its tau. Writes <csv stem>_noise.png and <csv stem>_noise.json next to the CSV.

Columns understood (any subset):
  ORI_Gyrox/y/z   deg/s, after the boot offset, BEFORE the 50 Hz Butterworth (API/bmi088_driver.c)
  Gyro_X/Y/Z_Real rad/s, after the LPF; imu_update.c adds the Mahony feedback to it in place
  ORI_Accx/y/z    accelerometer after the boot offset (mg)
Only the stretch where the host saw at least --min-hz samples per second is analysed, so a log that switches
telemetry mode part-way through is scored on its fast segment alone.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np

GYRO_DEG = ("ORI_Gyrox", "ORI_Gyroy", "ORI_Gyroz")
GYRO_RAD = ("Gyro_X_Real", "Gyro_Y_Real", "Gyro_Z_Real")
ACC = ("ORI_Accx", "ORI_Accy", "ORI_Accz")


def load(path: Path) -> dict[str, np.ndarray]:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return {k: np.array([float(r[k]) for r in rows]) for k in rows[0]}


def fast_segment(t: np.ndarray, min_hz: float) -> slice:
    """Longest run of whole seconds whose sample count is at least min_hz."""
    sec = np.floor(t - t[0]).astype(int)
    counts = np.bincount(sec)
    ok = counts >= min_hz
    best, start, run = (0, 0), None, 0
    for i, good in enumerate(ok):
        if good:
            start = i if start is None else start
            if i - start + 1 > best[1] - best[0]:
                best = (start, i + 1)
        else:
            start = None
    if best == (0, 0):
        return slice(0, 0)
    idx = np.nonzero((sec >= best[0]) & (sec < best[1]))[0]
    return slice(int(idx[0]), int(idx[-1]) + 1)


def allan(x: np.ndarray, fs: float) -> tuple[np.ndarray, np.ndarray]:
    """Overlapping Allan deviation of a rate signal sampled at fs."""
    n = len(x)
    theta = np.cumsum(x) / fs
    ms = np.unique(np.logspace(0, math.log10(max(1, n // 9)), 40).astype(int))
    taus, adev = [], []
    for m in ms:
        tau = m / fs
        d = theta[2 * m:] - 2 * theta[m:-m] + theta[:-2 * m]
        if len(d) < 2:
            break
        taus.append(tau)
        adev.append(math.sqrt(np.mean(d * d) / (2 * tau * tau)))
    return np.array(taus), np.array(adev)


def psd(x: np.ndarray, fs: float, nseg: int = 512) -> tuple[np.ndarray, np.ndarray]:
    """Welch PSD (Hann, 50 % overlap), one-sided, units^2/Hz."""
    nseg = min(nseg, len(x))
    w = np.hanning(nseg)
    step = nseg // 2
    segs = [x[i:i + nseg] - np.mean(x[i:i + nseg]) for i in range(0, len(x) - nseg + 1, step)]
    p = np.mean([np.abs(np.fft.rfft(s * w)) ** 2 for s in segs], axis=0) / (fs * np.sum(w * w))
    p[1:-1] *= 2
    return np.fft.rfftfreq(nseg, 1 / fs), p


def analyse(path: Path, min_hz: float) -> dict:
    d = load(path)
    t = d["t"]
    seg = fast_segment(t, min_hz)
    ts = t[seg]
    fs = (len(ts) - 1) / (ts[-1] - ts[0]) if len(ts) > 1 else float("nan")
    out = {"csv": str(path), "rows": int(len(t)), "segment_s": float(ts[-1] - ts[0]) if len(ts) > 1 else 0.0,
           "segment_rows": int(len(ts)), "achieved_hz": round(fs, 1), "columns": {}}
    if len(ts) < 2:
        return out | {"_seg": seg, "_fs": fs, "_d": d}
    for col in GYRO_DEG + GYRO_RAD + ACC:
        if col not in d:
            continue
        x = d[col][seg] * (180 / math.pi if col in GYRO_RAD else 1.0)
        unit = "mg" if col in ACC else "deg/s"
        taus, adev = allan(x, fs)
        k = int(np.argmin(adev)) if len(adev) else 0
        out["columns"][col] = {
            "unit": unit, "mean": float(np.mean(x)), "std": float(np.std(x)),
            "p2p": float(np.ptp(x)),
            "adev_min": float(adev[k]) if len(adev) else None,
            "adev_min_tau_s": float(taus[k]) if len(taus) else None,
        }
    for col in ("Gyro_Z_Offset", "g_gyro_z_bias_blocks"):
        if col in d:
            out[col] = {"first": float(d[col][0]), "last": float(d[col][-1])}
    return out | {"_seg": seg, "_fs": fs, "_d": d}


def plot(res: dict, png: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    d, seg, fs = res["_d"], res["_seg"], res["_fs"]
    t = d["t"][seg] - d["t"][seg][0]
    fig, ax = plt.subplots(2, 2, figsize=(13, 8))
    for col in GYRO_DEG:
        if col in d:
            ax[0, 0].plot(t, d[col][seg], lw=0.4, label=col)
            f, p = psd(d[col][seg], fs)
            ax[1, 0].semilogy(f[1:], np.sqrt(p[1:]), lw=0.8, label=col)
            taus, adev = allan(d[col][seg], fs)
            ax[1, 1].loglog(taus, adev, label=col)
    for col in GYRO_RAD:
        if col in d:
            ax[0, 1].plot(t, d[col][seg] * 180 / math.pi, lw=0.4, label=col + " (deg/s)")
            taus, adev = allan(d[col][seg] * 180 / math.pi, fs)
            ax[1, 1].loglog(taus, adev, "--", label=col)
    ax[0, 0].set_title("gyro before 50 Hz LPF (ORI_Gyro*, deg/s)")
    ax[0, 1].set_title("gyro after LPF (Gyro_*_Real, deg/s)")
    ax[1, 0].set_title(f"amplitude spectral density, fs {fs:.0f} Hz (deg/s/sqrt(Hz))")
    ax[1, 0].set_xlabel("Hz")
    ax[1, 1].set_title("Allan deviation (deg/s); minimum = bias instability")
    ax[1, 1].set_xlabel("tau (s)")
    for a in ax.flat:
        a.grid(True, which="both", alpha=0.3)
        a.legend(fontsize=7)
    ax[0, 0].set_xlabel("s")
    ax[0, 1].set_xlabel("s")
    fig.tight_layout()
    fig.savefig(png, dpi=110)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv", type=Path)
    ap.add_argument("--min-hz", type=float, default=150.0,
                    help="analyse only the longest stretch with at least this many samples per second")
    a = ap.parse_args(argv)
    res = analyse(a.csv, a.min_hz)
    if res["segment_rows"] < 100:
        print(f"no stretch at >= {a.min_hz} Hz; rerun with a lower --min-hz")
        return 1
    plot(res, a.csv.with_name(a.csv.stem + "_noise.png"))
    clean = {k: v for k, v in res.items() if not k.startswith("_")}
    a.csv.with_name(a.csv.stem + "_noise.json").write_text(json.dumps(clean, indent=1))
    print(f"segment {clean['segment_s']:.1f} s, {clean['segment_rows']} rows, {clean['achieved_hz']} Hz")
    print(f"{'column':14s} {'unit':6s} {'mean':>9s} {'std':>8s} {'p2p':>8s} {'adev_min':>9s} {'@tau s':>7s}")
    for col, c in clean["columns"].items():
        print(f"{col:14s} {c['unit']:6s} {c['mean']:9.4f} {c['std']:8.4f} {c['p2p']:8.3f} "
              f"{c['adev_min']:9.5f} {c['adev_min_tau_s']:7.2f}")
    for col in ("Gyro_Z_Offset", "g_gyro_z_bias_blocks"):
        if col in clean:
            print(f"{col}: {clean[col]['first']:.5g} -> {clean[col]['last']:.5g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
