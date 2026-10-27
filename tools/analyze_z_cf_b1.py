"""Offline B1 study; read original logs, write only the requested output directory.

Requires numpy, scipy, matplotlib. Windows are explicitly trimmed hover-only
intervals on the first coherent slot-1 firmware clock, not whole flights.
Analysis settings are choices, not measured sensor bandwidths.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import signal

CAMPAIGNS = ("231115", "231338", "231601", "231738")
WINDOWS = ((9.52, 37.52), (8.92, 36.92), (11.84, 39.84), (9.32, 37.36))
FIELDS = ("xTickCount", "ano_of.of_alt_cm", "ano_of.of2_h", "alt_cf.h",
          "alt_cf.vz", "alt_cf.b_az", "Throttle_out", "alt_cf.mode",
          "alt_cf.n_corr", "ano_of.alt_update_cnt")


def read_frames(session):
    frames = {}
    with (session / "telemetry.csv").open(newline="", encoding="utf-8-sig") as src:
        for row in csv.DictReader(src):
            if row["slot"] != "1" or not row["key"].startswith("slot1."):
                continue
            key = row["key"][6:]
            if key in FIELDS:
                frames.setdefault(int(row["received_ns"]), {})[key] = float(row["value"])
    rows = [frame for _, frame in sorted(frames.items()) if all(k in frame for k in FIELDS)]
    values = {key: np.array([frame[key] for frame in rows]) for key in FIELDS}
    if not rows or any(not np.all(np.isfinite(v)) for v in values.values()):
        raise ValueError("missing/nonfinite coherent slot-1 frames")
    t = (values["xTickCount"] - values["xTickCount"][0]) / 1000
    if np.any(np.diff(t) <= 0):
        raise ValueError("duplicate/reset firmware clock; cannot infer missing frames")
    return t, values


def bounded_lag(x, y, fs, bound=0.5):
    """Positive lag means y follows x; overlap-normalized Pearson, +/-bound s."""
    candidates = []
    for shift in range(-round(bound * fs), round(bound * fs) + 1):
        a, b = (x[:-shift], y[shift:]) if shift > 0 else (x[-shift:], y[:shift]) if shift < 0 else (x, y)
        candidates.append((float(np.corrcoef(a, b)[0, 1]), shift / fs))
    corr, lag = max(candidates)
    return {"lag_s": lag, "r": corr, "boundary": bool(abs(lag) >= np.floor(bound * fs) / fs)}


def spectrum(t, x, fs):
    centered = signal.detrend(x)
    f, psd = signal.periodogram(centered, fs, window="hann", detrend=False)
    band = (f >= 0.1) & (f <= 2.0)
    k = np.flatnonzero(band)[np.argmax(psd[band])]
    design = np.column_stack((np.ones(len(t)), t - t.mean(),
                              np.sin(2 * np.pi * f[k] * t), np.cos(2 * np.pi * f[k] * t)))
    fit = np.linalg.lstsq(design, x, rcond=None)[0]
    fast = (f >= 0.5) & (f <= 2.0)
    fast_k = np.flatnonzero(fast)[np.argmax(psd[fast])]
    fm, pm = signal.periodogram(x, fs, window="hann", detrend="constant")
    mean_k = np.flatnonzero(band)[np.argmax(pm[band])]
    global_k = np.flatnonzero((f > 0) & (f <= 2))[np.argmax(psd[(f > 0) & (f <= 2)])]
    local, _ = signal.find_peaks(psd)
    peaks = sorted((k for k in local if band[k]), key=lambda k: psd[k], reverse=True)[:3]
    return {"peak_hz": float(f[k]), "period_s": float(1 / f[k]),
            "lower_search_boundary": bool(k == np.flatnonzero(band)[0]),
            "full_positive_band_peak_period_s": float(1 / f[global_k]),
            "mean_only_detrend_peak_period_s": float(1 / fm[mean_k]),
            "local_peaks_hz": [float(f[k]) for k in peaks],
            "fast_band_peak_hz": float(f[fast_k]),
            "slow_band_power_fraction": float(psd[(f >= 0.1) & (f < 0.5)].sum() / psd[band].sum()),
            "sine_peak_m": float(np.hypot(fit[-2], fit[-1])),
            "std_m": float(np.std(x)), "p05_m": float(np.percentile(x, 5)),
            "p95_m": float(np.percentile(x, 95)),
            "min_m": float(x.min()), "max_m": float(x.max())}, f, psd


def analyze(t, values, window):
    mask = (t >= window[0] - 1e-8) & (t <= window[1] + 1e-8)
    t = t[mask]
    v = {k: a[mask] for k, a in values.items()}
    dt = np.diff(t)
    fs = 1 / np.median(dt)
    # Refuse gaps rather than manufacture values across them.
    if len(t) < 128 or not np.allclose(dt, 1 / fs, atol=0.0006):
        raise ValueError("hover is not uniformly sampled: split into contiguous segments")
    raw = v["ano_of.of_alt_cm"] / 100
    h = v["alt_cf.h"]
    sof, f, pof = spectrum(t, raw, fs)
    sh, _, ph = spectrum(t, h, fs)
    # PROPOSED analysis band 0.1-2 Hz. Filter height before differentiation;
    # zero-phase filtering adds no relative algorithmic lag to this comparison.
    sos = signal.butter(3, (0.1, 2.0), "bandpass", fs=fs, output="sos")
    filt = lambda a: signal.sosfiltfilt(sos, signal.detrend(a))
    edge = round(fs)  # trim one more second for filtering edge effects
    inner = slice(edge, -edge)
    fr, fh = filt(raw), filt(h)
    derivative = np.gradient(fr, 1 / fs)
    fv = filt(v["alt_cf.vz"])
    bias, throttle = v["alt_cf.b_az"], v["Throttle_out"]
    sensitivity = {}
    for trim in (0, 2):
        m = (t >= t[0] + trim) & (t <= t[-1] - trim)
        s, _, _ = spectrum(t[m], raw[m], fs)
        sensitivity[str(trim)] = s["period_s"]
    stats = {"window_s": [float(t[0]), float(t[-1])], "frames": len(t),
             "fs_hz": fs, "max_gap_s": float(dt.max()), "mode": np.unique(v["alt_cf.mode"]).tolist(),
             "raw_tof": sof, "cf_h": sh,
             "h_lag": bounded_lag(fr[inner], fh[inner], fs),
             "vz_lag": bounded_lag(derivative[inner], fv[inner], fs),
             "bias_throttle_raw_r": float(np.corrcoef(bias, throttle)[0, 1]),
             "bias_throttle_detrended_r": float(np.corrcoef(signal.detrend(bias), signal.detrend(throttle))[0, 1]),
             "bias_range_mps2": [float(bias.min()), float(bias.max())],
             "tof_peak_period_trim_sensitivity_s": sensitivity,
             "correction_rate_hz": float(np.diff(v["alt_cf.n_corr"]).sum() / (t[-1] - t[0])),
             "alt_update_cnt_rate_hz": float((np.diff(v["ano_of.alt_update_cnt"]) % 256).sum() / (t[-1] - t[0])),
             "tof_value_change_rate_hz": float(np.count_nonzero(np.diff(raw)) / (t[-1] - t[0]))}
    return stats, (t, v, raw, f, pof, ph, derivative, fv)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    fig, axes = plt.subplots(4, 3, figsize=(15, 13))
    for i, (stamp, window) in enumerate(zip(CAMPAIGNS, WINDOWS)):
        campaign = args.logs_root / "campaigns" / ("z-cf-hover_20261026-" + stamp)
        meta = json.loads((campaign / "metrics.json").read_text())["flights"][0]
        session = args.logs_root.parent / Path(meta["recording"].replace("\\", "/"))
        t, values = read_frames(session)
        stats, traces = analyze(t, values, window)
        stats.update(campaign=campaign.name, session=session.name,
                     campaign_hold_s=[meta["steps"][1]["t0_s"], meta["steps"][1]["t1_s"]])
        results.append(stats)
        t, v, raw, f, pof, ph, derivative, fv = traces
        ax = axes[i, 0]
        ax.plot(t, raw, label="raw ToF /100", alpha=0.7)
        ax.plot(t, v["alt_cf.h"], label="CF h")
        ax.set(ylabel=f"{stamp}: height (m)", xlabel="firmware elapsed (s)")
        ax.legend(fontsize=8)
        ax = axes[i, 1]
        ax.plot(f, pof, label="ToF")
        ax.plot(f, ph, label="CF h")
        ax.set(xlim=(0.05, 2), xlabel="frequency (Hz)", ylabel="PSD (m²/Hz)")
        ax.axvline(stats["raw_tof"]["peak_hz"], ls=":", color="black")
        ax.legend(fontsize=8)
        ax = axes[i, 2]
        ax.plot(t, v["alt_cf.b_az"], color="tab:blue", label="b_az")
        ax.set(xlabel="firmware elapsed (s)", ylabel="b_az (m/s²)")
        other = ax.twinx()
        other.plot(t, v["Throttle_out"], color="tab:orange", alpha=0.65)
        other.set_ylabel("Throttle_out", color="tab:orange")
        censor = " (right-censored slow peak)" if stats["raw_tof"]["lower_search_boundary"] else ""
        print(f"{stamp}: selected-bin period={stats['raw_tof']['period_s']:.3f}s{censor} mode={stats['mode']} "
              f"h lag={stats['h_lag']['lag_s']:.3f}s vz lag={stats['vz_lag']['lag_s']:.3f}s "
              f"bias/throttle r={stats['bias_throttle_raw_r']:.3f}")
    fig.tight_layout()
    fig.savefig(args.output_dir / "z-cf-rootcause-B1.png", dpi=150)
    plt.close(fig)
    (args.output_dir / "z-cf-rootcause-B1.json").write_text(json.dumps(results, indent=2) + "\n")
    print("B1: four measured recordings analyzed; no original log writes")


if __name__ == "__main__":
    main()
