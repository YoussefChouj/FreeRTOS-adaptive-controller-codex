"""SIL metrics on the scored window (scenario t >= 0) and the flight abort rules of
docs/analysis/controller-roadmap-2026-10-03.md:189-190:
  |u_ad| > 0.5 |u_nom| for 1 s, tilt > 12 deg, a motor at the 4000 clamp for more than 0.5 s, a simplex trip,
  position error > 0.5 m.
Interpretations (PROPOSED): |u_ad| and |u_nom| are 1 s moving RMS per axis of the injected correction
(Controller_Update - u_nom, mixer units) and of u_nom, so a u_nom zero crossing is not an abort; "position error" is
the 3-D distance to the reference in the navigation frame; a crash (plant.run rule) counts as an abort.
host_us is the host CPU time of one firmware tick (QueryPerformanceCounter in sil_server.c), not STM32 cost: the
flight cost is mrac_cyc (DWT cycles) on the target.
"""
from __future__ import annotations

import numpy as np

from sim.sil.plant import DT_C

AXES = ("p", "r", "y", "z")
WIN = int(round(1.0 / DT_C))
ABORTS = ("crash", "uad", "tilt12", "clamp4000", "simplex", "pos05")


def _movrms(x: np.ndarray, n: int) -> np.ndarray:
    c = np.cumsum(np.r_[0.0, x.astype(float) ** 2])
    return np.sqrt(np.maximum(c[n:] - c[:-n], 0.0) / n) if len(x) >= n else np.zeros(0)


def _longest(mask: np.ndarray) -> int:
    best = run = 0
    for v in mask:
        run = run + 1 if v else 0
        best = max(best, run)
    return best


def _overshoot_cm(t, p, ref) -> float:
    """Doublet: largest excursion past the target along the move direction during the dwells after each move."""
    best = 0.0
    for ax in (0, 1):
        r = ref[:, ax]
        moving = np.r_[False, np.abs(np.diff(r)) > 1e-9]
        k = 0
        while k < len(r):
            if moving[k]:
                s = k
                while k < len(r) and moving[k]:
                    k += 1
                d = np.sign(r[min(k, len(r) - 1)] - r[s - 1])
                e = k
                while e < len(r) and not moving[e]:
                    e += 1
                if d != 0 and e > k:
                    best = max(best, float(np.max((p[k:e, ax] - r[k:e]) * d)) * 100.0)
            k += 1
    return max(best, 0.0)


def compute(lg) -> dict:
    s = lg.t >= 0
    if lg.crash_k >= 0:
        s &= np.arange(len(lg.t)) < lg.crash_k
    err = np.linalg.norm(lg.p - lg.ref, axis=1)
    tilt = np.degrees(np.arccos(np.clip(np.cos(np.radians(lg.att[:, 0])) * np.cos(np.radians(lg.att[:, 1])), -1, 1)))
    mot = lg.mot[s]
    at_clamp = np.any((mot <= 2000) | (mot >= 4000), axis=1)
    at_top = np.any(mot >= 4000, axis=1)
    ratio, ratio_rms = {}, {}
    for a in AXES:
        c, u = lg.out[f"corr_{a}"][s], lg.out[f"unom_{a}"][s]
        rc, ru = _movrms(c, WIN), _movrms(u, WIN)
        ratio[a] = float(np.max(rc / np.maximum(ru, 1e-6))) if len(rc) else 0.0
        ratio_rms[a] = float(np.sqrt(np.mean(c.astype(float) ** 2)) / max(np.sqrt(np.mean(u.astype(float) ** 2)), 1e-6))
    th = sum(lg.out[f"th_{a}"][s].astype(float) for a in AXES)
    th1 = th[::WIN]
    rising = np.r_[False, np.diff(th1) > 1e-4 * max(float(th1.max()) if len(th1) else 0.0, 1e-6)]
    dz = (lg.p[s, 2] - lg.ref[s, 2]) * 100.0
    m = dict(
        rmse_cm=float(np.sqrt(np.mean(err[s] ** 2)) * 100.0),
        z_rmse_cm=float(np.sqrt(np.mean(dz ** 2))),
        uad_axes="".join(a for a in AXES if ratio[a] > 0.5),
        max_err_cm=float(err[s].max() * 100.0),
        max_tilt_deg=float(tilt[s].max()),
        clamp_s=float(at_clamp.sum() * DT_C),
        top_run_s=float(_longest(at_top) * DT_C),
        uad_ratio=max(ratio.values()),
        uad_ratio_axis=max(ratio, key=ratio.get),
        uad_rms_ratio=max(ratio_rms.values()),
        theta_end=float(th[-1]) if len(th) else 0.0,
        theta_rise_s=float(_longest(rising) * WIN * DT_C),
        heading_drift_deg=float(np.abs(lg.hdg[s]).max()),
        overshoot_cm=_overshoot_cm(lg.t[s], lg.p[s], lg.ref[s]),
        host_us=float(lg.out["host_us"][s].mean()),
        crashed=lg.crash_k >= 0,
        applied=all(lg.applied),
    )
    m["aborts"] = [name for name, hit in (
        ("crash", m["crashed"]), ("uad", m["uad_ratio"] > 0.5), ("tilt12", m["max_tilt_deg"] > 12.0),
        ("clamp4000", m["top_run_s"] > 0.5), ("simplex", bool(np.any(lg.out["tripped"][s] > 0))),
        ("pos05", m["max_err_cm"] > 50.0)) if hit]
    return m
