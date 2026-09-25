"""One-click flight-test report generator.

Usage::

    python -m ground_station.analysis.flight_report <session_dir> [--out DIR]
    python -m ground_station.analysis.flight_report --compare dirA dirB ...

Reads the long-form ``telemetry.csv`` produced by CsvRecorder and produces:

  * Segmented metrics (RMSE, MAE, overshoot, settling time, control effort, ...)
  * Publication-quality plots (PNG 300 dpi + PDF, serif, colour-blind safe)
  * Human-readable summary markdown
  * Organised metadata / metrics JSON + CSV

When invoked as a module (``python -m ground_station.analysis.flight_report``),
the ``__main__`` block handles CLI dispatch.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Optional imports — gracefully degrade when dependencies are missing
# ---------------------------------------------------------------------------
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import matplotlib.font_manager as font_manager
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

try:
    import numpy as np
    HAS_NP = True
except ImportError:
    HAS_NP = False

try:
    from scipy import signal as sp_signal
    from scipy import stats as sp_stats
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

# ---------------------------------------------------------------------------
# Signal map — try YAML, fall back to built-in defaults
# ---------------------------------------------------------------------------

# Default signal map matching the keys found in sample sessions
DEFAULT_SIGNALS: dict[str, list[str]] = {
    "roll": ["roll_deg", "status.roll_deg"],
    "pitch": ["pitch_deg", "status.pitch_deg"],
    "yaw": ["yaw_deg", "status.yaw_deg"],
    "roll_sp": ["roll_sp", "status.roll_sp"],
    "pitch_sp": ["pitch_sp", "status.pitch_sp"],
    "yaw_sp": ["yaw_sp", "status.yaw_sp"],
    "gyro_x": ["gyro_x", "c.gyro_x", "slot1.Gyro_X_Real"],
    "gyro_y": ["gyro_y", "c.gyro_y"],
    "gyro_z": ["gyro_z", "c.gyro_z"],
    "gyro_x_sp": ["gyro_x_sp", "gyrox_sp"],
    "gyro_y_sp": ["gyro_y_sp", "gyroy_sp"],
    "gyro_z_sp": ["gyro_z_sp", "gyroz_sp"],
    "pid_out_roll": ["pid.gyrox.U", "pid.gyrox.FB"],
    "pid_out_pitch": ["pid.gyroy.U", "pid.gyroy.FB"],
    "pid_out_yaw": ["pid.gyroz.U", "pid.gyroz.FB"],
    "mrac_active": ["mrac_state"],
    "theta_roll": ["mrac.roll.theta_0", "mrac.roll.theta_1", "mrac.roll.theta_2",
                   "mrac.roll.theta_3", "mrac.roll.theta_4", "mrac.roll.theta_5"],
    "theta_pitch": ["mrac.pitch.theta_0", "mrac.pitch.theta_1", "mrac.pitch.theta_2",
                    "mrac.pitch.theta_3", "mrac.pitch.theta_4", "mrac.pitch.theta_5"],
    "theta_yaw": ["mrac.yaw.theta_0", "mrac.yaw.theta_1", "mrac.yaw.theta_2",
                  "mrac.yaw.theta_3", "mrac.yaw.theta_4", "mrac.yaw.theta_5"],
    "u_ad_roll": ["mrac.roll.u_ad"],
    "u_ad_pitch": ["mrac.pitch.u_ad"],
    "u_ad_yaw": ["mrac.yaw.u_ad"],
    "xm_roll": [],
    "xm_pitch": [],
    "xm_yaw": [],
    "motor1": [],
    "motor2": [],
    "motor3": [],
    "motor4": [],
    "throttle": ["c.altitude_cm", "gs_throttle_max_pct"],
    "vbat": ["real_voltage", "status.vbat", "vbat"],
    "arm": ["arm", "slot0.DroneStatus.ARM_Status", "s_state"],
    "flymode": ["flymode", "slot0.DroneStatus.FlyMode", "flight_phase"],
    "pos_x": ["pos_x", "ekf.vel_x"],
    "pos_y": ["pos_y", "ekf.vel_y"],
    "pos_z": ["pos_z", "ekf.vel_z"],
    "pos_x_sp": [],
    "pos_y_sp": [],
    "pos_z_sp": [],
    "rpm_period_cyc1": ["slot2.rpm_dbg_period_cyc[0]"],
    "rpm_period_cyc2": ["slot2.rpm_dbg_period_cyc[1]"],
    "rpm_period_cyc3": ["slot2.rpm_dbg_period_cyc[2]"],
    "rpm_period_cyc4": ["slot2.rpm_dbg_period_cyc[3]"],
    "rpm_edges1": ["slot2.rpm_dbg_edges[0]"],
    "rpm_edges2": ["slot2.rpm_dbg_edges[1]"],
    "rpm_edges3": ["slot2.rpm_dbg_edges[2]"],
    "rpm_edges4": ["slot2.rpm_dbg_edges[3]"],
}


def _load_signal_map(session_dir: Path) -> dict[str, list[str]]:
    """Load signal map from YAML if present; otherwise return built-in defaults.

    Searches for the preset YAML in three locations:
      1. session_dir.parent / "flight_signals.yaml"  (session-level preset)
      2. sibling "flight_signals.yaml" in the analysis package
         directory (project-level preset, e.g. used by dashboard), but ONLY
         when the session is under the same project root as the preset
      3. falls back to DEFAULT_SIGNALS

    YAML values may be a single key string or a list of fallback keys.
    Both formats are normalised to ``list[str]`` so that
    ``compute_metrics`` can iterate them uniformly.
    """
    # 1. session-level preset
    yaml_path = session_dir.parent / "flight_signals.yaml"
    if yaml_path.exists():
        data = _read_yaml_file(yaml_path)
        if data is not None:
            return _normalize_signal_map(data)

    # 2. project-level preset (ground_station/analysis/flight_signals.yaml)
    #    Only when the session is under the same project root.
    pkg_dir = Path(__file__).resolve().parent
    project_yaml = pkg_dir / "flight_signals.yaml"
    if project_yaml.exists():
        # Check if session_dir is under the same project root
        project_root = pkg_dir.parents[2]  # analysis/ground_station/<root>
        try:
            session_dir.resolve().relative_to(project_root.resolve())
            # Session is under project root — use the project-level preset
            data = _read_yaml_file(project_yaml)
            if data is not None:
                return _normalize_signal_map(data)
        except ValueError:
            # Session is outside the project root — skip project-level preset
            pass

    return dict(DEFAULT_SIGNALS)


def _read_yaml_file(path: Path) -> dict[str, Any] | None:
    """Return parsed YAML or None on failure."""
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


def _normalize_signal_map(data: dict[str, Any]) -> dict[str, list[str]]:
    """Convert every value in the signal map to a list of strings."""
    result: dict[str, list[str]] = {}
    for role, val in data.items():
        if isinstance(val, list):
            result[role] = [str(v) for v in val]
        elif isinstance(val, str):
            result[role] = [val]
        else:
            result[role] = []
    return result


# ---------------------------------------------------------------------------
# CSV reader — long-form (received_ns, slot, key, value)
# ---------------------------------------------------------------------------

def read_telemetry_csv(
    session_dir: Path,
) -> list[tuple[int, int, str, float]]:
    """Read the long-form telemetry CSV.

    Returns list of (received_ns, slot, key, value).
    """
    csv_path = session_dir / "telemetry.csv"
    if not csv_path.exists():
        return []
    rows: list[tuple[int, int, str, float]] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            return []
        ts_idx = header.index("received_ns") if "received_ns" in header else -1
        slot_idx = header.index("slot") if "slot" in header else -1
        key_idx = header.index("key") if "key" in header else -1
        val_idx = header.index("value") if "value" in header else -1
        if key_idx < 0 or val_idx < 0:
            return []
        for row in reader:
            if len(row) <= max(i for i in (val_idx, key_idx, slot_idx, ts_idx) if i >= 0):
                continue
            try:
                ts = int(row[ts_idx]) if ts_idx >= 0 and row[ts_idx] else 0
                slot = int(row[slot_idx]) if slot_idx >= 0 and row[slot_idx] else 0
                key = row[key_idx].strip()
                val = float(row[val_idx])
                rows.append((ts, slot, key, val))
            except (ValueError, IndexError):
                continue
    return rows


def read_manifest(session_dir: Path) -> dict[str, Any]:
    """Read manifest.json if present."""
    manifest_path = session_dir / "manifest.json"
    if manifest_path.exists():
        try:
            with open(manifest_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


# ---------------------------------------------------------------------------
# Pivoting — key -> (timestamps, values)
# ---------------------------------------------------------------------------

def pivot(
    telemetry: list[tuple[int, int, str, float]],
) -> dict[str, tuple[list[int], list[float]]]:
    """Group telemetry by key. Returns {key: (ns_list, val_list)}."""
    buckets: dict[str, list[tuple[int, float]]] = {}
    for ts, _slot, key, val in telemetry:
        buckets.setdefault(key, []).append((ts, val))
    result: dict[str, tuple[list[int], list[float]]] = {}
    for key, pairs in buckets.items():
        pairs.sort(key=lambda p: p[0])
        result[key] = ([p[0] for p in pairs], [p[1] for p in pairs])
    return result


# ---------------------------------------------------------------------------
# Segmentation — armed / airborne window
# ---------------------------------------------------------------------------

def _find_arming_window(pivoted: dict[str, tuple[list[int], list[float]]]) -> tuple[int | None, int | None]:
    """Try to find armed/unarmed transitions from ``arm``-related keys."""
    arm_key = None
    for candidate in ("arm", "status.arm", "slot0.DroneStatus.ARM_Status", "s_state"):
        if candidate in pivoted:
            arm_key = candidate
            break
    if arm_key is None:
        return None, None
    timestamps, values = pivoted[arm_key]
    armed_idx = None
    for i, v in enumerate(values):
        if v >= 1.0:
            armed_idx = i
            break
    if armed_idx is None:
        return None, None
    # Armed window: first armed to last armed (or end of log)
    armed_start = timestamps[armed_idx]
    armed_end = None
    for i in range(len(values) - 1, -1, -1):
        if values[i] >= 1.0:
            armed_end = timestamps[i]
            break
    return armed_start, armed_end


def _find_airborne_window(
    pivoted: dict[str, tuple[list[int], list[float]]],
    armed_start: int | None,
    armed_end: int | None,
    throttle_threshold: float = 2.0,
) -> tuple[int | None, int | None]:
    """Find airborne window within armed period using throttle/motor keys."""
    throttle_key = None
    for candidate in ("throttle", "motor1", "motor2", "motor3", "motor4"):
        if candidate in pivoted:
            throttle_key = candidate
            break
    if throttle_key is None:
        return armed_start, armed_end
    timestamps, values = pivoted[throttle_key]
    # Use armed_start/armed_end as boundaries if available, otherwise whole log
    if armed_start is not None and armed_end is not None:
        start_mask = lambda ts: ts >= armed_start
        end_mask = lambda ts: ts <= armed_end
    else:
        start_mask = lambda ts: True
        end_mask = lambda ts: True

    airborne_start = None
    airborne_end = None
    first_airborne = None
    last_airborne = None

    for i, (ts, v) in enumerate(zip(timestamps, values)):
        if start_mask(ts) and end_mask(ts) and v > throttle_threshold:
            if first_airborne is None:
                first_airborne = i
        else:
            if first_airborne is not None:
                last_airborne = i
                break

    if first_airborne is not None:
        airborne_start = timestamps[first_airborne]
        airborne_end = timestamps[min(last_airborne if last_airborne else len(values) - 1, len(values) - 1)]
    return airborne_start, airborne_end


def segment_telemetry(
    telemetry: list[tuple[int, int, str, float]],
    pivoted: dict[str, tuple[list[int], list[float]]],
) -> dict[str, Any]:
    """Return segmentation info and the airborne window subset.

    Returns:
        {
            "armed_start_ns": int|None,
            "armed_end_ns": int|None,
            "airborne_start_ns": int|None,
            "airborne_end_ns": int|None,
            "used_whole_log": bool,
            "was_armed": bool,
            "mrac_split": bool,
        }
    """
    armed_start, armed_end = _find_arming_window(pivoted)
    airborne_start, airborne_end = _find_airborne_window(pivoted, armed_start, armed_end)

    # If drone was never armed, use the whole log
    was_armed = armed_start is not None
    used_whole_log = not was_armed

    # Check if mrac_active is present for splitting
    mrac_key = None
    for candidate in ("mrac_state", "mrac_active"):
        if candidate in pivoted:
            mrac_key = candidate
            break

    return {
        "armed_start_ns": armed_start,
        "armed_end_ns": armed_end,
        "airborne_start_ns": airborne_start,
        "airborne_end_ns": airborne_end,
        "used_whole_log": used_whole_log,
        "was_armed": was_armed,
        "mrac_split": mrac_key is not None,
    }


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def _rms(values: list[float]) -> float:
    if not values:
        return 0.0
    return math.sqrt(sum(v * v for v in values) / len(values))


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return statistics.stdev(values)


def _iteae(error_abs: list[float], dt_list: list[float]) -> float:
    """ITAE = sum(|e(t)| * t * dt)."""
    total = 0.0
    for i, (e, dt) in enumerate(zip(error_abs, dt_list)):
        total += e * (i + 1) * dt
    return total


def _overshoot_and_settling(
    error: list[float],
    values: list[float],
    settling_threshold_ratio: float = 0.02,
) -> dict[str, float]:
    """Estimate overshoot and 2% settling time for step-like signals.

    Returns overshoot_pct and settling_time_s (time to settle within 2% of final value).
    """
    if len(values) < 10:
        return {"overshoot_pct": 0.0, "settling_time_s": 0.0}

    final_val = values[-1] if values else 0.0
    min_val = min(values)
    max_val = max(values)
    settling_threshold = abs(final_val) * settling_threshold_ratio

    # Overshoot: how far above final value the signal goes
    overshoot = max(0.0, max_val - final_val) if final_val > 0 else max(0.0, final_val - min_val)
    range_val = max_val - min_val if max_val != min_val else 1.0
    overshoot_pct = (overshoot / range_val * 100) if range_val > 0 else 0.0

    # Settling time: time from start until signal stays within 2% of final
    settled = False
    settle_idx = len(values)
    for i in range(len(values)):
        if abs(values[i] - final_val) <= settling_threshold:
            # Check next N samples stay settled
            check_count = min(20, len(values) - i)
            if all(abs(values[i + j] - final_val) <= settling_threshold for j in range(check_count)):
                settled = True
                settle_idx = i
                break

    return {
        "overshoot_pct": round(overshoot_pct, 4),
        "settling_time_s": settle_idx,  # index; caller converts to seconds
    }


def compute_metrics(
    pivoted: dict[str, tuple[list[int], list[float]]],
    segmentation: dict[str, Any],
    signal_map: dict[str, list[str]],
    telemetry: list[tuple[int, int, str, float]] | None = None,
) -> dict[str, Any]:
    """Compute all flight-test metrics.

    Returns dict with keys: attitude, rate, control_effort, motor_imbalance,
    mrac, vbat_sag, telemetry_quality, missing_signals.
    """
    metrics: dict[str, Any] = {
        "attitude": {},
        "rate": {},
        "control_effort": {},
        "motor_imbalance": {},
        "mrac": {},
        "vbat_sag": {},
        "rpm": {},
        "telemetry_quality": {},
        "missing_signals": [],
    }

    # Resolve which signals are actually available
    available_signals: dict[str, str | None] = {}
    for role, keys in signal_map.items():
        for k in keys:
            if k in pivoted:
                available_signals[role] = k
                break
        else:
            if role not in available_signals:
                metrics["missing_signals"].append(role)
                available_signals[role] = None

    # Attitude tracking (roll, pitch, yaw)
    for axis in ("roll", "pitch", "yaw"):
        sp_key = available_signals.get(f"{axis}_sp")
        me_key = available_signals.get(axis)
        if sp_key is None or me_key is None:
            continue
        sp_ts, sp_vals = pivoted[sp_key]
        me_ts, me_vals = pivoted[me_key]

        # Align by time (simple: use whichever has fewer samples, match by nearest)
        aligned_errors = []
        for i, mts in enumerate(me_ts):
            best_j = None
            best_dt = float("inf")
            for j, sts in enumerate(sp_ts):
                dt = abs(mts - sts)
                if dt < best_dt:
                    best_dt = dt
                    best_j = j
            if best_j is not None and best_dt < 1e9:  # 1 second tolerance
                aligned_errors.append(me_vals[i] - sp_vals[best_j])

        if not aligned_errors:
            continue

        e_abs = [abs(e) for e in aligned_errors]
        n = len(e_abs)
        dt_vals = [1.0] * n  # placeholder; actual dt from timestamps
        if len(me_ts) >= 2:
            avg_dt_ns = statistics.median(me_ts[i+1] - me_ts[i] for i in range(len(me_ts)-1))
            avg_dt_s = avg_dt_ns / 1e9 if avg_dt_ns > 0 else 1.0
            dt_vals = [avg_dt_s] * n

        rmse = math.sqrt(sum(e * e for e in aligned_errors) / n) if n else 0.0
        mae = sum(e_abs) / n if n else 0.0
        max_abs = max(e_abs) if e_abs else 0.0
        iteae = _iteae(e_abs, dt_vals)
        steady_std = _stdev(aligned_errors[-50:]) if len(aligned_errors) >= 50 else _stdev(aligned_errors)

        overshoot_result = _overshoot_and_settling(
            aligned_errors, me_vals
        )

        metrics["attitude"][axis] = {
            "rmse": round(rmse, 6),
            "mae": round(mae, 6),
            "max_abs_error": round(max_abs, 6),
            "iteae": round(iteae, 4),
            "steady_state_std": round(steady_std, 6),
            "overshoot_pct": overshoot_result["overshoot_pct"],
            "settling_time_s": overshoot_result["settling_time_s"],
        }

    # Rate tracking (gyro_x, gyro_y, gyro_z)
    for axis_idx, axis_name in enumerate(("roll", "pitch", "yaw")):
        gyro_key = available_signals.get(f"gyro_{axis_idx+1 if axis_idx < 2 else 'z'}") or available_signals.get(f"gyro_{axis_name}")
        gyro_sp_key = available_signals.get(f"gyro_{axis_name}_sp")
        if gyro_key is None or gyro_sp_key is None:
            continue
        g_ts, g_vals = pivoted[gyro_key]
        s_ts, s_vals = pivoted[gyro_sp_key]

        aligned_errors = []
        for i, gts in enumerate(g_ts):
            best_j = None
            best_dt = float("inf")
            for j, sts in enumerate(s_ts):
                dt = abs(gts - sts)
                if dt < best_dt:
                    best_dt = dt
                    best_j = j
            if best_j is not None and best_dt < 1e9:
                aligned_errors.append(g_vals[i] - s_vals[best_j])

        if not aligned_errors:
            continue

        n = len(aligned_errors)
        e_abs = [abs(e) for e in aligned_errors]
        rmse = math.sqrt(sum(e * e for e in aligned_errors) / n) if n else 0.0
        mae = sum(e_abs) / n if n else 0.0
        max_abs = max(e_abs) if e_abs else 0.0

        metrics["rate"][axis_name] = {
            "rmse": round(rmse, 6),
            "mae": round(mae, 6),
            "max_abs_error": round(max_abs, 6),
        }

    # Control effort (PID outputs and motors)
    pid_outputs = []
    for suffix in ("roll", "pitch", "yaw"):
        u_key = available_signals.get(f"pid_out_{suffix}")
        if u_key and u_key in pivoted:
            _, vals = pivoted[u_key]
            pid_outputs.extend(vals)

    motor_keys = []
    for i in range(1, 5):
        mk = available_signals.get(f"motor{i}")
        if mk:
            motor_keys.append(mk)

    motor_values: list[list[float]] = []
    for mk in motor_keys:
        if mk in pivoted:
            motor_values.append(pivoted[mk][1])

    metrics["control_effort"] = {
        "pid_rms": round(_rms(pid_outputs), 6),
        "pid_total_variation": round(
            sum(abs(pid_outputs[i] - pid_outputs[i-1]) for i in range(1, len(pid_outputs))) if len(pid_outputs) > 1 else 0.0, 4
        ),
        "motor_count": len(motor_values),
    }

    # Motor imbalance
    if motor_values:
        motor_means = [_mean(mv) for mv in motor_values]
        max_mean = max(motor_means) if motor_means else 0
        min_mean = min(motor_means) if motor_means else 0
        metrics["motor_imbalance"] = {
            "per_motor_mean": [round(m, 4) for m in motor_means],
            "max_offset": round(abs(max_mean - min_mean), 4),
            "imbalance_pct": round(
                abs(max_mean - min_mean) / (abs(max_mean) + abs(min_mean)) * 100
                if (abs(max_mean) + abs(min_mean)) > 0 else 0.0, 4
            ),
        }

    # MRAC metrics
    mrac_active_key = available_signals.get("mrac_active")
    if mrac_active_key and mrac_active_key in pivoted:
        mrac_ts, mrac_vals = pivoted[mrac_active_key]
        mrac_active_indices = [i for i, v in enumerate(mrac_vals) if v >= 1.0]
        mrac_split = len(mrac_active_indices) > 0
    else:
        mrac_split = False

    for axis in ("roll", "pitch", "yaw"):
        u_ad_key = available_signals.get(f"u_ad_{axis}")
        theta_keys = [k for k in pivoted.keys() if f"mrac.{axis}.theta_" in k]
        mrac_e_key = available_signals.get(f"mrac.{axis}.e") if "mrac.{axis}.e" in pivoted else None

        axis_metrics: dict[str, Any] = {}

        # RMS of u_ad as fraction of total control
        if u_ad_key and u_ad_key in pivoted:
            _, u_ad_vals = pivoted[u_ad_key]
            u_ad_rms = _rms(u_ad_vals)
            pid_key = available_signals.get(f"pid_out_{axis}")
            if pid_key and pid_key in pivoted:
                _, pid_vals = pivoted[pid_key]
                pid_rms = _rms(pid_vals)
                total_rms = math.sqrt(u_ad_rms**2 + pid_rms**2) if (u_ad_rms**2 + pid_rms**2) > 0 else 1.0
                axis_metrics["u_ad_rms"] = round(u_ad_rms, 6)
                axis_metrics["u_ad_fraction_of_total"] = round(u_ad_rms / total_rms, 4)

        # Parameter convergence: time to stay within 5% of final
        for tidx, tkey in enumerate(theta_keys[:3]):  # check first 3 theta params
            if tkey in pivoted:
                _, t_vals = pivoted[tkey]
                if len(t_vals) >= 10:
                    final_val = t_vals[-1]
                    threshold = abs(final_val) * 0.05
                    converged_idx = None
                    for i in range(len(t_vals)):
                        remaining = t_vals[i:]
                        if all(abs(v - final_val) <= threshold for v in remaining[-20:]):
                            converged_idx = i
                            break
                    if converged_idx is not None:
                        axis_metrics[f"theta_{tidx}_convergence_index"] = converged_idx

        # Parameter drift
        for tidx, tkey in enumerate(theta_keys[:3]):
            if tkey in pivoted:
                _, t_vals = pivoted[tkey]
                if len(t_vals) >= 2:
                    drift = abs(t_vals[-1] - t_vals[0])
                    axis_metrics[f"theta_{tidx}_drift"] = round(drift, 6)

        if axis_metrics:
            metrics["mrac"][axis] = axis_metrics

    # vbat sag
    vbat_key = available_signals.get("vbat")
    if vbat_key and vbat_key in pivoted:
        _, v_vals = pivoted[vbat_key]
        if v_vals:
            metrics["vbat_sag"] = {
                "max_voltage": round(max(v_vals), 4),
                "min_voltage": round(min(v_vals), 4),
                "sag_v": round(max(v_vals) - min(v_vals), 4),
            }

    # -----------------------------------------------------------------------
    # RPM metrics — convert period_cyc → RPM, detect stale/edge-frozen.
    # Source: BSP/rpm.h:31  RPM_PULSES_PER_REV=2,  line 82  "RPM =
    #         60*SystemCoreClock/this"  with SystemCoreClock=168 MHz
    #         (BSP/rpm.h:60, BSP/rpm.c:185).
    # -----------------------------------------------------------------------
    _rpm_period_keys = [
        "slot2.rpm_dbg_period_cyc[0]",
        "slot2.rpm_dbg_period_cyc[1]",
        "slot2.rpm_dbg_period_cyc[2]",
        "slot2.rpm_dbg_period_cyc[3]",
    ]
    _rpm_edge_keys = [
        "slot2.rpm_dbg_edges[0]",
        "slot2.rpm_dbg_edges[1]",
        "slot2.rpm_dbg_edges[2]",
        "slot2.rpm_dbg_edges[3]",
    ]
    _has_any_rpm = False
    for ch_idx in range(4):
        period_key = _rpm_period_keys[ch_idx]
        edge_key = _rpm_edge_keys[ch_idx]
        period_vals = pivoted.get(period_key)
        edge_vals = pivoted.get(edge_key)
        if period_vals is None or edge_vals is None:
            continue
        p_ts, p_list = period_vals
        e_ts, e_list = edge_vals
        n = min(len(p_list), len(e_list))
        if n == 0:
            continue
        _has_any_rpm = True
        period_aligned = p_list[:n]
        edges_aligned = e_list[:n]

        # Convert to RPM using firmware formula
        #   RPM = 60 * SystemCoreClock / period_cyc
        #   period_cyc is a full-revolution period (ISR accumulates 2 edges).
        #   Source: BSP/rpm.h:82
        _SYSTEM_CORE_CLOCK = 168_000_000
        rpm_series: list[float] = []
        for pc, ec in zip(period_aligned, edges_aligned):
            if pc <= 0:
                rpm_series.append(float("nan"))
            else:
                rpm_series.append((60.0 * _SYSTEM_CORE_CLOCK) / pc)

        # Stale detection: edge counter not increasing for >=3 consecutive samples
        _STALE_WINDOW = 3
        stale_mask: list[bool] = []
        consecutive_stale = 0
        prev_edge: float | None = None
        for ec in edges_aligned:
            if prev_edge is not None and ec <= prev_edge:
                consecutive_stale += 1
            else:
                consecutive_stale = 0
            stale_mask.append(consecutive_stale >= _STALE_WINDOW)
            prev_edge = ec if ec > 0 else prev_edge
        # Edge counter never advanced: the period register holds a leftover
        # value from an earlier spin, so no sample measures the motor.
        if max(edges_aligned) <= edges_aligned[0]:
            stale_mask = [True] * n
        stale_count = sum(stale_mask)

        # Stats over fresh samples only; stale samples repeat an old period.
        valid_rpms = [r for r, s in zip(rpm_series, stale_mask)
                      if not s and not math.isnan(r)]
        motor_label = f"motor{ch_idx + 1}"
        if valid_rpms:
            metrics["rpm"][motor_label] = {
                "mean": round(statistics.mean(valid_rpms), 4),
                "std": round(statistics.stdev(valid_rpms), 4) if len(valid_rpms) >= 2 else 0.0,
                "max": round(max(valid_rpms), 4),
                "stale_fraction": round(stale_count / max(len(rpm_series), 1), 6),
            }
        else:
            metrics["rpm"][motor_label] = {
                "mean": 0.0,
                "std": 0.0,
                "max": 0.0,
                "stale_fraction": 1.0 if rpm_series else 0.0,
            }

    # Asymmetry index across motors
    if _has_any_rpm:
        motor_means = {
            i + 1: metrics["rpm"].get(f"motor{i+1}", {}).get("mean", 0.0)
            for i in range(4)
        }
        valid_means = [v for v in motor_means.values() if v > 0]
        if len(valid_means) >= 2:
            overall_mean = statistics.mean(valid_means)
            if overall_mean > 0:
                metrics["rpm"]["asymmetry_index"] = round(
                    (max(valid_means) - min(valid_means)) / overall_mean, 6
                )
        # Front-back / left-right: no motor-layout mapping found in codebase.
        # Source: searched BSP/, USER/, ground_station/ — none defines motor
        #         physical layout (FL/FR/BL/BR).  Skipping these fields.

    # vbat sag (already handled above if available)

    # Telemetry quality — compute per-slot over unique frame timestamps.
    # A single frame (one received_ns) may contain many keys; computing dt over
    # all raw rows would produce spurious 0-dt gaps.
    if telemetry:
        slot_ts: dict[int, set[int]] = {}
        total_rows = len(telemetry)
        for ts, slot, _key, _val in telemetry:
            slot_ts.setdefault(slot, set()).add(ts)
        all_dts: list[float] = []
        slot_rates: dict[int, float | None] = {}
        total_gaps = 0
        total_dt_count = 0
        for slot, timestamps in sorted(slot_ts.items()):
            sorted_ts = sorted(timestamps)
            if len(sorted_ts) >= 2:
                dts = [sorted_ts[i + 1] - sorted_ts[i] for i in range(len(sorted_ts) - 1)]
                med_dt = statistics.median(dts)
                total_dts = sum(dts)
                all_dts.append(med_dt)
                total_dt_count += len(dts)
                gap_thresh = med_dt * 5 if med_dt > 0 else 0
                gaps = sum(1 for d in dts if d > gap_thresh)
                total_gaps += gaps
                if med_dt > 0:
                    slot_rates[slot] = round(1e9 / med_dt, 2)
                else:
                    slot_rates[slot] = None
        if all_dts:
            overall_median_dt = statistics.median(all_dts)
        elif total_dt_count > 0:
            overall_median_dt = 0
        else:
            overall_median_dt = 0
        overall_est_rate: float | None = None
        if overall_median_dt > 0:
            overall_est_rate = round(1e9 / overall_median_dt, 2)
        metrics["telemetry_quality"] = {
            "total_samples": total_rows,
            "median_dt_ns": round(overall_median_dt, 2),
            "estimated_rate_hz": overall_est_rate,
            "gap_count": total_gaps,
            "gap_rate": round(total_gaps / max(total_dt_count, 1) * 100, 4),
            "slot_rates": {str(k): v for k, v in slot_rates.items()},
        }
    else:
        total_rows = sum(len(pivoted[k][0]) for k in pivoted)
        all_ts_list = []
        for k, (ts, _) in pivoted.items():
            all_ts_list.extend(ts)
        if all_ts_list:
            all_ts_list.sort()
            if len(all_ts_list) >= 2:
                dts = [all_ts_list[i + 1] - all_ts_list[i] for i in range(len(all_ts_list) - 1)]
                median_dt = statistics.median(dts) if dts else 0
                gap_threshold = median_dt * 5
                gaps = sum(1 for dt in dts if dt > gap_threshold)
                metrics["telemetry_quality"] = {
                    "total_samples": total_rows,
                    "median_dt_ns": round(median_dt, 2),
                    "estimated_rate_hz": round(1e9 / median_dt, 2) if median_dt > 0 else None,
                    "gap_count": gaps,
                    "gap_rate": round(gaps / len(dts) * 100, 4) if dts else 0.0,
                }

    return metrics


# ---------------------------------------------------------------------------
# Plot generation
# ---------------------------------------------------------------------------

def _get_default_cmap() -> list[str]:
    """Colour-blind-safe palette."""
    return ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
            "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]


def _setup_style() -> None:
    """Configure matplotlib for publication quality."""
    if not HAS_MPL:
        return
    plt.rcParams.update({
        "font.family": "serif",
        "font.serif": ["DejaVu Serif", "Times New Roman"],
        "axes.labelsize": 11,
        "axes.titlesize": 13,
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "figure.dpi": 300,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "legend.fontsize": 9,
        "lines.linewidth": 1.2,
        "axes.grid": False,
    })


def _make_fig(figsize: tuple[int, int] = (3.5, 2.5)) -> tuple[plt.Figure, plt.Axes]:
    fig, ax = plt.subplots(figsize=figsize)
    return fig, ax


def plot_attitude_tracking(
    pivoted: dict[str, tuple[list[int], list[float]]],
    signal_map: dict[str, list[str]],
    out_dir: Path,
    segmentation: dict[str, Any],
) -> list[str]:
    """Plot attitude tracking (measured vs setpoint) per axis with error subplot."""
    if not HAS_MPL:
        return []
    _setup_style()
    files = []
    axes_map = {
        "roll": "roll_deg",
        "pitch": "pitch_deg",
        "yaw": "yaw_deg",
    }
    sp_map = {
        "roll": "roll_sp",
        "pitch": "pitch_sp",
        "yaw": "yaw_sp",
    }
    colors = _get_default_cmap()

    fig, axes = plt.subplots(3, 1, figsize=(7, 8), sharex=True)
    fig.suptitle("Attitude Tracking", fontsize=14)

    for i, (axis, ax) in enumerate(zip(["roll", "pitch", "yaw"], axes)):
        me_key = signal_map.get(axis, [])[0]
        sp_key_list = signal_map.get(sp_map[axis], [])
        sp_key = sp_key_list[0] if sp_key_list else None

        if me_key not in pivoted:
            ax.text(0.5, 0.5, f"{axis}: signal missing", transform=ax.transAxes,
                    ha="center", va="center", fontsize=10)
            continue

        me_ts, me_vals = pivoted[me_key]
        if not me_vals:
            continue

        # Normalise timestamps
        if me_ts:
            t0 = me_ts[0]
            t_norm = [(t - t0) / 1e9 for t in me_ts]

            ax.plot(t_norm, me_vals, color=colors[i], label="measured", alpha=0.8)
            if sp_key and sp_key in pivoted:
                sp_ts, sp_vals = pivoted[sp_key]
                if sp_ts:
                    # Simple alignment: use first N samples matching length
                    min_len = min(len(t_norm), len(sp_vals))
                    if min_len > 0:
                        ax.plot(t_norm[:min_len], sp_vals[:min_len], color=colors[i+1],
                                label="setpoint", linestyle="--", alpha=0.8)

            # Error subplot
            if sp_key and sp_key in pivoted:
                sp_ts, sp_vals = pivoted[sp_key]
                min_len = min(len(t_norm), len(sp_vals))
                if min_len > 0:
                    errors = [me_vals[j] - sp_vals[j] for j in range(min_len)]
                    ax2 = ax.twinx()
                    ax2.plot(t_norm[:min_len], errors, color=colors[3], label="error", alpha=0.6)
                    ax2.set_ylabel(f"{axis} error (deg)")

            ax.set_ylabel(f"{axis} (deg)")
            ax.set_xlabel("Time (s)")
            ax.legend(loc="best", fontsize=7)

    plt.tight_layout()
    png_path = out_dir / "attitude_tracking.png"
    pdf_path = out_dir / "attitude_tracking.pdf"
    fig.savefig(png_path, format="png")
    fig.savefig(pdf_path, format="pdf")
    plt.close(fig)
    files.extend([str(png_path), str(pdf_path)])
    return files


def plot_rate_tracking(
    pivoted: dict[str, tuple[list[int], list[float]]],
    signal_map: dict[str, list[str]],
    out_dir: Path,
) -> list[str]:
    """Plot rate tracking (gyro vs rate setpoint) per axis."""
    if not HAS_MPL:
        return []
    _setup_style()
    files = []
    fig, axes = plt.subplots(3, 1, figsize=(7, 8), sharex=True)
    fig.suptitle("Rate Tracking", fontsize=14)

    rate_keys = ["gyro_x", "gyro_y", "gyro_z"]
    sp_keys = ["gyro_x_sp", "gyro_y_sp", "gyro_z_sp"]
    colors = _get_default_cmap()

    for i, (rkey, spkey, ax) in enumerate(zip(rate_keys, sp_keys, axes)):
        rk_list = signal_map.get(rkey, [])
        sk_list = signal_map.get(spkey, [])
        rk = rk_list[0] if rk_list else None
        sk = sk_list[0] if sk_list else None

        if rk and rk in pivoted:
            ts, vals = pivoted[rk]
            if ts:
                t0 = ts[0]
                t_norm = [(t - t0) / 1e9 for t in ts]
                ax.plot(t_norm, vals, color=colors[i], label="measured", alpha=0.8)
        if sk and sk in pivoted:
            ts, vals = pivoted[sk]
            if ts:
                t0 = ts[0]
                t_norm = [(t - t0) / 1e9 for t in ts]
                ax.plot(t_norm, vals, color=colors[i+1], label="setpoint", linestyle="--", alpha=0.8)

        ax.set_ylabel(f"rate_{['x','y','z'][i]} (deg/s)")
        ax.set_xlabel("Time (s)")
        ax.legend(loc="best", fontsize=7)

    plt.tight_layout()
    png_path = out_dir / "rate_tracking.png"
    pdf_path = out_dir / "rate_tracking.pdf"
    fig.savefig(png_path, format="png")
    fig.savefig(pdf_path, format="pdf")
    plt.close(fig)
    files.extend([str(png_path), str(pdf_path)])
    return files


def plot_control_effort(
    pivoted: dict[str, tuple[list[int], list[float]]],
    signal_map: dict[str, list[str]],
    out_dir: Path,
) -> list[str]:
    """Plot control effort (PID outputs and motor outputs)."""
    if not HAS_MPL:
        return []
    _setup_style()
    files = []
    fig, axes = plt.subplots(2, 1, figsize=(7, 6), sharex=True)
    fig.suptitle("Control Effort & Motor Outputs", fontsize=14)

    colors = _get_default_cmap()

    # PID outputs
    pid_outputs = []
    for suffix, idx in [("roll", 0), ("pitch", 1), ("yaw", 2)]:
        u_key = signal_map.get(f"pid_out_{suffix}", [])[0]
        if u_key and u_key in pivoted:
            pid_outputs.append((u_key, pivoted[u_key], colors[idx]))

    ax0 = axes[0]
    for i, (name, (ts, vals), c) in enumerate(pid_outputs):
        if ts:
            t0 = ts[0]
            t_norm = [(t - t0) / 1e9 for t in ts]
            ax0.plot(t_norm, vals, color=c, label=name, alpha=0.8)
    ax0.set_ylabel("PID output")
    ax0.legend(loc="best", fontsize=7)

    # Motors
    motor_outputs = []
    for i in range(1, 5):
        mk = signal_map.get(f"motor{i}", [])[0]
        if mk and mk in pivoted:
            motor_outputs.append((mk, pivoted[mk], colors[(i+2) % 10]))

    ax1 = axes[1]
    for i, (name, (ts, vals), c) in enumerate(motor_outputs):
        if ts:
            t0 = ts[0]
            t_norm = [(t - t0) / 1e9 for t in ts]
            ax1.plot(t_norm, vals, color=c, label=name, alpha=0.8)
    ax1.set_ylabel("Motor output")
    ax1.set_xlabel("Time (s)")
    ax1.legend(loc="best", fontsize=7)

    plt.tight_layout()
    png_path = out_dir / "control_effort.png"
    pdf_path = out_dir / "control_effort.pdf"
    fig.savefig(png_path, format="png")
    fig.savefig(pdf_path, format="pdf")
    plt.close(fig)
    files.extend([str(png_path), str(pdf_path)])
    return files


def plot_mrac_params(
    pivoted: dict[str, tuple[list[int], list[float]]],
    signal_map: dict[str, list[str]],
    out_dir: Path,
) -> list[str]:
    """Plot MRAC parameter evolution and u_ad."""
    if not HAS_MPL:
        return []
    _setup_style()
    files = []
    fig, axes = plt.subplots(2, 1, figsize=(7, 6), sharex=True)
    fig.suptitle("MRAC Parameter Evolution & u_ad", fontsize=14)

    colors = _get_default_cmap()
    found_any = False

    for axis_idx, axis in enumerate(("roll", "pitch", "yaw")):
        theta_keys = [k for k in pivoted.keys() if f"mrac.{axis}.theta_" in k]
        u_ad_key = signal_map.get(f"u_ad_{axis}", [])[0]

        ax0 = axes[0]
        for tidx, tk in enumerate(theta_keys[:5]):
            ts, vals = pivoted[tk]
            if ts:
                t0 = ts[0]
                t_norm = [(t - t0) / 1e9 for t in ts]
                ax0.plot(t_norm, vals, color=colors[(axis_idx*3+tidx)%10],
                        label=f"{axis}_theta_{tidx}", alpha=0.7)
                found_any = True

        ax1 = axes[1]
        if u_ad_key and u_ad_key in pivoted:
            ts, vals = pivoted[u_ad_key]
            if ts:
                t0 = ts[0]
                t_norm = [(t - t0) / 1e9 for t in ts]
                ax1.plot(t_norm, vals, color=colors[axis_idx], label=f"u_ad_{axis}", alpha=0.8)
                found_any = True

    if found_any:
        axes[0].set_ylabel("Theta parameters")
        axes[1].set_ylabel("u_ad")
        axes[1].set_xlabel("Time (s)")
        axes[0].legend(loc="best", fontsize=7)
        axes[1].legend(loc="best", fontsize=7)

        png_path = out_dir / "mrac_params.png"
        pdf_path = out_dir / "mrac_params.pdf"
        fig.savefig(png_path, format="png")
        fig.savefig(pdf_path, format="pdf")
        plt.close(fig)
        files.extend([str(png_path), str(pdf_path)])
    else:
        plt.close(fig)
    return files


def plot_error_distribution(
    pivoted: dict[str, tuple[list[int], list[float]]],
    signal_map: dict[str, list[str]],
    out_dir: Path,
) -> list[str]:
    """Plot error distribution histogram per attitude axis."""
    if not HAS_MPL:
        return []
    _setup_style()
    files = []

    fig, axes = plt.subplots(1, 3, figsize=(10.5, 3))
    fig.suptitle("Error Distribution", fontsize=14)

    colors = _get_default_cmap()
    for i, (axis, ax) in enumerate(zip(["roll", "pitch", "yaw"], axes)):
        sp_key = signal_map.get(f"{axis}_sp", [])[0]
        me_key = signal_map.get(axis, [])[0]
        if not me_key or me_key not in pivoted:
            continue
        if not sp_key or sp_key not in pivoted:
            ax.text(0.5, 0.5, f"{axis}: setpoint missing", transform=ax.transAxes,
                    ha="center", va="center")
            continue

        me_ts, me_vals = pivoted[me_key]
        sp_ts, sp_vals = pivoted[sp_key]
        min_len = min(len(me_vals), len(sp_vals))
        if min_len < 10:
            continue

        errors = [me_vals[j] - sp_vals[j] for j in range(min_len)]
        ax.hist(errors, bins=40, color=colors[i], alpha=0.7, edgecolor="white")
        ax.set_xlabel(f"{axis} error (deg)")
        ax.set_ylabel("Count")
        ax.set_title(f"{axis} error distribution")
        # Annotate stats
        if errors:
            ax.axvline(statistics.mean(errors), color="red", linestyle="--", linewidth=1)
            ax.axvline(statistics.mean(errors) + statistics.stdev(errors) if len(errors)>1 else 0,
                       color="orange", linestyle=":", linewidth=1)
            ax.axvline(statistics.mean(errors) - statistics.stdev(errors) if len(errors)>1 else 0,
                       color="orange", linestyle=":", linewidth=1)

    plt.tight_layout()
    png_path = out_dir / "error_distribution.png"
    pdf_path = out_dir / "error_distribution.pdf"
    fig.savefig(png_path, format="png")
    fig.savefig(pdf_path, format="pdf")
    plt.close(fig)
    files.extend([str(png_path), str(pdf_path)])
    return files


def plot_psd(
    pivoted: dict[str, tuple[list[int], list[float]]],
    signal_map: dict[str, list[str]],
    out_dir: Path,
) -> list[str]:
    """Plot PSD of attitude error (reuse spectrum.py if scipy available)."""
    if not HAS_MPL:
        return []
    _setup_style()
    files = []

    fig, axes = plt.subplots(3, 1, figsize=(7, 8), sharex=True)
    fig.suptitle("PSD of Attitude Error", fontsize=14)

    colors = _get_default_cmap()

    for i, axis in enumerate(["roll", "pitch", "yaw"]):
        sp_key = signal_map.get(f"{axis}_sp", [])[0]
        me_key = signal_map.get(axis, [])[0]
        ax = axes[i]

        if not me_key or me_key not in pivoted:
            ax.text(0.5, 0.5, f"{axis}: signal missing", transform=ax.transAxes,
                    ha="center", va="center")
            continue
        if not sp_key or sp_key not in pivoted:
            continue

        me_ts, me_vals = pivoted[me_key]
        sp_ts, sp_vals = pivoted[sp_key]
        min_len = min(len(me_vals), len(sp_vals))

        if min_len < 2:
            ax.text(0.5, 0.5, "insufficient data", transform=ax.transAxes,
                    ha="center", va="center")
            continue

        errors = [me_vals[j] - sp_vals[j] for j in range(min_len)]

        if HAS_SCIPY and HAS_NP:
            try:
                # Resample to uniform grid for PSD
                if me_ts:
                    t0 = me_ts[0]
                    t_norm = [(t - t0) / 1e9 for t in me_ts[:min_len]]
                    dt = statistics.median(
                        [t_norm[j+1] - t_norm[j] for j in range(min_len-1)]
                    ) if min_len > 1 else 1.0
                    if dt > 0 and min_len >= 16:
                        fs = 1.0 / dt
                        from scipy.fft import fft
                        f_fft = np.linspace(0, fs/2, min_len//2+1)
                        fft_vals = fft(errors)
                        psd = (2.0 / (min_len * fs)) * np.abs(fft_vals[:min_len//2+1])**2
                        ax.semilogy(f_fft, psd, color=colors[i], alpha=0.8)
                        ax.set_ylabel(f"{axis} PSD")
                    else:
                        ax.text(0.5, 0.5, "not enough data for PSD",
                                transform=ax.transAxes, ha="center", va="center", fontsize=8)
                else:
                    ax.text(0.5, 0.5, "no timestamps", transform=ax.transAxes,
                            ha="center", va="center", fontsize=8)
            except Exception:
                ax.text(0.5, 0.5, "PSD failed", transform=ax.transAxes,
                        ha="center", va="center", fontsize=8)
        else:
            ax.text(0.5, 0.5, "scipy not available for PSD",
                    transform=ax.transAxes, ha="center", va="center", fontsize=8)

    axes[2].set_xlabel("Frequency (Hz)")
    plt.tight_layout()
    png_path = out_dir / "psd_error.png"
    pdf_path = out_dir / "psd_error.pdf"
    fig.savefig(png_path, format="png")
    fig.savefig(pdf_path, format="pdf")
    plt.close(fig)
    files.extend([str(png_path), str(pdf_path)])
    return files


def plot_trajectory_3d(
    pivoted: dict[str, tuple[list[int], list[float]]],
    signal_map: dict[str, list[str]],
    out_dir: Path,
) -> list[str]:
    """Plot 3D trajectory (actual vs desired) and XY/XZ projections."""
    if not HAS_MPL:
        return []
    _setup_style()
    files = []

    pos_keys = {"x": "pos_x", "y": "pos_y", "z": "pos_z"}
    sp_keys = {"x": "pos_x_sp", "y": "pos_y_sp", "z": "pos_z_sp"}

    has_actual = any(signal_map.get(pos_keys[k], [])[0] and
                     signal_map.get(pos_keys[k], [])[0] in pivoted
                     for k in pos_keys)
    has_desired = any(signal_map.get(sp_keys[k], [])[0] and
                      signal_map.get(sp_keys[k], [])[0] in pivoted
                      for k in sp_keys)

    if not has_actual:
        return files

    fig = plt.figure(figsize=(7, 7))
    ax3d = fig.add_subplot(111, projection="3d")
    ax3d.set_title("3D Trajectory", fontsize=14)

    actual_data = []
    desired_data = []

    for k in pos_keys:
        ak = signal_map.get(pos_keys[k], [])[0]
        sk = signal_map.get(sp_keys[k], [])[0]
        if ak and ak in pivoted:
            _, vals = pivoted[ak]
            if vals:
                actual_data.append((k, vals))
        if sk and sk in pivoted:
            _, vals = pivoted[sk]
            if vals:
                desired_data.append((k, vals))

    if actual_data:
        # Use z as the primary axis for timeline
        z_key_data = [d for d in actual_data if d[0] == "z"]
        if z_key_data:
            z_vals = z_key_data[0][1]
            for k, vals in actual_data:
                min_len = min(len(z_vals), len(vals))
                if min_len > 0:
                    ax3d.plot(z_vals[:min_len], vals[:min_len],
                              [0]*min_len if k != "z" else [z_vals[j] for j in range(min_len)],
                              label=f"actual {k}", alpha=0.8)

    if desired_data:
        z_key_data = [d for d in desired_data if d[0] == "z"]
        if z_key_data:
            z_vals = z_key_data[0][1]
            for k, vals in desired_data:
                min_len = min(len(z_vals), len(vals))
                if min_len > 0:
                    ax3d.plot(z_vals[:min_len], vals[:min_len],
                              [0]*min_len if k != "z" else [z_vals[j] for j in range(min_len)],
                              label=f"desired {k}", alpha=0.5, linestyle="--")

    ax3d.legend(fontsize=7)
    png_path = out_dir / "trajectory_3d.png"
    pdf_path = out_dir / "trajectory_3d.pdf"
    fig.savefig(png_path, format="png")
    fig.savefig(pdf_path, format="pdf")
    plt.close(fig)
    files.extend([str(png_path), str(pdf_path)])
    return files


def plot_time_series_overview(
    pivoted: dict[str, tuple[list[int], list[float]]],
    signal_map: dict[str, list[str]],
    out_dir: Path,
) -> list[str]:
    """Plot time-series overview of all available signals."""
    if not HAS_MPL:
        return []
    _setup_style()
    files = []

    # Pick a representative set of signals
    pick_list = [
        ("roll", "roll"),
        ("pitch", "pitch"),
        ("yaw", "yaw"),
        ("gyro_x", "gyro_x"),
        ("gyro_y", "gyro_y"),
        ("gyro_z", "gyro_z"),
        ("vbat", "vbat"),
    ]

    available = []
    for role, lookup in pick_list:
        key = signal_map.get(lookup, [])[0]
        if key and key in pivoted:
            available.append((role, key))

    if not available:
        return files

    fig, axes = plt.subplots(len(available), 1, figsize=(7, 3 * len(available)), sharex=True)
    if len(available) == 1:
        axes = [axes]
    fig.suptitle("Time-Series Overview", fontsize=14)

    colors = _get_default_cmap()
    for i, (role, key) in enumerate(available):
        ts, vals = pivoted[key]
        ax = axes[i]
        if ts:
            t0 = ts[0]
            t_norm = [(t - t0) / 1e9 for t in ts]
            ax.plot(t_norm, vals, color=colors[i % 10], alpha=0.8)
        ax.set_ylabel(role)
        ax.label_outer()

    plt.tight_layout()
    png_path = out_dir / "timeseries_overview.png"
    pdf_path = out_dir / "timeseries_overview.pdf"
    fig.savefig(png_path, format="png")
    fig.savefig(pdf_path, format="pdf")
    plt.close(fig)
    files.extend([str(png_path), str(pdf_path)])
    return files


def plot_rpm(
    pivoted: dict[str, tuple[list[int], list[float]]],
    signal_map: dict[str, list[str]],
    out_dir: Path,
) -> list[str]:
    """Plot 4 motor RPM traces vs time (research-paper style)."""
    if not HAS_MPL:
        return []
    _setup_style()
    files = []

    _SYSTEM_CORE_CLOCK = 168_000_000

    period_keys = [
        "slot2.rpm_dbg_period_cyc[0]",
        "slot2.rpm_dbg_period_cyc[1]",
        "slot2.rpm_dbg_period_cyc[2]",
        "slot2.rpm_dbg_period_cyc[3]",
    ]

    rpm_series: dict[int, tuple[list[float], list[float]]] = {}
    for ch_idx, pk in enumerate(period_keys):
        pv = pivoted.get(pk)
        if pv is None:
            continue
        ts, p_list = pv
        n = len(p_list)
        if n == 0:
            continue
        t_norm = [(t - ts[0]) / 1e9 for t in ts]
        rpm_vals: list[float] = []
        for pc in p_list:
            if pc > 0:
                rpm_vals.append((60.0 * _SYSTEM_CORE_CLOCK) / pc)
            else:
                rpm_vals.append(float("nan"))
        rpm_series[ch_idx + 1] = (t_norm, rpm_vals)

    if not rpm_series:
        return files

    colors = _get_default_cmap()
    fig, ax = plt.subplots(figsize=(7, 4))
    fig.suptitle("Motor RPM", fontsize=14)

    for i, (ch, (t_norm, rpm_vals)) in enumerate(sorted(rpm_series.items())):
        valid_mask = [not math.isnan(v) for v in rpm_vals]
        t_valid = [t_norm[j] for j in range(len(t_norm)) if valid_mask[j]]
        v_valid = [rpm_vals[j] for j in range(len(rpm_vals)) if valid_mask[j]]
        ax.plot(t_valid, v_valid, color=colors[i], label=f"motor{ch}", alpha=0.8)

    ax.set_ylabel("RPM")
    ax.set_xlabel("Time (s)")
    ax.legend(loc="best", fontsize=9)

    plt.tight_layout()
    png_path = out_dir / "motor_rpm.png"
    pdf_path = out_dir / "motor_rpm.pdf"
    fig.savefig(png_path, format="png")
    fig.savefig(pdf_path, format="pdf")
    plt.close(fig)
    files.extend([str(png_path), str(pdf_path)])
    return files


# ---------------------------------------------------------------------------
# Compare mode
# ---------------------------------------------------------------------------

def plot_compare_metrics(
    sessions: list[tuple[str, dict, dict]],
    out_dir: Path,
) -> list[str]:
    """Grouped bar charts of metrics by condition for comparison runs.

    sessions: [(label, metrics_dict, segmentation), ...]
    """
    if not HAS_MPL:
        return []
    _setup_style()
    files = []

    # Extract comparable metrics
    rmse_vals = {}
    labels = []
    for label, metrics, _ in sessions:
        labels.append(label)
        rmse_vals[label] = {}
        for axis in ("roll", "pitch", "yaw"):
            if axis in metrics.get("attitude", {}):
                rmse_vals[label][axis] = metrics["attitude"][axis].get("rmse", 0)

    if not rmse_vals:
        return files

    fig, ax = plt.subplots(figsize=(7, 4))
    fig.suptitle("RMSE by Axis and Condition", fontsize=14)

    axes_list = ["roll", "pitch", "yaw"]
    x = np.arange(len(axes_list)) if HAS_NP else [0, 1, 2]
    width = 0.8 / max(len(rmse_vals), 1)

    for ci, (label, vals) in enumerate(rmse_vals.items()):
        offsets = x + ci * width
        heights = [vals.get(a, 0) for a in axes_list]
        ax.bar(offsets, heights, width, label=label, alpha=0.85)

    ax.set_xticks(x + width * (len(rmse_vals) - 1) / 2)
    ax.set_xticklabels(axes_list)
    ax.set_ylabel("RMSE (deg)")
    ax.set_xlabel("Axis")
    ax.legend(title="Condition")

    png_path = out_dir / "compare_rmse.png"
    fig.savefig(png_path, format="png")
    plt.close(fig)
    files.append(str(png_path))

    # Error CDF overlay
    if HAS_NP and HAS_SCIPY:
        fig2, ax2 = plt.subplots(figsize=(7, 4))
        fig2.suptitle("Error CDF by Condition", fontsize=14)
        for label, metrics, _ in sessions:
            errors = []
            for axis in ("roll", "pitch", "yaw"):
                sp_key = None
                me_key = None
                # We need the pivoted data for this; skip if not available
            if errors:
                sorted_e = np.sort(np.abs(errors))
                cdf = np.arange(1, len(sorted_e)+1) / len(sorted_e)
                ax2.plot(sorted_e, cdf, label=label, alpha=0.8)
        ax2.set_xlabel("Absolute Error")
        ax2.set_ylabel("CDF")
        ax2.legend()

        png_path2 = out_dir / "compare_error_cdf.png"
        fig2.savefig(png_path2, format="png")
        plt.close(fig2)
        files.append(str(png_path2))

    return files


# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------

def generate_summary(
    metrics: dict[str, Any],
    segmentation: dict[str, Any],
    manifest: dict[str, Any],
    notes: str,
    missing_signals: list[str],
) -> str:
    """Generate human-readable markdown summary."""
    lines = [
        "# Flight Test Report Summary",
        "",
        "## Conditions",
        "",
        f"- **Controller**: {manifest.get('controller', 'unknown')}",
        f"- **Payload**: {manifest.get('payload', 'unknown')}",
        f"- **Session label**: {manifest.get('label', manifest.get('reason', 'N/A'))}",
        f"- **Started**: {manifest.get('started_at', 'N/A')}",
        f"- **Stopped**: {manifest.get('stopped_at', 'N/A')}",
        "",
    ]

    if manifest.get("reason"):
        lines.append(f"- **Reason**: {manifest['reason']}")
    if manifest.get("requested_by"):
        lines.append(f"- **Requested by**: {manifest['requested_by']}")
    if notes:
        lines.append(f"- **Notes**: {notes}")
    lines.append("")

    lines.append("## Segmentation")
    lines.append("")
    if segmentation["used_whole_log"]:
        lines.append("- **Note**: Drone was never armed; analysis uses the full log.")
    else:
        lines.append("- Armed window detected.")
        if segmentation.get("airborne_start_ns") is not None:
            lines.append(f"- Airborne window: armed with throttle above idle threshold.")
    lines.append("")

    lines.append("## Key Results")
    lines.append("")
    lines.append("| Axis | RMSE (deg) | MAE (deg) | Max Error (deg) | Overshoot (%) | Steady-State Std |")
    lines.append("|------|-----------|-----------|-----------------|---------------|-----------------|")

    attitude = metrics.get("attitude", {})
    for axis in ("roll", "pitch", "yaw"):
        m = attitude.get(axis, {})
        lines.append(
            f"| {axis} | {m.get('rmse', 'N/A')} | {m.get('mae', 'N/A')} "
            f"| {m.get('max_abs_error', 'N/A')} | {m.get('overshoot_pct', 'N/A')} "
            f"| {m.get('steady_state_std', 'N/A')} |"
        )
    lines.append("")

    rate = metrics.get("rate", {})
    if rate:
        lines.append("### Rate Tracking")
        lines.append("")
        lines.append("| Axis | RMSE (deg/s) | MAE (deg/s) | Max Error (deg/s) |")
        lines.append("|------|-------------|-------------|------------------|")
        for axis_name in ("roll", "pitch", "yaw"):
            m = rate.get(axis_name, {})
            lines.append(
                f"| {axis_name} | {m.get('rmse', 'N/A')} | {m.get('mae', 'N/A')} "
                f"| {m.get('max_abs_error', 'N/A')} |"
            )
        lines.append("")

    ctrl = metrics.get("control_effort", {})
    if ctrl:
        lines.append("### Control Effort")
        lines.append("")
        lines.append(f"- PID RMS: {ctrl.get('pid_rms', 'N/A')}")
        lines.append(f"- PID Total Variation: {ctrl.get('pid_total_variation', 'N/A')}")
        lines.append("")

    mi = metrics.get("motor_imbalance", {})
    if mi:
        lines.append("### Motor Imbalance")
        lines.append("")
        lines.append(f"- Per-motor mean: {mi.get('per_motor_mean', 'N/A')}")
        lines.append(f"- Max offset: {mi.get('max_offset', 'N/A')}")
        lines.append(f"- Imbalance: {mi.get('imbalance_pct', 'N/A')}%")
        lines.append("")

    tb = metrics.get("vbat_sag", {})
    if tb:
        lines.append(f"- Max voltage: {tb.get('max_voltage', 'N/A')} V")
        lines.append(f"- Min voltage: {tb.get('min_voltage', 'N/A')} V")
        lines.append(f"- Sag: {tb.get('sag_v', 'N/A')} V")
        lines.append("")

    tq = metrics.get("telemetry_quality", {})
    if tq:
        lines.append("### Telemetry Quality")
        lines.append("")
        lines.append(f"- Estimated rate: {tq.get('estimated_rate_hz', 'N/A')} Hz")
        lines.append(f"- Median dt: {tq.get('median_dt_ns', 'N/A')} ns")
        lines.append(f"- Gaps: {tq.get('gap_count', 'N/A')} ({tq.get('gap_rate', 'N/A')}%)")
        lines.append("")

    rpm = metrics.get("rpm", {})
    if rpm:
        lines.append("### Motor RPM")
        lines.append("")
        has_data = False
        all_stale = True
        for motor_key in ("motor1", "motor2", "motor3", "motor4"):
            m = rpm.get(motor_key)
            if m:
                all_stale = all_stale and m.get("mean", 0) == 0
                if m.get("mean", 0) > 0:
                    has_data = True
                stale_val = m.get("stale_fraction", 0.0)
                if m.get("mean", 0) > 0:
                    lines.append(
                        f"- **{motor_key}**: mean={m['mean']} RPM, "
                        f"std={m['std']} RPM, max={m['max']} RPM, "
                        f"stale={stale_val:.4f}"
                    )
                else:
                    lines.append(
                        f"- **{motor_key}**: mean=0 RPM, "
                        f"stale_fraction={stale_val:.4f}"
                    )
        if all_stale and has_data is False:
            lines.append("- Motors not spinning (all samples stale)")
            lines.append("")
        else:
            if "asymmetry_index" in rpm:
                lines.append(f"- Asymmetry index (`asymmetry_index`): {rpm['asymmetry_index']}")
            lines.append("")

    mrac = metrics.get("mrac", {})
    if mrac:
        lines.append("### MRAC Metrics")
        lines.append("")
        for axis in ("roll", "pitch", "yaw"):
            am = mrac.get(axis, {})
            if am:
                lines.append(f"- **{axis}**")
                for k, v in am.items():
                    lines.append(f"  - {k}: {v}")
        lines.append("")

    if missing_signals:
        lines.append("## Missing Signals")
        lines.append("")
        lines.append("The following signals were not found in the session and some metrics are incomplete:")
        lines.append("")
        for s in missing_signals:
            lines.append(f"- {s}")
        lines.append("")

    lines.append("## Caveats")
    lines.append("")
    lines.append("- `t_s` is host arrival time (Wi-Fi latency not removed).")
    lines.append("- Setpoint values come from recorded telemetry, not the flight plan.")
    lines.append("- Step detection uses a simple heuristic; actual setpoint changes may be gradual.")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Git commit info
# ---------------------------------------------------------------------------

def get_git_commit() -> str | None:
    """Get current git commit hash."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()[:12]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# ELF hash
# ---------------------------------------------------------------------------

def get_elf_hash(session_dir: Path) -> str | None:
    """Get firmware ELF hash from manifest or direct read."""
    manifest = read_manifest(session_dir)
    ctx = manifest.get("context", {})
    elf_info = ctx.get("firmware_elf", {})
    elf_path_str = elf_info.get("path", "")
    if elf_path_str:
        # Try to resolve the ELF file
        base = Path(session_dir).resolve().parents[2]  # logs/sessions/<id> -> project root
        elf_path = base / elf_path_str.replace("\\", "/")
        if elf_path.exists():
            h = hashlib.sha256()
            with open(elf_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()[:16]
    return elf_info.get("path", None)


# ---------------------------------------------------------------------------
# Main report generation
# ---------------------------------------------------------------------------

def generate_report(
    session_dir: Path,
    out_dir: Path | None = None,
    notes: str = "",
    controller: str = "unknown",
    payload: str = "unknown",
) -> dict[str, Any]:
    """Generate a complete flight-test report.

    Args:
        session_dir: Path to the session directory containing telemetry.csv.
        out_dir: Output directory (default: <session_dir>/report).
        notes: Operator notes.
        controller: Controller type (pid/mrac).
        payload: Payload condition (symmetric/asymmetric).

    Returns:
        dict with keys: output_dir, files, metrics, summary_path.
    """
    session_dir = Path(session_dir)
    if not session_dir.exists():
        raise FileNotFoundError(f"Session directory not found: {session_dir}")

    if out_dir is None:
        out_dir = session_dir / "report"
    else:
        out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "plots").mkdir(exist_ok=True)

    # Load data
    telemetry = read_telemetry_csv(session_dir)
    manifest = read_manifest(session_dir)
    signal_map = _load_signal_map(session_dir)
    pivoted = pivot(telemetry)

    # Segmentation
    segmentation = segment_telemetry(telemetry, pivoted)

    # Update manifest with flight-test metadata
    manifest["controller"] = controller
    manifest["payload"] = payload
    manifest["notes"] = notes
    manifest["signal_map_used"] = {k: v for k, v in signal_map.items() if k in pivoted}

    # Metrics
    metrics = compute_metrics(pivoted, segmentation, signal_map, telemetry)

    # Collect git info
    git_commit = get_git_commit()
    elf_hash = get_elf_hash(session_dir)

    # Write metadata.json
    metadata = {
        "controller": controller,
        "payload": payload,
        "notes": notes,
        "session_id": manifest.get("label", ""),
        "started_at": manifest.get("started_at"),
        "started_at_epoch": manifest.get("started_at_epoch"),
        "stopped_at": manifest.get("stopped_at"),
        "stopped_at_epoch": manifest.get("stopped_at_epoch"),
        "firmware_elf_hash": elf_hash,
        "preset": manifest.get("preset", ""),
        "signal_map_used": manifest["signal_map_used"],
        "git_commit": git_commit,
        "segmentation": {
            "armed_start_ns": segmentation["armed_start_ns"],
            "armed_end_ns": segmentation["armed_end_ns"],
            "airborne_start_ns": segmentation["airborne_start_ns"],
            "airborne_end_ns": segmentation["airborne_end_ns"],
            "used_whole_log": segmentation["used_whole_log"],
            "was_armed": segmentation["was_armed"],
        },
    }
    with open(out_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, default=str)

    # Write metrics.json
    with open(out_dir / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, default=str)

    # Write metrics.csv
    csv_path = out_dir / "metrics.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["category", "axis", "metric", "value"])
        for cat, cat_data in metrics.items():
            if cat in ("missing_signals",):
                continue
            if isinstance(cat_data, dict):
                for axis, axis_data in cat_data.items():
                    if isinstance(axis_data, dict):
                        for metric, value in axis_data.items():
                            writer.writerow([cat, axis, metric, value])
                    else:
                        writer.writerow([cat, axis, "_value", axis_data])
            elif isinstance(cat_data, list):
                for item in cat_data:
                    writer.writerow([cat, "", "_missing", item])
        # Also write top-level keys
        for key, val in metrics.items():
            if isinstance(val, (int, float, str)):
                writer.writerow(["general", "", key, val])

    # Generate plots
    plot_files = []
    plot_funcs = [
        plot_attitude_tracking,
        plot_rate_tracking,
        plot_control_effort,
        plot_mrac_params,
        plot_error_distribution,
        plot_psd,
        plot_trajectory_3d,
        plot_time_series_overview,
        plot_rpm,
    ]
    plot_args = [
        (pivoted, signal_map, out_dir / "plots", segmentation),
        (pivoted, signal_map, out_dir / "plots",),
        (pivoted, signal_map, out_dir / "plots",),
        (pivoted, signal_map, out_dir / "plots",),
        (pivoted, signal_map, out_dir / "plots",),
        (pivoted, signal_map, out_dir / "plots",),
        (pivoted, signal_map, out_dir / "plots",),
        (pivoted, signal_map, out_dir / "plots",),
        (pivoted, signal_map, out_dir / "plots",),
    ]
    for func, args in zip(plot_funcs, plot_args):
        try:
            files = func(*args)
            plot_files.extend(files)
        except Exception as exc:
            # Log but don't fail the whole report
            plot_files.append(f"Plot failed: {func.__name__}: {exc}")

    # Summary
    summary = generate_summary(metrics, segmentation, manifest, notes, metrics["missing_signals"])
    summary_path = out_dir / "summary.md"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary)

    return {
        "output_dir": str(out_dir),
        "files": plot_files + [str(out_dir / "metadata.json"), str(out_dir / "metrics.json"),
                                str(out_dir / "metrics.csv"), str(summary_path)],
        "metrics": metrics,
        "summary_path": str(summary_path),
        "plot_files": plot_files,
    }


# ---------------------------------------------------------------------------
# Compare mode
# ---------------------------------------------------------------------------

def generate_compare_report(
    session_dirs: list[str],
    out_dir: Path | None = None,
    controller_labels: list[str] | None = None,
    payload_labels: list[str] | None = None,
) -> dict[str, Any]:
    """Generate a comparison report across multiple sessions.

    Args:
        session_dirs: List of session directories.
        out_dir: Output directory (default: logs/flight_tests/compare_<date>).
        controller_labels: Labels for each session (default: derived from dir name).
        payload_labels: Payload labels for each session.
    """
    import uuid
    if out_dir is None:
        out_dir = Path("logs/flight_tests/compare") / f"compare_{uuid.uuid4().hex[:8]}"
    else:
        out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "plots").mkdir(exist_ok=True)

    sessions = []
    for i, sd in enumerate(session_dirs):
        label = controller_labels[i] if controller_labels else Path(sd).name
        sd_path = Path(sd)
        if not sd_path.exists():
            continue
        try:
            session_out = generate_report(
                session_dir=sd_path,
                out_dir=out_dir / f"session_{i}",
                notes="",
                controller=label,
                payload=payload_labels[i] if payload_labels else "unknown",
            )
            manifest = read_manifest(sd_path)
            signal_map = _load_signal_map(sd_path)
            telemetry = read_telemetry_csv(sd_path)
            pivoted = pivot(telemetry)
            segmentation = segment_telemetry(telemetry, pivoted)
            metrics = compute_metrics(pivoted, segmentation, signal_map, telemetry)
            sessions.append((label, metrics, segmentation))
        except Exception as exc:
            sessions.append((label, {"error": str(exc)}, {}))

    # Comparison plots
    compare_files = plot_compare_metrics(sessions, out_dir / "plots")

    # Summary for compare
    lines = [
        "# Flight Test Comparison Report",
        "",
        f"Sessions compared: {len(sessions)}",
        "",
    ]
    for label, metrics, seg in sessions:
        lines.append(f"## {label}")
        attitude = metrics.get("attitude", {})
        for axis in ("roll", "pitch", "yaw"):
            m = attitude.get(axis, {})
            rmse = m.get("rmse", "N/A")
            lines.append(f"- {axis}: RMSE = {rmse}")
        lines.append("")

    summary_path = out_dir / "summary.md"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return {
        "output_dir": str(out_dir),
        "files": compare_files + [str(summary_path)],
        "sessions": sessions,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    """CLI entry point for flight report generation."""
    if argv is None:
        argv = sys.argv[1:]

    if "--compare" in argv:
        # Compare mode
        idx = argv.index("--compare")
        compare_dirs = argv[idx+1:]
        if not compare_dirs:
            print("Error: --compare requires directory arguments", file=sys.stderr)
            sys.exit(1)
        controller_labels = None
        payload_labels = None
        out_dir = None
        # Check for optional --out
        if "--out" in argv:
            out_idx = argv.index("--out")
            if out_idx + 1 < len(argv):
                out_dir = argv[out_idx + 1]
        result = generate_compare_report(compare_dirs, out_dir=out_dir)
        print(f"Compare report generated: {result['output_dir']}")
        print(f"Files: {result['files']}")
    else:
        # Single session mode
        if not argv or argv[0].startswith("-"):
            print("Usage: flight_report <session_dir> [--out DIR] [--controller CTRL] [--payload PAYLOAD] [--notes TEXT]",
                  file=sys.stderr)
            sys.exit(1)

        session_dir = argv[0]
        notes = ""
        controller = "unknown"
        payload = "unknown"
        out_dir = None

        i = 1
        while i < len(argv):
            if argv[i] == "--out" and i + 1 < len(argv):
                out_dir = argv[i + 1]
                i += 2
            elif argv[i] == "--controller" and i + 1 < len(argv):
                controller = argv[i + 1]
                i += 2
            elif argv[i] == "--payload" and i + 1 < len(argv):
                payload = argv[i + 1]
                i += 2
            elif argv[i] == "--notes" and i + 1 < len(argv):
                notes = argv[i + 1]
                i += 2
            else:
                i += 1

        result = generate_report(session_dir, out_dir=out_dir, notes=notes,
                                 controller=controller, payload=payload)
        print(f"Report generated: {result['output_dir']}")
        print(f"Summary: {result['summary_path']}")
        print(f"Plots: {len(result['plot_files'])} files")


if __name__ == "__main__":
    main()
