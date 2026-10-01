#!/usr/bin/env python3
"""RPM/thrust analysis for f17 flights.

Computes k_T from hover RPM data, mass_hat, CW/CCW share, and roll/pitch
torque for a given channel-to-corner map.  Also supports ``--compare`` mode
to extract payload torque from loaded vs. unloaded flights.

CLI
---
::

    python -m ground_station.analysis.rpm_signals --logs logs/vofa

prints the k_T table for all f17 flights.

Usage
-----
::

    python -m ground_station.analysis.rpm_signals \
        --logs /home/agent/data/logs/vofa \
        --flight f17_hover_active15 \
        --mass 0.9885 \
        --arm 0.1414

    # Compare loaded vs. unloaded:
    python -m ground_station.analysis.rpm_signals \
        --compare loaded unloaded \
        --arm 0.1414
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SYSTEM_CORE_CLOCK = 168_000_000  # Hz (Cortex-M4 DWT clock)
GRAVITY_MS2 = 9.80665


def period_cyc_to_rpm(period_cyc: np.ndarray) -> np.ndarray:
    """Convert DWT cycle period to RPM.

    RPM = 60 * f_cpu / period_cyc  (per-revolution measurement).
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        rpm = (60.0 * SYSTEM_CORE_CLOCK) / period_cyc.astype(np.float64)
    rpm = np.where(np.isfinite(rpm), rpm, 0.0)
    rpm = np.where(rpm > 65535.0, 65535.0, rpm)
    return rpm


def load_slots(log_dir: Path, prefix: str, slots=(0, 1, 2, 3)):
    """Load slot CSVs for one flight prefix.

    Returns merged DataFrame joined on the fastest slot's t_src_ms.
    """
    dfs_dict = {}
    for slot in slots:
        f = log_dir / f"{prefix}.slot{slot}.csv"
        if f.exists():
            try:
                df = pd.read_csv(f)
                if not df.empty:
                    df.sort_values("t_src_ms", inplace=True)
                    dfs_dict[slot] = df
            except Exception:
                pass

    if not dfs_dict:
        return pd.DataFrame()

    seen_cols = set(["t_src_ms"])
    for slot in sorted(dfs_dict.keys()):
        df = dfs_dict[slot]
        keep_cols = ["t_src_ms"]
        for c in df.columns:
            if c not in seen_cols:
                keep_cols.append(c)
                seen_cols.add(c)
        dfs_dict[slot] = df[keep_cols]

    fastest_slot = max(dfs_dict.keys(), key=lambda s: len(dfs_dict[s]))
    base_df = dfs_dict[fastest_slot]

    for slot in sorted(dfs_dict.keys()):
        if slot == fastest_slot:
            continue
        df = dfs_dict[slot]
        dt = df["t_src_ms"].diff().median()
        tolerance = int(2 * dt) if pd.notna(dt) else 100

        base_df = pd.merge_asof(
            base_df,
            df,
            on="t_src_ms",
            direction="nearest",
            tolerance=tolerance
        )

    return base_df


def hover_mask(df: pd.DataFrame) -> pd.Series:
    """Return True for frames where the drone is stably hovering.

    Heuristic: FLYING phase (flight_phase==1 or s_state==1), altitude between 0.1 and 2.0 m,
    and voltage > 14 V (battery not deeply discharged).
    """
    phase = df.get("flight_phase")
    s_state = df.get("s_state")
    alt = df.get("ano_of.of_alt_cm")
    vbat = df.get("real_voltage")

    mask = pd.Series(True, index=df.index)
    if phase is not None:
        mask &= (phase == 1.0)
    elif s_state is not None:
        mask &= (s_state == 1.0)
        
    if alt is not None:
        mask &= (alt >= 10.0) & (alt <= 200.0)
    if vbat is not None:
        mask &= (vbat > 14.0)
    return mask


def mask_outliers_ch3(
    rpm_ch3: np.ndarray, window: int = 200, threshold_sigma: float = 3.0
) -> np.ndarray:
    """Mask ch3 outliers using a rolling median with sigma threshold.

    ch3 has 13-42% outlier rate (missed marks, extra edges). This applies a
    rolling median filter over `window` samples and masks anything more than
    `threshold_sigma` standard deviations from the median.
    """
    s = pd.Series(rpm_ch3)
    median_roll = s.rolling(window=window, min_periods=1, center=True).median()
    std_roll = s.rolling(window=window, min_periods=1, center=True).std()
    diff = (s - median_roll).abs()
    good = (std_roll == 0) | (diff <= threshold_sigma * std_roll)
    # Also mask zero values (stalled or no-signal frames)
    good = good & (s > 0)
    return good.values


def hover_thrust_id(
    df: pd.DataFrame,
    mass_kg: float,
    motor_of_ch: list[int] | None = None,
    arm_m: float = 0.1414,
    ch3_sigma: float = 3.0,
    ch3_window: int = 200,
) -> dict:
    """Compute hover thrust parameters from a DataFrame.

    Parameters
    ----------
    df :
        Combined slot data for one flight (output of ``load_slots``).
    mass_kg :
        Assumed drone mass in kg (sets the k_T calibration reference).
    motor_of_ch :
        Mapping [ch0, ch1, ch2, ch3] -> [M1, M2, M3, M4] motor numbers.
        If None, uses [0, 1, 2, 3] identity (channels = motors).
    arm_m :
        Motor arm length in metres (for torque computation).
    ch3_sigma, ch3_window :
        Outlier masking for ch3 (passed to ``mask_outliers_ch3``).

    Returns
    -------
    dict with keys:
        k_T, mass_hat, cw_share, sum_w2, rpm_mean, torque_roll, torque_pitch,
        torque_payload (only in --compare mode).
    """
    if motor_of_ch is None:
        motor_of_ch = [0, 1, 2, 3]

    mask = hover_mask(df)
    if not mask.any():
        return {"error": "no hover frames found"}

    hover = df[mask]
    n_frames = hover.shape[0]
    if n_frames < 10:
        return {"error": "too few hover frames"}

    # Read RPM period cycles from slot2 columns.
    rpm_cyc = np.column_stack([
        hover.get(f"rpm_dbg_period_cyc[{i}]").values.astype(np.float64)
        for i in range(4)
    ])
    rpm = period_cyc_to_rpm(rpm_cyc)

    # Omega in rad/s: omega = RPM * 2*pi / 60
    omega = rpm * 2.0 * np.pi / 60.0  # shape (N, 4)

    # Mask ch3 outliers
    ch3_good = mask_outliers_ch3(rpm[:, 3], window=ch3_window, threshold_sigma=ch3_sigma)
    rpm_masked = rpm.copy()
    rpm_masked[~ch3_good, :] = 0.0
    omega_masked = omega.copy()
    omega_masked[~ch3_good, :] = 0.0

    # sum(omega^2) — use only non-masked samples
    w2 = omega_masked ** 2  # shape (N, 4)
    sum_w2 = w2.sum(axis=1)  # shape (N,)

    # CW pair = ch0 + ch1 (M1 + M2), CCW = ch2 + ch3 (M3 + M4)
    cw_w2 = w2[:, 0] + w2[:, 1]
    ccw_w2 = w2[:, 2] + w2[:, 3]

    # Use median for robust statistics
    med_sum_w2 = np.median(sum_w2)
    med_cw_share = np.median(cw_w2 / (sum_w2 + 1e-12))
    rpm_mean = np.median(rpm, axis=0)

    # Calibrated k_T: k_T = m*g / sum_w2 at hover
    k_T = mass_kg * GRAVITY_MS2 / med_sum_w2  # N s^2 / rad^2

    # Mass estimate: m_hat = k_T * sum_w2 / (g + a_z)
    acc_z = hover.get("Lin_Acc_Z_body").values * GRAVITY_MS2 / 1000.0  # mg -> m/s^2
    g_plus_az = GRAVITY_MS2 + acc_z
    g_plus_az = np.clip(g_plus_az, 1.0, None)  # avoid div by zero
    mass_hat_vals = k_T * sum_w2 / g_plus_az
    med_mass_hat = np.median(mass_hat_vals)

    # Roll/pitch torque from CW/CCW imbalance
    # tau_roll = arm * (T_CCW - T_CW) * sign (simplified: no corner map)
    # tau_pitch = arm * (T_front - T_rear) * sign
    # Without corner map, compute differential torque from RPM asymmetry.
    # T_i = k_T * omega_i^2
    med_torque_roll = arm_m * k_T * np.median(w2[:, 2] + w2[:, 3] - w2[:, 0] - w2[:, 1])
    med_torque_pitch = arm_m * k_T * np.median(
        w2[:, 0] + w2[:, 2] - w2[:, 1] - w2[:, 3]
    )

    result: dict = {
        "mass_kg": mass_kg,
        "k_T": float(k_T),
        "mass_hat_kg": float(med_mass_hat),
        "sum_w2": float(med_sum_w2),
        "cw_share": float(med_cw_share),
        "rpm_mean": [float(r) for r in rpm_mean],
        "torque_roll_Nm": float(med_torque_roll),
        "torque_pitch_Nm": float(med_torque_pitch),
        "n_hover_frames": int(n_frames),
        "hover_seconds": float((hover["t_src_ms"].values[-1] - hover["t_src_ms"].values[0]) / 1000.0) if len(hover) > 1 else 0.0,
        "n_masked_ch3": int((~ch3_good).sum()),
    }
    return result


def compare_payload(
    df_loaded: pd.DataFrame,
    df_unloaded: pd.DataFrame,
    arm_m: float = 0.1414,
) -> dict:
    """Compute payload torque from loaded vs. unloaded hover data.

    The difference in CW/CCW torque share cancels per-motor k_T bias,
    giving a robust payload torque estimate.
    """
    loaded = hover_thrust_id(df_loaded, mass_kg=0.0, arm_m=arm_m)
    unloaded = hover_thrust_id(df_unloaded, mass_kg=0.0, arm_m=arm_m)

    if "error" in loaded or "error" in unloaded:
        return {"error": "hover_thrust_id failed"}

    # Torque difference = payload torque (approximate)
    delta_roll = loaded["torque_roll_Nm"] - unloaded["torque_roll_Nm"]
    delta_pitch = loaded["torque_pitch_Nm"] - unloaded["torque_pitch_Nm"]

    return {
        "torque_roll_delta_Nm": float(delta_roll),
        "torque_pitch_delta_Nm": float(delta_pitch),
        "payload_magnitude_Nm": float(np.sqrt(delta_roll**2 + delta_pitch**2)),
        "loaded": loaded,
        "unloaded": unloaded,
    }


def compute_rpm(
    period: list[float],
    edges: list[float],
    hold_window: int = 10,
) -> list[float]:
    """Convert per-sample period_cyc + edges to RPM, with stale detection.

    Parameters
    ----------
    period :
        Per-sample DWT cycle period (from ``rpm_dbg_period_cyc``).
    edges :
        Per-sample edge count (from ``rpm_dbg_edges``).
    hold_window :
        Number of consecutive samples with no edge increase after which
        the RPM is marked stale (NaN).

    Returns
    -------
    List of RPM values; NaN when period <= 0 or edges are frozen.
    """
    import math

    rpms: list[float] = []
    n = len(period)
    stale_count = 0

    for i in range(n):
        p = period[i] if i < n else 0.0
        e = edges[i] if i < n else 0.0

        # Zero or negative period -> NaN
        if p <= 0.0:
            rpms.append(float("nan"))
            stale_count = 0
            continue

        # Basic RPM computation
        rpm = (60.0 * SYSTEM_CORE_CLOCK) / p
        if rpm > 65535.0:
            rpm = 65535.0

        # Stale detection: if edges haven't changed for hold_window samples
        if i > 0:
            if e == edges[i - 1]:
                stale_count += 1
            else:
                stale_count = 0

        if stale_count >= hold_window:
            rpm = float("nan")

        rpms.append(rpm)

    return rpms


def rpm_metrics(rpms: list[float]) -> dict:
    """Compute summary metrics from an RPM series.

    Parameters
    ----------
    rpms :
        List of RPM values (may contain NaN).

    Returns
    -------
    Dict with keys: mean, std, max, min, stale_fraction.
    """
    import math

    valid = [r for r in rpms if math.isfinite(r) and r > 0]
    total = len(rpms)

    if total == 0 or len(valid) == 0:
        return {"mean": 0.0, "std": 0.0, "max": 0.0, "min": 0.0, "stale_fraction": 1.0}

    stale_frac = 1.0 - len(valid) / total
    return {
        "mean": float(np.mean(valid)),
        "std": float(np.std(valid)),
        "max": float(np.max(valid)),
        "min": float(np.min(valid)),
        "stale_fraction": float(stale_frac),
    }


def asymmetry_index(motor_means: dict[int, float]) -> float | None:
    """Compute thrust asymmetry index from per-motor mean RPM.

    Parameters
    ----------
    motor_means :
        Dict mapping motor number -> mean RPM.

    Returns
    -------
    Asymmetry index = (max_mean - min_mean) / overall_mean, or None
    if fewer than 2 motors have valid means.
    """
    valid_means = {k: v for k, v in motor_means.items() if v > 0}
    if len(valid_means) < 2:
        return None

    vals = list(valid_means.values())
    overall_mean = sum(vals) / len(vals)
    if overall_mean <= 0:
        return None

    return (max(vals) - min(vals)) / overall_mean


def print_kt_table(flights: list[tuple[str, Path]], mass_kg: float) -> None:
    """Print a k_T table for all f17 flights.
    """
    print(f"mass is an ASSUMPTION (not weighed): {mass_kg} kg\n")
    rows = []
    for prefix, log_dir in flights:
        df = load_slots(log_dir, prefix)
        if df.empty:
            print(f"Skip {prefix}: no data", file=sys.stderr)
            continue
            
        rpm_cols = [c for c in df.columns if c.startswith("rpm_dbg_period_cyc")]
        if not rpm_cols:
            print(f"Skip {prefix}: no rpm columns", file=sys.stderr)
            continue
            
        result = hover_thrust_id(df, mass_kg=mass_kg)
        if "error" in result:
            print(f"Skip {prefix}: {result['error']}", file=sys.stderr)
            continue
            
        if result["n_hover_frames"] < 200:
            print(f"Skip {prefix}: fewer than 200 hover rows", file=sys.stderr)
            continue
            
        # Shorten prefix (e.g. keep first 25 chars or similar? The prompt just says "shortened")
        # Let's just use prefix[:30] or so. Or "f17_hover_active15" etc.
        # Actually, let's truncate to 30 chars.
        short_prefix = prefix if len(prefix) <= 35 else prefix[:32] + "..."
            
        rows.append(
            {
                "flight": short_prefix,
                "k_T": f'{result["k_T"]:.2e}',
                "mass_hat": f'{result["mass_hat_kg"]:.3f}',
                "rpm_m1": f'{result["rpm_mean"][0]:.0f}',
                "rpm_m2": f'{result["rpm_mean"][1]:.0f}',
                "rpm_m3": f'{result["rpm_mean"][2]:.0f}',
                "rpm_m4": f'{result.get("rpm_mean", [0,0,0,0])[3] if len(result.get("rpm_mean", [])) > 3 else 0:.0f}',
                "frames": result["n_hover_frames"],
                "hover_s": f'{result["hover_seconds"]:.1f}',
            }
        )

    if not rows:
        print("No hover data found.", file=sys.stderr)
        return

    print()
    df_out = pd.DataFrame(rows)
    print(df_out.to_string(index=False))
    print()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="RPM/thrust analysis for f17 flights")
    parser.add_argument(
        "--logs",
        type=Path,
        default=Path("logs/vofa"),
        help="Directory containing f17_* slot CSVs (default: logs/vofa)",
    )
    parser.add_argument(
        "--flight",
        type=str,
        default=None,
        help="Single flight prefix (e.g. f17_hover_active15). If set, --logs is scanned.",
    )
    parser.add_argument(
        "--mass",
        type=float,
        default=0.9885,
        help="Drone mass in kg for k_T calibration (default: 0.9885)",
    )
    parser.add_argument(
        "--arm",
        type=float,
        default=0.1414,
        help="Motor arm length in metres (default: 0.1414)",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("LOADED", "UNLOADED"),
        help="Compare two flights: LOADED and UNLOADED prefixes.",
    )

    args = parser.parse_args(argv)

    if args.compare:
        loaded_prefix, unloaded_prefix = args.compare
        loaded_dir = args.logs / loaded_prefix
        unloaded_dir = args.logs / unloaded_prefix

        df_loaded = load_slots(loaded_dir, loaded_prefix)
        df_unloaded = load_slots(unloaded_dir, unloaded_prefix)

        result = compare_payload(df_loaded, df_unloaded, arm_m=args.arm)
        if "error" in result:
            print(f"ERROR: {result['error']}", file=sys.stderr)
            sys.exit(1)

        print("\n=== Payload torque (loaded - unloaded) ===")
        print(f"  Roll torque delta : {result['torque_roll_delta_Nm']:.4f} N m")
        print(f"  Pitch torque delta: {result['torque_pitch_delta_Nm']:.4f} N m")
        print(f"  Payload magnitude : {result['payload_magnitude_Nm']:.4f} N m")

        print("\n--- Loaded hover ---")
        for k, v in result["loaded"].items():
            if isinstance(v, float):
                print(f"  {k}: {v:.6g}")
            else:
                print(f"  {k}: {v}")

        print("\n--- Unloaded hover ---")
        for k, v in result["unloaded"].items():
            if isinstance(v, float):
                print(f"  {k}: {v:.6g}")
            else:
                print(f"  {k}: {v}")

    else:
        # k_T table mode
        flights = []
        if args.logs.is_dir():
            # Old per-directory path
            for d in sorted(args.logs.iterdir()):
                if d.is_dir() and d.name.startswith("f17"):
                    flights.append((d.name, d))
            # Flat layout discovery via .meta.json
            for m in sorted(args.logs.glob("f17_*.meta.json")):
                prefix = m.name[:-10]
                flights.append((prefix, args.logs))
                
        if not flights and args.logs.is_dir() and args.logs.name.startswith("f17"):
            flights.append((args.logs.name, args.logs))

        print_kt_table(flights, mass_kg=args.mass)


if __name__ == "__main__":
    main()