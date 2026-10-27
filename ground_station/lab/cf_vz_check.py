"""Compare the altitude CF with range derivatives on the firmware clock.

Run ``python -m ground_station.lab.cf_vz_check SESSION_DIR [T0 T1]``.
The range derivative is a shared-sensor proxy, not independent flight truth.
T0/T1 are seconds after the first complete fast-slot frame; select the hover
plateau explicitly. With no bounds, the entire capture is analyzed.
Replay models the initialized, armed predict/correct branch, not the raw-range
gate or arm state machine. Missing ticks, resets and clamp information prevent
a claim of faithful replay; they are reported instead of interpolated away.
Lag is positive when CF follows ToF; the PROPOSED search is +/-0.5 s.
Oscillation search is 0.1-2 Hz. Sparse captures cannot resolve correction steps.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import numpy as np
from scipy import signal

FAST_FIELDS = (
    "xTickCount", "alt_cf.h", "alt_cf.vz", "alt_cf.a_up", "alt_cf.b_az",
    "alt_cf.e", "alt_cf.n_corr", "alt_cf.dt_c", "alt_cf.y_corr", "alt_cf.mode",
    "ano_of.alt_update_cnt", "ano_of.of_alt_cm", "ano_of.of2_h",
)
THROTTLE_FIELDS = ("Throttle_out", "throttle")
REPLAY_MODE_GROUPS = (("alt_cf.delay_ticks", "s_alt_delay_ticks", "g_alt_tof_delay_ticks"),
                      ("alt_cf.spread_mode", "s_alt_spread_mode", "g_alt_cf_spread_mode"))
REPLAY_MODE_FIELDS = tuple(key for group in REPLAY_MODE_GROUPS for key in group)
CORE_FIELDS = ("xTickCount", "alt_cf.h", "alt_cf.vz")
DT = 0.005  # s, firmware STAB_DT_S; not an estimated sample period
TS_DESIGN = 0.05  # s, PROPOSED rate-scaled design interval
DTC_MAX = 0.10  # s, PROPOSED maximum correction interval


def _increments(values: np.ndarray, bits: int) -> np.ndarray:
    """Unsigned counter differences, including wrap, without float subtraction."""
    v = np.rint(values).astype(np.uint64)
    return ((v[1:] - v[:-1]) & np.uint64((1 << bits) - 1)).astype(np.float64)


def read_fast_slot(session: Path) -> tuple[np.ndarray, dict[str, np.ndarray], int]:
    """Keep coherent frames together; never align values by CSV row index."""
    frames: dict[tuple[int, int], dict[str, float]] = {}
    with (session / "telemetry.csv").open(newline="", encoding="utf-8-sig") as src:
        rows = csv.reader(src)
        next(rows, None)
        for row in rows:
            try:
                stamp, slot = int(row[0]), int(row[1])
                key = re.sub(r"^slot\d+\.", "", row[2])
                if key in FAST_FIELDS + THROTTLE_FIELDS + REPLAY_MODE_FIELDS:
                    frames.setdefault((slot, stamp), {})[key] = float(row[3])
            except (ValueError, IndexError):
                continue
    candidates = {}
    for (slot, stamp), values in frames.items():
        if (all(key in values and np.isfinite(values[key]) for key in CORE_FIELDS)
                and any(np.isfinite(values.get(key, np.nan))
                        for key in ("ano_of.of2_h", "ano_of.of_alt_cm"))):
            candidates.setdefault(slot, []).append((stamp, values))
    if not candidates:
        raise ValueError("no complete CF fast-slot frames; capture xTickCount, alt_cf.h, alt_cf.vz and ToF together")
    slot, rows = max(candidates.items(), key=lambda item: len(item[1]))
    rows.sort(key=lambda row: row[0])  # receive order preserves firmware tick wrap
    # Optional diagnostics must be present in every selected coherent frame.
    values = {key: np.array([row[1][key] for row in rows])
              for key in FAST_FIELDS + THROTTLE_FIELDS + REPLAY_MODE_FIELDS
              if all(np.isfinite(row[1].get(key, np.nan)) for row in rows)}
    if not any(key in values for key in ("ano_of.of2_h", "ano_of.of_alt_cm")):
        raise ValueError("no consistently captured ToF field in selected slot; select one coherent capture")
    ticks = _increments(values["xTickCount"], 32)
    if np.any(ticks > 60000):
        raise ValueError("firmware clock reset/out-of-order frames; analyze one continuous boot segment")
    keep = np.r_[True, ticks > 0]
    values = {key: value[keep] for key, value in values.items()}
    t = np.r_[0.0, np.cumsum(_increments(values["xTickCount"], 32))] / 1000.0
    if len(t) < 32:
        raise ValueError("too few complete CF fast-slot frames for spectral analysis")
    return t, values, slot


def _correlation(x, y):
    if len(x) < 3 or np.std(x) < 1e-12 or np.std(y) < 1e-12:
        return None
    return float(np.clip(np.corrcoef(x, y)[0, 1], -1.0, 1.0))


def lag_metrics(x, y, fs):
    """Overlap Pearson search; positive lag means y follows x, not causation."""
    bound = min(round(0.5 * fs), (len(x) - 3) // 2)
    candidates = []
    for shift in range(-bound, bound + 1):
        a, b = ((x[:-shift], y[shift:]) if shift > 0 else
                (x[-shift:], y[:shift]) if shift < 0 else (x, y))
        r = _correlation(a, b)
        if r is not None:
            candidates.append((r, -abs(shift), shift))
    if not candidates:
        return None
    r, _, shift = max(candidates)
    return {"lag_s": shift / fs, "correlation": r,
            "resolution_s": 1 / fs, "at_search_boundary": abs(shift) == bound}


def oscillation_metrics(x, fs):
    """Dominant detrended period in the PROPOSED 0.1-2 Hz analysis band."""
    if np.std(signal.detrend(x)) < 1e-12:
        return None
    f, psd = signal.periodogram(x, fs=fs, window="hann", detrend="linear")
    band = np.flatnonzero((f >= 0.1) & (f <= min(2.0, fs / 2)))
    if not len(band):
        return None
    k = band[np.argmax(psd[band])]
    return {"peak_hz": float(f[k]), "period_s": float(1 / f[k]),
            "frequency_resolution_hz": float(fs / len(x)),
            "cycles_in_window": float(f[k] * len(x) / fs),
            "at_search_boundary": bool(k in (band[0], band[-1])),
            "std_m": float(np.std(x))}


def _step_summary(x):
    return {"count": len(x), "rms": float(np.sqrt(np.mean(x ** 2))),
            "p95_abs": float(np.percentile(np.abs(x), 95)),
            "max_abs": float(np.max(np.abs(x)))} if len(x) else None


def correction_metrics(t, values):
    """Isolate applied corrections only with adjacent 200 Hz state snapshots."""
    observed = {key: _step_summary(np.diff(values[key]))
                for key in ("alt_cf.h", "alt_cf.vz", "alt_cf.b_az") if key in values}
    required = ("alt_cf.n_corr", "alt_cf.a_up", "alt_cf.b_az")
    if any(key not in values for key in required) or not np.allclose(np.diff(t), DT, atol=0.0005, rtol=0):
        return {"status": "observed inter-frame differences only; hidden predictions/corrections cannot be separated",
                "observed_inter_frame": observed, "accepted_correction_tick_residuals": None}
    count = _increments(values["alt_cf.n_corr"], 32)
    if np.any(count > 1):
        return {"status": "counter reset/multiple corrections; applied steps unavailable",
                "observed_inter_frame": observed, "accepted_correction_tick_residuals": None}
    accepted = count == 1
    a = values["alt_cf.a_up"][1:]
    residuals = {"h_m": np.diff(values["alt_cf.h"]) - values["alt_cf.vz"][:-1] * DT - 0.5 * a * DT ** 2,
                 "vz_mps": np.diff(values["alt_cf.vz"]) - a * DT,
                 "b_az_mps2": np.diff(values["alt_cf.b_az"])}
    return {"status": "residuals after subtracting initialized-armed predictor; accepted ticks and all ticks reported separately for smoothing",
            "observed_inter_frame": observed,
            "prediction_residuals_all_ticks": {key: _step_summary(x) for key, x in residuals.items()},
            "accepted_correction_tick_residuals": {key: _step_summary(x[accepted]) for key, x in residuals.items()}}


def transfer_metrics(x: np.ndarray, y: np.ndarray, fs: float) -> dict:
    """Welch H1=Pxy/Pxx and H2=Pyy/conj(Pxy); geometric mean gain.

    Band summaries use input-power weights and circular phase averages. This
    avoids assigning empty FFT bins the same influence as an excited bin.
    All individual bins and coherence remain in JSON for assessment.
    """
    nperseg = min(len(x), max(32, int(round(fs * 10.24))))
    f, pxy = signal.csd(x, y, fs=fs, nperseg=nperseg)
    _, pxx = signal.welch(x, fs=fs, nperseg=nperseg)
    _, pyy = signal.welch(y, fs=fs, nperseg=nperseg)
    good = (pxx > np.max(pxx) * 1e-12) & (np.abs(pxy) > 0)
    h1 = np.divide(pxy, pxx, out=np.zeros_like(pxy), where=good)
    h2 = np.divide(pyy, np.conj(pxy), out=np.zeros_like(pxy), where=good)
    coherence = np.divide(np.abs(pxy) ** 2, pxx * pyy, out=np.zeros_like(pxx), where=good & (pyy > 0))
    geo = np.sqrt(np.abs(h1) * np.abs(h2))

    def summarize(lo, hi):
        band = (f >= lo) & (f <= hi) & good & (geo > 0)
        if not np.any(band):
            return None
        w = pxx[band] / np.sum(pxx[band])
        return {
            "h1_gain": float(np.exp(np.sum(w * np.log(np.abs(h1[band]))))),
            "h2_gain": float(np.exp(np.sum(w * np.log(np.abs(h2[band]))))),
            "geo_gain": float(np.exp(np.sum(w * np.log(geo[band])))),
            "phase_deg": float(np.degrees(np.angle(np.sum(w * np.exp(1j * np.angle(h1[band])))))),
            "h1_phase_deg": float(np.degrees(np.angle(np.sum(w * np.exp(1j * np.angle(h1[band])))))),
            "h2_phase_deg": float(np.degrees(np.angle(np.sum(w * np.exp(1j * np.angle(h2[band])))))),
            "coherence": float(np.sum(w * coherence[band])),
        }

    selected = (f >= 0.2) & (f <= 1.2)
    bins = [{"hz": float(f[i]), "h1_gain": float(abs(h1[i])), "h2_gain": float(abs(h2[i])),
             "geo_gain": float(geo[i]), "phase_deg": float(np.degrees(np.angle(h1[i]))),
             "coherence": float(coherence[i])} for i in np.flatnonzero(selected & good)]
    return {"band_0.2_1.2_hz": summarize(0.2, 1.2),
            "bands": {str(center): summarize(center - 0.05, center + 0.05) for center in (0.3, 0.5, 0.7, 0.9)},
            "bins": bins}


def replay_cf(values: dict[str, np.ndarray], mode: int) -> np.ndarray:
    """Float32 initialized-armed branch, seeded from the first logged state.

    a_up already subtracts bias. Undo with the PREVIOUS logged bias (the bias
    before that tick's correction), then subtract the replay's own bias. This
    enables a counterfactual second mode without feeding its bias back as truth.
    n_corr marks accepted corrections and y_corr is the actual gated input.
    """
    f = np.float32
    h, v, b = (f(values[key][0]) for key in ("alt_cf.h", "alt_cf.vz", "alt_cf.b_az"))
    out = np.empty(len(values["alt_cf.vz"]), dtype=np.float64)
    out[0] = v
    corrections = _increments(values["alt_cf.n_corr"], 32)
    for i in range(1, len(out)):
        raw = f(f(values["alt_cf.a_up"][i]) + f(values["alt_cf.b_az"][i - 1]))
        a = f(np.clip(f(raw - b), -20.0, 20.0))
        h = f(h + f(f(v * f(DT)) + f(f(f(f(0.5) * a) * f(DT)) * f(DT))))
        v = f(v + f(a * f(DT)))
        if corrections[i - 1] == 1:
            e = f(f(values["alt_cf.y_corr"][i]) - h)
            dt_c = f(np.clip(values["alt_cf.dt_c"][i], DT, DTC_MAX))
            k1, k2, k3 = (f(f(f(k) * dt_c) / f(TS_DESIGN)) if mode else f(k) for k in (0.21, 0.45, 0.1))
            h = f(h + f(k1 * e))
            v = f(v + f(k2 * e))
            b = f(np.clip(f(b - f(k3 * e)), -1.0, 1.0))
        out[i] = v
    return out


def analyze(t: np.ndarray, values: dict[str, np.ndarray], t0=None, t1=None) -> tuple[dict, dict]:
    t0 = float(t[0] if t0 is None else t0)
    t1 = float(t[-1] if t1 is None else t1)
    mask = (t >= t0) & (t <= t1)
    if np.sum(mask) < 32 or t1 <= t0:
        raise ValueError("hover window has insufficient data")
    t, values = t[mask], {key: value[mask] for key, value in values.items()}
    dts = np.diff(t)
    if np.any(dts <= 0) or not np.all(np.isfinite(t)):
        raise ValueError("firmware time must be finite and strictly increasing")
    if any(not np.all(np.isfinite(value)) for value in values.values()):
        raise ValueError("signal values must be finite")
    fs = 1.0 / np.median(dts)
    if np.max(dts) > 1.5 / fs:
        raise ValueError("capture has large gaps; select a continuous window instead of interpolating across gaps")
    grid = np.arange(t[0], t[-1] + 0.1 / fs, 1 / fs)
    uniform = {key: np.interp(grid, t, value) for key, value in values.items()}
    range_key = "ano_of.of_alt_cm" if "ano_of.of_alt_cm" in values else "ano_of.of2_h"
    h_range = uniform[range_key] / (100.0 if range_key.endswith("_cm") else 1.0)
    v_range = np.gradient(h_range, 1 / fs)
    a_range = np.gradient(v_range, 1 / fs)
    transfers = {
        "vz_over_d_range": transfer_metrics(v_range, uniform["alt_cf.vz"], fs),
        "h_over_range": transfer_metrics(h_range, uniform["alt_cf.h"], fs),
    }
    if "alt_cf.a_up" in uniform:
        transfers["a_up_over_d2_range"] = transfer_metrics(a_range, uniform["alt_cf.a_up"], fs)
    # Smooth the shared noisy/quantized sensor before differentiating it.
    # Identical zero-phase filters add no relative delay; trim edge transients.
    lag = {"tof_to_h": None, "d_tof_dt_to_vz": None}
    if fs > 4.0 and t[-1] - t[0] >= 4.0 and len(grid) > 40:
        sos = signal.butter(3, (0.1, 2.0), "bandpass", fs=fs, output="sos")
        filtered = [signal.sosfiltfilt(sos, signal.detrend(x))
                    for x in (h_range, uniform["alt_cf.h"], uniform["alt_cf.vz"])]
        edge = max(1, round(fs))
        inner = slice(edge, -edge)
        lag = {"tof_to_h": lag_metrics(filtered[0][inner], filtered[1][inner], fs),
               "d_tof_dt_to_vz": lag_metrics(np.gradient(filtered[0], 1 / fs)[inner], filtered[2][inner], fs)}
    bias_throttle = None
    throttle_key = next((key for key in THROTTLE_FIELDS if key in uniform), None)
    if throttle_key and "alt_cf.b_az" in uniform:
        bias, throttle = uniform["alt_cf.b_az"], uniform[throttle_key]
        bias_throttle = {"throttle_field": throttle_key, "raw_r": _correlation(bias, throttle),
                         "detrended_r": _correlation(signal.detrend(bias), signal.detrend(throttle)),
                         "interpretation": "association only; not evidence of thrust-caused bias"}
    elapsed = t[-1] - t[0]
    corr_delta = _increments(values["alt_cf.n_corr"], 32) if "alt_cf.n_corr" in values else None
    issues = []
    missing = [key for key in FAST_FIELDS if key not in values]
    if missing:
        issues.append("missing optional replay fields: " + ", ".join(missing))
    assumptions = []
    for group in REPLAY_MODE_GROUPS:
        # Actual latched diagnostics take precedence over pending requests.
        key = next((key for key in group if key in values), None)
        if key is not None and np.any(values[key] != 0):
            issues.append("unsupported delay/spread replay mode: " + key + " is nonzero")
        if key is None or key == group[-1]:
            assumptions.append(group[0] + " not logged; baseline replay assumes disabled; requested settings do not prove effective settings")
    if not np.all(np.abs(dts - DT) < 0.0005):
        issues.append("missing/non-200 Hz prediction snapshots: no interpolation can recover hidden inputs")
    if corr_delta is not None and np.any(corr_delta > 1):
        issues.append("multiple corrections or counter reset between snapshots")
    if "alt_cf.mode" in values and (len(np.unique(values["alt_cf.mode"])) != 1 or values["alt_cf.mode"][0] not in (0, 1)):
        issues.append("mode changed or invalid within window; use one armed segment")
    if "alt_cf.a_up" in values and np.any(np.abs(values["alt_cf.a_up"]) >= 20.0):
        issues.append("acceleration clamp loses pre-clamp input needed for counterfactual replay")
    replay = {}
    traces = {}
    for mode in (() if missing else (0, 1)):
        rv = replay_cf(values, mode)
        corr = float(np.corrcoef(rv, values["alt_cf.vz"])[0, 1]) if np.std(rv) > 1e-12 and np.std(values["alt_cf.vz"]) > 1e-12 else None
        replay[str(mode)] = {"correlation_vz": corr,
                             "rms_vz_error_mps": float(np.sqrt(np.mean((rv - values["alt_cf.vz"]) ** 2)))}
        traces["replay_" + str(mode)] = rv
    mode = str(int(values["alt_cf.mode"][0])) if "alt_cf.mode" in values else "unknown"
    faithful = not issues and replay.get(mode, {}).get("correlation_vz") is not None and replay[mode]["correlation_vz"] >= 0.95
    report = {"window_s": [float(t[0]), float(t[-1])], "frames": len(t), "firmware_sample_hz": float(fs),
              "of2_h_std_m": float(np.std(values["ano_of.of2_h"])) if "ano_of.of2_h" in values else None,
              "n_corr_hz": float(np.sum(corr_delta) / elapsed) if corr_delta is not None else None,
              "alt_update_cnt_hz": float(np.sum(_increments(values["ano_of.alt_update_cnt"], 8)) / elapsed) if "ano_of.alt_update_cnt" in values else None,
              "range_field": range_key, "missing_fields": missing,
              "lag": lag, "lag_method": "0.1-2 Hz zero-phase bandpass, 1 s edge trim, +/-0.5 s overlap Pearson; positive means CF follows ToF; null for flat/short/low-rate signals",
              "oscillation": {"tof": oscillation_metrics(h_range, fs), "cf_h": oscillation_metrics(uniform["alt_cf.h"], fs)},
              "bias_throttle": bias_throttle, "correction_steps": correction_metrics(t, values),
              "sampling_limits": {"nyquist_hz": float(fs / 2), "lag_resolution_s": float(1 / fs),
                                  "sparse": bool(not np.all(np.abs(dts - DT) < 0.0005)),
                                  "note": "25 Hz snapshots alias content above 12.5 Hz; cannot resolve 5 ms corrections or unknown sensor latency; interpolation adds no information"},
              "transfers": transfers, "replay": replay, "replay_issues": issues, "replay_assumptions": assumptions,
              "replay_faithful": faithful,
              "estimator_status": ("replay matches baseline under unverified delay/spread assumptions" if assumptions else "replay validated on this capture") if faithful else "estimator is still not understood",
              "decision_threshold": "PROPOSED replay correlation >= 0.95; other-mode replay is counterfactual, not flight evidence",
              "limitations": "range-derived reference shares sensor input; replay models initialized armed branch, not gate/reset logic"}
    traces.update(t=t, range_h=values[range_key] / (100.0 if range_key.endswith("_cm") else 1.0), logged_vz=values["alt_cf.vz"])
    return report, traces


def run(session: Path, t0=None, t1=None) -> dict:
    t, values, slot = read_fast_slot(session)
    report, traces = analyze(t, values, t0, t1)
    report["slot"] = slot
    (session / "cf_vz_check.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    fig, axes = plt.subplots(3, 1, figsize=(11, 9), constrained_layout=True)
    axes[0].plot(traces["t"], traces["range_h"])
    axes[0].set(ylabel="Range height (m)")
    for key, label in (("logged_vz", "logged"), ("replay_0", "legacy replay"), ("replay_1", "rate-scaled replay")):
        if key in traces:
            axes[1].plot(traces["t"], traces[key], label=label)
    axes[1].set(ylabel="Climb rate (m/s)")
    axes[1].legend()
    for name, metrics in report["transfers"].items():
        bins = metrics["bins"]
        axes[2].plot([b["hz"] for b in bins], [b["phase_deg"] for b in bins], marker=".", label=name)
    axes[2].set(xlabel="Frequency (Hz)", ylabel="H1 phase (deg)")
    axes[2].legend()
    fig.suptitle("CF telemetry / range proxy; " + report["estimator_status"])
    fig.savefig(session / "cf_vz_check.png", dpi=150)
    plt.close(fig)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_dir", type=Path)
    parser.add_argument("t0", type=float, nargs="?")
    parser.add_argument("t1", type=float, nargs="?")
    args = parser.parse_args(argv)
    try:
        report = run(args.session_dir, args.t0, args.t1)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"cf_vz_check: {exc}\n")
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
