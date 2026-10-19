"""Offline replay of a flight against an NN checkpoint."""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from ground_station.analysis.nn_io import (
    RATE_HZ,
    any_slot_loader,
    corrected,
    load_model,
    predict,
    seq,
)
from ground_station.analysis.video_truth import NN4_OF_SCALE, drift_score


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stem", help="Prefix for slot CSVs (e.g. flight name or full path without .slotX.csv)")
    ap.add_argument("--truth", required=True, help="Truth CSV with t_s, x_m, y_m (or truth4 flight name)")
    ap.add_argument("--ckpt", required=True, help="Path to checkpoint file")
    ap.add_argument("--out", default=".", help="Output directory")
    a = ap.parse_args(argv)

    # Load model
    try:
        model = load_model(a.ckpt)
    except Exception as e:
        print(f"Error loading checkpoint: {e}", file=sys.stderr)
        return 1

    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not a.truth.endswith(".csv"):
        name = a.truth
        if not (Path(f"logs/video_truth/{name}/truth4.csv").exists()):
            print(f"Error: truth4 flight {name} not found in logs/video_truth/", file=sys.stderr)
            return 1

        try:
            d = seq(name, RATE_HZ)
        except Exception as e:
            print(f"Error loading data: {e}", file=sys.stderr)
            return 1
    else:
        # Load external flight
        name = Path(a.truth).stem
        try:
            truth_df = pd.read_csv(a.truth)
            t_s = truth_df["t_s"].values
            truth = truth_df["x_m"].values + 1j * truth_df["y_m"].values

            # Reconstruct the seq dictionary dynamically.
            # We don't have earth track, back or yaw, so we leave earth = 0 and back = 1 for simple replay evaluation
            # Wait, drift_score requires them. Actually drift_score takes `name` of a truth4 flight.
            # Since the user requested any-slot loader offline replay, we need to load from the stem.

            ts = np.arange(t_s[0], t_s[-1], 1.0 / RATE_HZ)
            F = any_slot_loader(a.stem, ts, RATE_HZ)

            d = {
                "ts": ts,
                "t": t_s,
                "truth": truth,
                "earth": np.zeros_like(truth), # no EKF earth track
                "back": np.ones(len(ts), dtype=complex), # assume aligned
                "R": np.zeros(len(ts), dtype=complex), # no earth error
                "F": F,
            }
            # v_col from F: raw OF in cm/s, x NN4_OF_SCALE = body m/s (nn4_data's "of")
            from ground_station.analysis.nn_io import V_COL
            d["v"] = (d["F"][:, V_COL[0]] + 1j * d["F"][:, V_COL[1]]) * NN4_OF_SCALE

        except Exception as e:
            print(f"Error loading external flight: {e}", file=sys.stderr)
            return 1

    p = predict(model, d)

    if not a.truth.endswith(".csv"):
        res = drift_score(name, ests={"earth": np.c_[d["earth"].real, d["earth"].imag], "nn": corrected(d, p)})
        f = {k: round(e["drift60s_p90_cm"], 1) for k, e in res["est"].items() if "drift60s_p90_cm" in e}
        earth_d60 = f.get("earth")
        nn_d60 = f.get("nn")
    else:
        # Fallback metric for arbitrary track
        earth_d60 = 0.0
        # Compute drift60 for NN correction directly
        dts = np.diff(d["ts"], prepend=d["ts"][0])
        corr_int = np.cumsum((p[:, 0] + 1j * p[:, 1]) * d["back"] * dts)
        ks, h = int(RATE_HZ), int(60 * RATE_HZ)
        es = np.convolve(corr_int - d["R"], np.ones(ks) / ks, "valid")
        if len(es) > h:
            nn_d60 = round(float(np.percentile(np.abs(es[h:] - es[:-h]), 90) * 100), 1)
        else:
            nn_d60 = float('nan')

    # Metrics
    # residual |p|
    p_mag = np.linalg.norm(p, axis=1) * 100 # cm/s
    p_mag_m_s = np.linalg.norm(p, axis=1) # m/s

    metrics = {
        "drift60s_p90_cm": {"earth": earth_d60, "nn": nn_d60},
        "residual_p_mag_cm_s": {
            "p50": round(float(np.percentile(p_mag, 50)), 1),
            "p90": round(float(np.percentile(p_mag, 90)), 1),
            "p99": round(float(np.percentile(p_mag, 99)), 1),
            "max": round(float(np.max(p_mag)), 1)
        },
        "frac_above_5cm_s": round(float(np.mean(p_mag_m_s > 0.05)), 3)
    }

    # Save md
    md = f"""# NN Replay Report
- Checkpoint: `{a.ckpt}`
- Flight: `{name}`

## Drift (60s p90, cm)
- EKF earth: {earth_d60}
- NN corrected: {nn_d60}

## Residual |p| (cm/s)
- p50: {metrics['residual_p_mag_cm_s']['p50']}
- p90: {metrics['residual_p_mag_cm_s']['p90']}
- p99: {metrics['residual_p_mag_cm_s']['p99']}
- max: {metrics['residual_p_mag_cm_s']['max']}
- Fraction > 5 cm/s: {metrics['frac_above_5cm_s']:.3f}
"""
    (out_dir / "nn_replay.md").write_text(md)
    (out_dir / "nn_replay.json").write_text(json.dumps(metrics, indent=2))

    # PNGs
    try:
        import matplotlib.pyplot as plt
        plt.figure(figsize=(8, 6))
        plt.plot(d["truth"].real, d["truth"].imag, label="Truth", color="black", linestyle="--")
        if not a.truth.endswith(".csv"):
            plt.plot(d["earth"].real, d["earth"].imag, label="EKF Earth", alpha=0.7)
            c = corrected(d, p)
            plt.plot(c[:, 0], c[:, 1], label="NN Corrected", alpha=0.7)
        else:
            # Just plot truth and integrated drift for arbitrary track
            c = d["truth"] + np.interp(d["t"], d["ts"], corr_int)
            plt.plot(c.real, c.imag, label="NN Corrected Track", alpha=0.7)
        plt.legend()
        plt.title("XY Tracks")
        plt.xlabel("X (m)")
        plt.ylabel("Y (m)")
        plt.grid(True)
        plt.savefig(out_dir / "tracks.png")
        plt.close()

        # Plot |p| and drift vs time
        plt.figure(figsize=(10, 6))

        # d60 computation over time
        drift = np.abs(es[h:] - es[:-h]) * 100 if len(es) > h else np.zeros(0)

        plt.plot(d["ts"], p_mag, label="|p| (cm/s)", alpha=0.5)
        if len(drift) > 0:
            plt.plot(d["ts"][h + ks - 1:], drift, label="NN Drift 60s (cm)", color="red")
        plt.legend()
        plt.title("|p| and Drift vs Time")
        plt.xlabel("Time (s)")
        plt.grid(True)
        plt.savefig(out_dir / "p_and_drift.png")
        plt.close()
    except Exception as e:
        print(f"Warning: could not plot PNGs: {e}", file=sys.stderr)

    return 0

if __name__ == "__main__":
    sys.exit(main())
