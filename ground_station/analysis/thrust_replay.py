"""Thrust estimators (API/thrust_estimators.c) against flight logs: hover balance, battery sag, tilt, disarmed ticks.

Per airborne log with g_thrust_est.* (log_corpus airborne rule, first and last EDGE_S cut), in WINDOW_S windows:
mean motor PWM, sum of the empirical (bench LUT) and blade-element (k_T w^2) per-motor thrusts, the logged
imu_total, both imu_total forms recomputed from Lin_Acc_Z_body and the attitude (pre-WP-34 m (a/c + g) and the
fixed m (a + g c)), and the battery voltage. In a steady hover every thrust estimate should equal the weight, so
sum / g is the mass each estimator implies, and its slope against battery voltage shows what the estimator
misses as the pack sags.

    python -m ground_station.analysis.thrust_replay [--root <checkout with logs/>]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from ground_station.analysis import log_corpus as lc

G = 9.81                    # GRAVITY_MS2 in API/thrust_estimators.c
M_FW = 0.9885               # DRONE_MASS_KG, API/thrust_estimators.c:31 (unverified there)
DT = 0.02                   # 50 Hz analysis grid, the log plan rate
EDGE_S = 2.0                # cut takeoff / landing transients
WINDOW_S = 2.0
MOTORS = [f"mymotor.motor{i}" for i in range(1, 5)]
EMP = [f"g_thrust_est.empirical[{i}]" for i in range(4)]
BLADE = [f"g_thrust_est.blade_element[{i}]" for i in range(4)]
VBAT = ("real_voltage", "status.vbat")
PERIOD = [f"rpm_dbg_period_cyc[{i}]" for i in range(4)]   # DWT cycles per revolution, BSP/rpm.h:84
F_CPU = 168e6                                               # SystemCoreClock (BSP/rpm.c:265)
K_T = 6.80e-6                                               # K_T_CALIBRATED, API/thrust_estimators.c:39


def sum_w2_from_period(periods) -> np.ndarray:
    """sum of omega^2 (rad^2/s^2) over the motors from their revolution periods; a 0 / stale period counts 0."""
    p = np.asarray(periods, float)
    w = np.where(p > 0, 2.0 * np.pi * F_CPU / np.where(p > 0, p, 1.0), 0.0)
    return np.sum(w * w, axis=0)


def imu_total_forms(lin_acc_z_mg, pitch_deg, roll_deg, m=M_FW, g=G):
    """(pre-WP-34, fixed) imu_total from the call-site inputs (StabilizerTask.c:1433: Lin_Acc_Z_body mg -> m/s^2)."""
    a = np.asarray(lin_acc_z_mg, float) * 9.80665 / 1000.0
    c = np.cos(np.radians(pitch_deg)) * np.cos(np.radians(roll_deg))
    old = np.where(np.abs(c) > 0.1, m * (a / np.where(np.abs(c) > 0.1, c, 1.0) + g), 0.0)
    return old, m * (a + g * c)


def windows(t: np.ndarray, mask: np.ndarray) -> list[np.ndarray]:
    """Index arrays of WINDOW_S windows inside each airborne span, EDGE_S trimmed at both ends."""
    out = []
    for a, b in lc.spans(mask, t, 2 * EDGE_S + WINDOW_S):
        edges = np.arange(a + EDGE_S, b - EDGE_S - WINDOW_S + 1e-9, WINDOW_S)
        out += [np.flatnonzero((t >= e) & (t < e + WINDOW_S)) for e in edges]
    return [w for w in out if len(w) > 10]


def slope(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3 or np.ptp(x[ok]) <= 0:
        return float("nan")
    return float(np.polyfit(x[ok], y[ok], 1)[0])


def analyze(series: lc.Series) -> dict | None:
    need = MOTORS + EMP + BLADE + ["g_thrust_est.imu_total"]
    if not all(k in series for k in need):
        return None
    vkey = next((k for k in VBAT if k in series), None)
    extra = [k for k in ("Lin_Acc_Z_body", "imu_data.pit", "imu_data.rol") if k in series]
    t, g = lc.grid(series, need + extra + ([vkey] if vkey else []), DT)
    if not len(t):
        return None
    air = lc.airborne(series, t)
    emp, blade = sum(g[k] for k in EMP), sum(g[k] for k in BLADE)
    # blade-element thrust with today's k_T from the raw periods (held, not interpolated): the logged
    # blade_element[] went through each flight's own firmware RPM and k_T
    rpm_now = (K_T * sum_w2_from_period([lc.hold(series, k, t, 0.0) for k in PERIOD])
               if all(k in series for k in PERIOD) else np.full(len(t), np.nan))
    pwm = sum(g[k] for k in MOTORS) / 4.0
    recompute = len(extra) == 3
    if recompute:
        old, new = imu_total_forms(g["Lin_Acc_Z_body"], g["imu_data.pit"], g["imu_data.rol"])
    rows = []
    for w in windows(t, air):
        r = dict(t=float(t[w[0]]), pwm=float(pwm[w].mean()), emp=float(emp[w].mean()), blade=float(blade[w].mean()),
                 blade_now=float(np.median(rpm_now[w])),
                 imu_logged=float(g["g_thrust_est.imu_total"][w].mean()),
                 vbat=float(g[vkey][w].mean()) if vkey else float("nan"))
        if recompute:
            tilt = np.degrees(np.arccos(np.clip(np.cos(np.radians(g["imu_data.pit"][w]))
                                                * np.cos(np.radians(g["imu_data.rol"][w])), -1, 1)))
            r.update(imu_old=float(old[w].mean()), imu_new=float(new[w].mean()), tilt_deg=float(tilt.mean()))
        rows.append(r)
    armed_key = "DroneStatus.ARM_Status" if "DroneStatus.ARM_Status" in series else "status.arm"
    disarmed = lc.hold(series, armed_key, t, 1.0) < 0.5
    out = dict(rows=rows, vbat_key=vkey,
               disarmed_s=float(disarmed.sum() * DT),
               disarmed_emp_med=float(np.median(emp[disarmed])) if disarmed.any() else float("nan"),
               disarmed_emp_frac=float(np.mean(emp[disarmed] > 1.0)) if disarmed.any() else float("nan"))
    if recompute:
        ok = np.isfinite(old) & np.isfinite(g["g_thrust_est.imu_total"]) & air
        out["imu_old_vs_logged_rms"] = (float(np.sqrt(np.mean((old[ok] - g["g_thrust_est.imu_total"][ok]) ** 2)))
                                        if ok.any() else float("nan"))
    if rows:
        col = {k: np.array([r[k] for r in rows]) for k in rows[0]}
        out.update(n_win=len(rows), pwm=float(np.median(col["pwm"])),
                   m_emp=float(np.median(col["emp"]) / G), m_blade=float(np.median(col["blade"]) / G),
                   m_now=float(np.nanmedian(col["blade_now"]) / G) if np.isfinite(col["blade_now"]).any() else float("nan"),
                   m_imu=float(np.median(col["imu_logged"]) / G),
                   vbat=float(np.median(col["vbat"])), vbat_span=float(np.ptp(col["vbat"])),
                   dpwm_dv=slope(col["vbat"], col["pwm"]), demp_dv=slope(col["vbat"], col["emp"]),
                   dnow_dv=slope(col["vbat"], col["blade_now"]))
        if recompute:
            out.update(tilt_deg=float(np.median(col["tilt_deg"])),
                       imu_fix_pct=float(np.median((col["imu_old"] - col["imu_new"]) / col["imu_new"]) * 100.0))
    return out


def to_markdown(results: dict) -> str:
    lines = ["| log | win | hover PWM | kg implied: LUT / blade logged / blade k_T now / imu | vbat (span) V | dPWM/dV "
             "| dLUT/dV N/V | dblade now/dV N/V | tilt deg | imu old-new % | old vs logged rms N | disarmed LUT N (frac>1N) |",
             "|---|" + "---|" * 11]
    nan = float("nan")
    for log, r in results.items():
        if not r.get("n_win"):
            continue
        lines.append(f"| {log} | {r['n_win']} | {r['pwm']:.0f} | {r['m_emp']:.3f} / {r['m_blade']:.3f} / {r['m_now']:.3f} / "
                     f"{r['m_imu']:.3f} | {r['vbat']:.2f} ({r['vbat_span']:.2f}) | {r['dpwm_dv']:.0f} | {r['demp_dv']:.2f} | "
                     f"{r['dnow_dv']:.2f} | {r.get('tilt_deg', nan):.1f} | {r.get('imu_fix_pct', nan):.2f} | "
                     f"{r.get('imu_old_vs_logged_rms', nan):.3f} | {r['disarmed_emp_med']:.1f} ({r['disarmed_emp_frac']:.2f}) |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(lc.REPO))
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    results = {}
    for ref in lc.find_logs(args.root):
        if "g_thrust_est.imu_total" not in lc.keys(ref):
            continue
        r = analyze(lc.load(ref))
        if r is not None:
            results[ref.name] = r
    print(to_markdown(results))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1, default=float), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
