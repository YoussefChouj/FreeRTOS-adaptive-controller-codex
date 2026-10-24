"""A/B control-metrics comparison across MRAC layer arms:

    python -m ground_station.analysis.layer_ab <day> --arms <id,id,...> --ref <id>

Computes per flight, per axis, on the same segments:
  - tracking error: rms and p95 of position and velocity error (hold, figure-8)
  - step response: rise time 10-90 %, overshoot %, settling time (5 % band) (step segments)
  - control effort: rms of the inner setpoint and of the motor command; % of samples saturated (all)
  - stability margins: multisine loop gain L(jw) = -Uc/U at each excited frequency (Uc = controller output,
    U = Uc + injected signal); gain and phase margin by interpolation ONLY inside the excited band, else
    "crossover outside the band" (excitation segments)
  - oscillation: peak-to-peak and dominant frequency in the hold (hold)

Reports per arm: each metric, its % change against --ref, and the same-arm spread (difference between repeats of one
arm). A change smaller than the same-arm spread prints "within noise".
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np

from ground_station.analysis import log_corpus as lc

DT_DEFAULT = 0.005
MOTOR_SAT_LO, MOTOR_SAT_HI = 2005.0, 3995.0  # PWM 0..1000 saturation thresholds
INNER_SAT_DEG = 25.0  # attitude inner setpoint saturation threshold (deg)


def _rms(v: np.ndarray) -> float:
    v = np.asarray(v, dtype=float)
    v = v[np.isfinite(v)]
    return float(math.sqrt(np.mean(v * v))) if len(v) else float("nan")


def _p95(v: np.ndarray) -> float:
    v = np.asarray(v, dtype=float)
    v = np.abs(v[np.isfinite(v)])
    return float(np.percentile(v, 95)) if len(v) else float("nan")


def step_metrics(t: np.ndarray, r: np.ndarray, y: np.ndarray) -> dict[str, float]:
    """Rise time 10-90 %, overshoot %, and 5 %-band settling time on a step segment."""
    t = np.asarray(t, dtype=float)
    r = np.asarray(r, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(t) < 10:
        return {"rise_time_s": float("nan"), "overshoot_pct": float("nan"), "settling_time_s": float("nan")}

    # Detect largest step edge in r
    dr = np.diff(r)
    k0 = int(np.argmax(np.abs(dr)))
    y0 = float(np.mean(y[: max(1, k0 + 1)]))
    r_final = float(r[min(len(r) - 1, k0 + max(2, (len(r) - k0) // 2))])
    step_amp = r_final - y0
    if abs(step_amp) < 1e-6:
        step_amp = float(r[-1] - r[0]) or 1.0
        y0 = float(y[0])
    t0 = float(t[k0])
    ts = t[k0:] - t0
    yn = (y[k0:] - y0) / step_amp  # normalized 0 -> 1 step response

    # Rise time 10% -> 90%
    i10 = np.flatnonzero(yn >= 0.10)
    i90 = np.flatnonzero(yn >= 0.90)
    if len(i10) and len(i90) and i90[0] >= i10[0]:
        t10 = float(np.interp(0.10, [yn[max(0, i10[0] - 1)], yn[i10[0]]], [ts[max(0, i10[0] - 1)], ts[i10[0]]]))
        t90 = float(np.interp(0.90, [yn[max(0, i90[0] - 1)], yn[i90[0]]], [ts[max(0, i90[0] - 1)], ts[i90[0]]]))
        rise_s = max(0.0, t90 - t10)
    else:
        rise_s = float("nan")

    overshoot_pct = max(0.0, (float(np.max(yn)) - 1.0) * 100.0)

    # Settling time (5 % band: |yn - 1| <= 0.05 for the rest of the step dwell)
    outside = np.flatnonzero(np.abs(yn - 1.0) > 0.05)
    if len(outside) == 0:
        settle_s = 0.0
    elif outside[-1] < len(ts) - 1:
        settle_s = float(ts[outside[-1] + 1])
    else:
        settle_s = float(ts[-1])

    return {
        "rise_time_s": rise_s,
        "overshoot_pct": overshoot_pct,
        "settling_time_s": settle_s,
    }


def hold_oscillation(t: np.ndarray, y: np.ndarray) -> dict[str, float]:
    """Peak-to-peak amplitude and dominant frequency (Hz) in a hold segment."""
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(y)
    if ok.sum() < 8:
        return {"p2p": float("nan"), "dom_freq_hz": float("nan")}
    t, y = t[ok], y[ok]
    yc = y - np.mean(y)
    p2p = float(np.max(yc) - np.min(yc))
    dt = float(np.median(np.diff(t))) if len(t) > 1 else DT_DEFAULT
    if dt <= 0:
        return {"p2p": p2p, "dom_freq_hz": float("nan")}
    spec = np.abs(np.fft.rfft(yc * np.hanning(len(yc))))
    freqs = np.fft.rfftfreq(len(yc), d=dt)
    if len(spec) <= 1:
        return {"p2p": p2p, "dom_freq_hz": 0.0}
    spec[0] = 0.0
    dom_hz = float(freqs[int(np.argmax(spec))])
    return {"p2p": p2p, "dom_freq_hz": dom_hz}


def stability_margins_multisine(
    t: np.ndarray,
    uc: np.ndarray,
    u: np.ndarray,
    excited_freqs_hz: list[float] | np.ndarray | None = None,
    band_hz: tuple[float, float] = (0.5, 15.0),
) -> dict[str, Any]:
    """Loop gain L(jw) = -Uc(jw)/U(jw) at each excited frequency; gain and phase margin by interpolation ONLY inside
    the excited band, else "crossover outside the band".
    """
    t = np.asarray(t, dtype=float)
    uc = np.asarray(uc, dtype=float)
    u = np.asarray(u, dtype=float)
    n = len(t)
    if n < 16:
        return {
            "gain_margin": float("nan"),
            "gain_margin_db": float("nan"),
            "phase_margin_deg": float("nan"),
            "gm_status": "crossover outside the band",
            "pm_status": "crossover outside the band",
        }
    dt = float(np.median(np.diff(t)))
    f0, f1 = float(band_hz[0]), float(band_hz[1])

    if excited_freqs_hz is None:
        freqs_all = np.fft.rfftfreq(n, d=dt)
        U_fft = np.fft.rfft(u - np.mean(u))
        Uc_fft = np.fft.rfft(uc - np.mean(uc))
        in_band = (freqs_all >= f0 - 1e-9) & (freqs_all <= f1 + 1e-9)
        if not in_band.any():
            return {
                "gain_margin": float("nan"),
                "gain_margin_db": float("nan"),
                "phase_margin_deg": float("nan"),
                "gm_status": "crossover outside the band",
                "pm_status": "crossover outside the band",
            }
        idx_band = np.flatnonzero(in_band)
        mag_u = np.abs(U_fft[idx_band])
        thr = 0.15 * float(np.max(mag_u)) if len(mag_u) else 0.0
        keep = idx_band[mag_u >= max(thr, 1e-9)]
        freqs = freqs_all[keep]
        L_vals = -Uc_fft[keep] / U_fft[keep]
    else:
        freqs_arr = np.asarray(sorted(excited_freqs_hz), dtype=float)
        freqs = freqs_arr[(freqs_arr >= f0 - 1e-9) & (freqs_arr <= f1 + 1e-9)]
        tc = t - t[0]
        uc_c = uc - np.mean(uc)
        u_c = u - np.mean(u)
        L_list = []
        kept_f = []
        for fk in freqs:
            basis = np.exp(-1j * 2.0 * np.pi * fk * tc)
            Uc_k = np.dot(uc_c, basis) / n
            U_k = np.dot(u_c, basis) / n
            if abs(U_k) > 1e-9:
                kept_f.append(float(fk))
                L_list.append(-Uc_k / U_k)
        freqs = np.asarray(kept_f, dtype=float)
        L_vals = np.asarray(L_list, dtype=complex)

    if len(freqs) < 2:
        return {
            "gain_margin": float("nan"),
            "gain_margin_db": float("nan"),
            "phase_margin_deg": float("nan"),
            "gm_status": "crossover outside the band",
            "pm_status": "crossover outside the band",
        }

    mag = np.abs(L_vals)
    # Unwrapped phase in degrees, anchored near negative angles for standard Bode loop gain
    phase_rad = np.unwrap(np.angle(L_vals))
    if phase_rad[0] > 0:
        phase_rad -= 2.0 * np.pi
    phase_deg = np.degrees(phase_rad)

    # Phase margin: |L(jw)| = 1 inside excited band
    pm_deg = float("nan")
    pm_freq = float("nan")
    pm_status = "crossover outside the band"
    log_mag = np.log(np.maximum(mag, 1e-12))
    for i in range(len(freqs) - 1):
        if (log_mag[i] >= 0.0 and log_mag[i + 1] <= 0.0) or (log_mag[i] <= 0.0 and log_mag[i + 1] >= 0.0):
            denom = log_mag[i + 1] - log_mag[i]
            alpha = -log_mag[i] / denom if abs(denom) > 1e-12 else 0.5
            pm_freq = float(freqs[i] + alpha * (freqs[i + 1] - freqs[i]))
            ph_at_gc = float(phase_deg[i] + alpha * (phase_deg[i + 1] - phase_deg[i]))
            pm_deg = 180.0 + ph_at_gc
            pm_status = "ok"
            break

    # Gain margin: phase(L(jw)) = -180 deg inside excited band
    gm = float("nan")
    gm_db = float("nan")
    gm_freq = float("nan")
    gm_status = "crossover outside the band"
    target_ph = -180.0
    for i in range(len(freqs) - 1):
        d0, d1 = phase_deg[i] - target_ph, phase_deg[i + 1] - target_ph
        if (d0 >= 0.0 and d1 <= 0.0) or (d0 <= 0.0 and d1 >= 0.0):
            denom = d1 - d0
            alpha = -d0 / denom if abs(denom) > 1e-12 else 0.5
            gm_freq = float(freqs[i] + alpha * (freqs[i + 1] - freqs[i]))
            log_m = float(log_mag[i] + alpha * (log_mag[i + 1] - log_mag[i]))
            mag_pc = math.exp(log_m)
            gm = 1.0 / mag_pc if mag_pc > 1e-12 else float("inf")
            gm_db = -20.0 * math.log10(max(mag_pc, 1e-12))
            gm_status = "ok"
            break

    return {
        "gain_margin": gm,
        "gain_margin_db": gm_db,
        "gm_freq_hz": gm_freq,
        "gm_status": gm_status,
        "phase_margin_deg": pm_deg,
        "pm_freq_hz": pm_freq,
        "pm_status": pm_status,
    }


def compute_session_metrics(series: lc.Series, dt: float = DT_DEFAULT) -> dict[str, float | str]:
    """Compute the full section 2b control metrics dictionary on one loaded session Series."""
    all_keys = sorted(series.keys())
    if not all_keys:
        return {}
    spans = [series[k][0] for k in all_keys if len(series[k][0]) >= 2]
    if not spans:
        return {}
    t0, t1 = max(float(s[0]) for s in spans), min(float(s[-1]) for s in spans)
    if t1 <= t0:
        t0, t1 = min(float(s[0]) for s in spans), max(float(s[-1]) for s in spans)
    t = np.arange(t0, t1, dt)
    if len(t) < 10:
        return {}

    air = lc.airborne(series, t)
    mask_all = air if air.any() else np.ones(len(t), dtype=bool)

    # Segment masks: figure-8 (traj_status == 1) vs hold (airborne and not figure-8 / step)
    traj_st = np.rint(lc.hold(series, "traj_status", t, 0.0)).astype(int) if "traj_status" in series else np.zeros(len(t), int)
    f8_mask = mask_all & (traj_st == 1)
    hold_mask = mask_all & (traj_st == 0)
    if not hold_mask.any():
        hold_mask = mask_all
    if not f8_mask.any():
        f8_mask = mask_all

    out: dict[str, float | str] = {}

    # 1. Tracking error (rms and p95 of position and velocity error on hold and figure-8)
    for ax, pos_des, pos_fb, vel_des, vel_fb in (
        ("x", "Ctrler.locxPID.Des", "Ctrler.locxPID.FB", "Ctrler.locxsPID.Des", "Ctrler.locxsPID.FB"),
        ("y", "Ctrler.locyPID.Des", "Ctrler.locyPID.FB", "Ctrler.locysPID.Des", "Ctrler.locysPID.FB"),
        ("z", "Ctrler.Z_posPID.Des", "Ctrler.Z_posPID.FB", "Ctrler.Z_ratePID.Des", "Ctrler.Z_ratePID.FB"),
    ):
        if pos_des in series and pos_fb in series:
            ep = lc.hold(series, pos_des, t) - lc.hold(series, pos_fb, t)
            out[f"pos_err_rms_hold_{ax}"] = _rms(ep[hold_mask])
            out[f"pos_err_p95_hold_{ax}"] = _p95(ep[hold_mask])
            out[f"pos_err_rms_f8_{ax}"] = _rms(ep[f8_mask])
            out[f"pos_err_p95_f8_{ax}"] = _p95(ep[f8_mask])
            osc = hold_oscillation(t[hold_mask], lc.hold(series, pos_fb, t)[hold_mask])
            out[f"hold_osc_p2p_{ax}"] = osc["p2p"]
            out[f"hold_osc_freq_hz_{ax}"] = osc["dom_freq_hz"]
        if vel_des in series and vel_fb in series:
            ev = lc.hold(series, vel_des, t) - lc.hold(series, vel_fb, t)
            out[f"vel_err_rms_hold_{ax}"] = _rms(ev[hold_mask])
            out[f"vel_err_p95_hold_{ax}"] = _p95(ev[hold_mask])
            out[f"vel_err_rms_f8_{ax}"] = _rms(ev[f8_mask])
            out[f"vel_err_p95_f8_{ax}"] = _p95(ev[f8_mask])

    # 2. Step response (z or yaw setpoint steps when present)
    for ax, des_k, fb_k in (
        ("z", "Ctrler.Z_posPID.Des", "Ctrler.Z_posPID.FB"),
        ("yaw", "Ctrler.yawPID.Des", " imu_data.yaw"),
        ("yaw", "Ctrler.gyrozPID.Des", "Ctrler.gyrozPID.FB"),
    ):
        des_k, fb_k = des_k.strip(), fb_k.strip()
        if des_k in series and fb_k in series and f"step_rise_s_{ax}" not in out:
            r_sig = lc.hold(series, des_k, t)[mask_all]
            y_sig = lc.hold(series, fb_k, t)[mask_all]
            if len(r_sig) >= 10 and (np.max(r_sig) - np.min(r_sig)) > 1e-3:
                sm = step_metrics(t[mask_all], r_sig, y_sig)
                out[f"step_rise_s_{ax}"] = sm["rise_time_s"]
                out[f"step_overshoot_pct_{ax}"] = sm["overshoot_pct"]
                out[f"step_settling_s_{ax}"] = sm["settling_time_s"]

    # 3. Control effort (rms of inner setpoint and motor command; % saturated over all airborne samples)
    inner_keys = [k for k in ("Ctrler.pitchPID.Des", "Ctrler.rollPID.Des", "Ctrler.gyroxPID.Des", "Ctrler.gyroyPID.Des") if k in series]
    if inner_keys:
        inner_vals = np.concatenate([lc.hold(series, k, t)[mask_all] for k in inner_keys])
        out["effort_inner_setpoint_rms"] = _rms(inner_vals)
        out["effort_inner_sat_pct"] = float(np.mean(np.abs(inner_vals) >= INNER_SAT_DEG) * 100.0)
    motor_keys = [f"mymotor.motor{i}" for i in (1, 2, 3, 4) if f"mymotor.motor{i}" in series]
    if motor_keys:
        m_vals = np.concatenate([lc.hold(series, k, t)[mask_all] for k in motor_keys])
        out["effort_motor_cmd_rms"] = _rms(m_vals)
        out["effort_motor_sat_pct"] = float(np.mean((m_vals <= MOTOR_SAT_LO) | (m_vals >= MOTOR_SAT_HI)) * 100.0)

    # 4. Stability margins on multisine excitation segments (explicit Uc/U or rate loop u_gyrox / gyroxPID)
    if "excite.Uc" in series and "excite.U" in series:
        uc_s = lc.hold(series, "excite.Uc", t)[mask_all]
        u_s = lc.hold(series, "excite.U", t)[mask_all]
        sm = stability_margins_multisine(t[mask_all], uc_s, u_s)
        out["gain_margin"] = sm["gain_margin"]
        out["gain_margin_db"] = sm["gain_margin_db"]
        out["phase_margin_deg"] = sm["phase_margin_deg"]
        out["gm_status"] = sm["gm_status"]
        out["pm_status"] = sm["pm_status"]
    elif "Ctrler.gyroxPID.Des" in series and "Ctrler.gyroxPID.FB" in series:
        # Rate-setpoint excitation: r = r0 + d, y = gyroxPID.FB -> Uc = -y, U = r - y so -Uc/U = Y/(R-Y) = L(jw)
        r_s = lc.hold(series, "Ctrler.gyroxPID.Des", t)[mask_all]
        y_s = lc.hold(series, "Ctrler.gyroxPID.FB", t)[mask_all]
        uc_s = -y_s
        u_s = r_s - y_s
        sm = stability_margins_multisine(t[mask_all], uc_s, u_s)
        out["gain_margin"] = sm["gain_margin"]
        out["gain_margin_db"] = sm["gain_margin_db"]
        out["phase_margin_deg"] = sm["phase_margin_deg"]
        out["gm_status"] = sm["gm_status"]
        out["pm_status"] = sm["pm_status"]

    return out


def _base_arm(arm_id: str) -> str:
    """Group repeat ids like xy-layer-inner-1 and xy-layer-inner-2 under xy-layer-inner."""
    return re.sub(r"-[12]$", "", arm_id)


def _find_arm_sessions(day: str, arm: str, root: Path) -> list[Path]:
    """Find session directories matching arm (either direct paths, or under logs/sessions for day/arm)."""
    direct = Path(arm)
    if direct.is_dir() and (direct / "telemetry.csv").is_file():
        return [direct]
    for base in (root, root / "logs" / "sessions", root / "logs" / "campaigns"):
        cand = base / arm
        if cand.is_dir() and (cand / "telemetry.csv").is_file():
            return [cand]
    sess_root = root / "logs" / "sessions"
    if not sess_root.is_dir():
        return []
    day_digits = re.sub(r"[^0-9]", "", day)
    slug = arm.replace("_", "-")
    slug_u = arm.replace("-", "_")
    hits = []
    for d in sorted(sess_root.iterdir()):
        if not (d / "telemetry.csv").is_file():
            continue
        if day_digits and len(day_digits) == 8 and not d.name.startswith(day_digits):
            continue
        if slug in d.name or slug_u in d.name:
            hits.append(d)
    return hits


def compare_arms(day: str, arms: list[str], ref: str, root: str | Path = ".") -> dict[str, Any]:
    """Evaluate all requested arms, compute same-arm repeat spread and % change vs ref."""
    root_p = Path(root)
    if not arms:
        raise ValueError("layer_ab: --arms must name at least one arm")
    all_ids = list(dict.fromkeys(arms + [ref]))

    arm_runs: dict[str, list[dict[str, float | str]]] = {}
    for a in all_ids:
        base = _base_arm(a)
        sessions = _find_arm_sessions(day, a, root_p)
        if not sessions and base != a:
            sessions = _find_arm_sessions(day, base, root_p)
        for s in sessions:
            ser = lc.load(lc.LogRef(s.name, s, "session"))
            met = compute_session_metrics(ser)
            if met:
                arm_runs.setdefault(base, []).append(met)

    ref_base = _base_arm(ref)
    if ref_base not in arm_runs or not arm_runs[ref_base]:
        raise ValueError(f"layer_ab: no session data found for reference arm {ref!r} on day {day!r}")
    missing_arms = [a for a in arms if _base_arm(a) not in arm_runs]
    if missing_arms:
        raise ValueError(f"layer_ab: no session data found for arm(s) {', '.join(missing_arms)} on day {day!r}")

    def _agg(runs: list[dict[str, float | str]]) -> tuple[dict[str, float | str], dict[str, float]]:
        means: dict[str, float | str] = {}
        spreads: dict[str, float] = {}
        keys = sorted({k for r in runs for k in r})
        for k in keys:
            vals = [r[k] for r in runs if k in r]
            if all(isinstance(v, str) for v in vals):
                means[k] = vals[0]
                spreads[k] = 0.0
            else:
                fvals = [float(v) for v in vals if isinstance(v, (int, float)) and math.isfinite(float(v))]
                if fvals:
                    means[k] = float(np.mean(fvals))
                    spreads[k] = float(np.max(fvals) - np.min(fvals)) if len(fvals) >= 2 else 0.0
                else:
                    means[k] = float("nan")
                    spreads[k] = 0.0
        return means, spreads

    ref_mean, ref_spread = _agg(arm_runs[ref_base])
    report_arms: dict[str, Any] = {}
    seen_bases = list(dict.fromkeys(_base_arm(a) for a in all_ids))

    for base in seen_bases:
        runs = arm_runs.get(base, [])
        if not runs:
            continue
        mean_m, spread_m = _agg(runs)
        metrics_out: dict[str, Any] = {}
        for k, val in mean_m.items():
            if isinstance(val, str):
                metrics_out[k] = {"value": val, "pct_vs_ref": None, "spread": 0.0, "verdict": val}
                continue
            rval = ref_mean.get(k, float("nan"))
            sp = max(spread_m.get(k, 0.0), ref_spread.get(k, 0.0))
            if isinstance(rval, (int, float)) and math.isfinite(rval) and abs(rval) > 1e-12 and math.isfinite(val):
                diff = abs(val - rval)
                pct = (val - rval) / abs(rval) * 100.0
                verdict = "within noise" if (sp > 0.0 and diff < sp) else f"{pct:+.1f}%"
            else:
                pct = float("nan")
                verdict = "n/a"
            metrics_out[k] = {
                "value": val,
                "pct_vs_ref": pct,
                "spread": sp,
                "verdict": verdict,
            }
        report_arms[base] = {"n_runs": len(runs), "metrics": metrics_out}

    return {"day": day, "ref": ref_base, "arms": report_arms}


def format_report(result: dict[str, Any]) -> str:
    lines = [f"# layer_ab ({result['day']}, ref={result['ref']})"]
    for arm, info in result["arms"].items():
        lines.append(f"\n## Arm: {arm} (n={info['n_runs']})")
        lines.append("| metric | value | % vs ref | same-arm spread | verdict |")
        lines.append("|---|---|---|---|---|")
        for k, m in sorted(info["metrics"].items()):
            v_str = m["value"] if isinstance(m["value"], str) else f"{m['value']:.4g}"
            pct_str = "-" if m["pct_vs_ref"] is None or not math.isfinite(m["pct_vs_ref"]) else f"{m['pct_vs_ref']:+.1f}%"
            sp_str = f"{m['spread']:.4g}"
            lines.append(f"| {k} | {v_str} | {pct_str} | {sp_str} | {m['verdict']} |")
    return "\n".join(lines)


def format_one_line_summaries(result: dict[str, Any]) -> list[str]:
    lines = []
    for arm, info in result["arms"].items():
        parts = []
        for k, m in sorted(info["metrics"].items()):
            v = m["value"]
            v_str = v if isinstance(v, str) else f"{v:.3g}"
            parts.append(f"{k}={v_str} ({m['verdict']}, spread={m['spread']:.3g})")
        lines.append(f"layer_ab {arm} (n={info['n_runs']}, ref={result['ref']}): " + "; ".join(parts[:6]))
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("day", help="lab day (e.g. 2026-10-24) or session directory")
    ap.add_argument("--arms", required=True, help="comma-separated arm ids or session paths")
    ap.add_argument("--ref", required=True, help="reference arm id or session path")
    ap.add_argument("--root", default=".", help="repository root")
    ap.add_argument("--json", help="optional path to write JSON output (markdown written alongside)")
    args = ap.parse_args(argv)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    try:
        res = compare_arms(args.day, arms, args.ref, root=args.root)
    except (ValueError, FileNotFoundError) as e:
        import sys
        print(str(e), file=sys.stderr)
        return 2
    md_text = format_report(res)
    json_path = Path(args.json) if args.json else Path(args.root) / "logs" / "layer_ab" / f"{args.day}.json"
    md_path = json_path.with_suffix(".md")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(res, indent=2, default=float) + "\n", encoding="utf-8")
    md_path.write_text(md_text + "\n", encoding="utf-8")
    for line in format_one_line_summaries(res):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
