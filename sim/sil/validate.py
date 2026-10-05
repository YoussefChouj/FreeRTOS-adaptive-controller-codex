"""Replay logged flights through the SIL (WP-31 F): the logged position setpoints drive the SIL firmware PID and the
simulated feedback is compared with the logged feedback.

A session is a dashboard session dir (telemetry.csv, ground_station/autotune/frf.load_series format) with the core
streams Ctrler.locxPID / locyPID / Z_posPID .Des and .FB (cm, cm, m). The airborne window is Z_posPID.FB > 0.3 m
(PROPOSED). The replay holds the first setpoint during the warmup, then follows the log's setpoints (relative to their
first value). Reported per axis: RMS tracking error FB - Des in the log and in the sim, and the RMS of the pointwise
difference between the simulated and logged FB (meaningful for the doublet, where the setpoint dominates; for hover it
is noise against noise).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from sim.sil import engine
from sim.sil.controllers import ControllerSpec
from sim.sil.plant import DT_C
from sim.sil.scenarios import Scenario, Traj

REPO = Path(__file__).resolve().parents[2]
KEYS = {ax: (f"Ctrler.{m}.Des", f"Ctrler.{m}.FB", s) for ax, m, s in
        (("x", "locxPID", 0.01), ("y", "locyPID", 0.01), ("z", "Z_posPID", 1.0))}
AIRBORNE_Z = 0.3


def find_sessions(root: Path = REPO) -> list[Path]:
    out = []
    for d in ("logs/sessions", "logs/campaigns"):
        out += sorted((root / d).rglob("telemetry.csv")) if (root / d).is_dir() else []
    return out


def load(session: Path) -> dict | None:
    """Uniform 200 Hz series of the airborne window, metres, or None when the streams are missing."""
    from ground_station.autotune.frf import load_series  # local: frf loads scipy.signal (~3 s) at import
    s = load_series(session)
    if not all(k in s for des, fb, _ in KEYS.values() for k in (des, fb)):
        return None
    tz, z = s[KEYS["z"][1]]
    air = tz[z > AIRBORNE_Z]
    if len(air) < 2 or air[-1] - air[0] < 5.0:
        return None
    t = np.arange(air[0], air[-1], DT_C)
    out = {"t": t - t[0]}
    for ax, (des, fb, sc) in KEYS.items():
        out[ax + "_des"] = np.interp(t, *s[des]) * sc
        out[ax + "_fb"] = np.interp(t, *s[fb]) * sc
    span = max(np.ptp(out["x_des"]), np.ptp(out["y_des"]))
    out["kind"] = "hover" if span < 0.05 else "doublet" if span < 1.2 else "path"
    return out


def replay(log: dict, seed: int = 0) -> dict:
    x0, y0 = log["x_des"][0], log["y_des"][0]
    tt = log["t"]
    moving = (np.abs(np.gradient(log["x_des"])) + np.abs(np.gradient(log["y_des"]))) > 1e-7   # TrajFF on (PROPOSED)

    def path(t):
        return (np.interp(t, tt, log["x_des"] - x0), np.interp(t, tt, log["y_des"] - y0),
                np.interp(t, tt, moving.astype(float)) > 0.5)

    scen = Scenario(Traj("replay", "logged setpoints", float(tt[-1]), path), (), float(np.median(log["z_des"])))
    lg = engine.run([engine.Case(ControllerSpec("pid", "replay"), scen, seed)])[0]
    s = lg.t >= 0
    n = min(int(s.sum()), len(tt))
    res = {"kind": log["kind"], "duration_s": float(tt[-1]), "crashed": lg.crash_k >= 0}
    for i, ax in enumerate("xyz"):
        sim_fb = lg.p[s][:n, i] + (x0 if ax == "x" else y0 if ax == "y" else 0.0)
        sim_des = lg.ref[s][:n, i] + (x0 if ax == "x" else y0 if ax == "y" else 0.0)
        log_fb, log_des = log[ax + "_fb"][:n], log[ax + "_des"][:n]
        res[ax] = dict(log_rms_cm=float(np.sqrt(np.mean((log_fb - log_des) ** 2)) * 100),
                       sim_rms_cm=float(np.sqrt(np.mean((sim_fb - sim_des) ** 2)) * 100),
                       fb_diff_rms_cm=float(np.sqrt(np.mean((sim_fb - log_fb) ** 2)) * 100))
    return res


def validate(root: Path = REPO, seed: int = 0) -> list[dict]:
    """Replays every usable session under logs/sessions and logs/campaigns; [] means the matrix is unvalidated."""
    out = []
    for sess in find_sessions(root):
        log = load(sess)
        if log is not None and log["kind"] in ("hover", "doublet"):
            r = replay(log, seed)
            r["session"] = str(sess.parent.relative_to(root))
            out.append(r)
    return out
