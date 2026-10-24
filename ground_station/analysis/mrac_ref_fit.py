"""Fit per-axis x/y reference models from an alone-retest shadow session and write logs/ref_fit/<kind>.json.

    python -m ground_station.analysis.mrac_ref_fit <session> --kind lqr|mpc|adrc|pid

Reuses ``refmodel_replay.ref_model`` and ``refmodel_replay.replay_axis`` on the session's velocity demand -> measured
velocity channels (``Ctrler.locxsPID.Des`` -> ``Ctrler.locxsPID.FB`` for x, ``Ctrler.locysPID.Des`` ->
``Ctrler.locysPID.FB`` for y; falls back to ``mrac_state.x/y.r`` and ``.x`` when present). Fits both 1st-order + delay
and 2nd-order + delay over the airborne hold/figure-8 segments, clamps to ``mrac_variants`` firmware bounds (reporting
any clamped parameter), and compares replayed MRAC tracking error against the firmware PID default row
(bw=2.0 rad/s, zeta=0.8, delay=0.0 s; API/mrac.c:90-111).
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize, minimize_scalar

from ground_station.analysis import log_corpus as lc
from ground_station.analysis import mrac_variants as mv
from ground_station.analysis import refmodel_replay as rr
from ground_station.service import ground_plan as gp

KINDS = ("lqr", "mpc", "adrc", "pid")
PID_ROW = {"bw": 2.0, "zeta": 0.8, "delay_s": 0.0}  # API/mrac.c:90-111 s_xyrm row 0

AXIS_CHANNELS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "x": (
        ("Ctrler.locxsPID.Des", "mrac_state.x.r"),
        ("Ctrler.locxsPID.FB", "mrac_state.x.x", "s_ekf_of.x[1]"),
        ("Ctrler.pitchPID.Des", "s_xyc[0].u_prev", "mrac_state.x.u_nom"),
    ),
    "y": (
        ("Ctrler.locysPID.Des", "mrac_state.y.r"),
        ("Ctrler.locysPID.FB", "mrac_state.y.x", "s_ekf_of.x[4]"),
        ("Ctrler.rollPID.Des", "s_xyc[1].u_prev", "mrac_state.y.u_nom"),
    ),
}


def _bounds(field: str, default: tuple[float, float]) -> tuple[float, float]:
    for name, lo, hi in mv.VARIANT_FIELDS:
        if name in gp.REF_FIELD_ALIASES.get(field, (field,)):
            return float(lo), float(hi)
    return default


def _clamp(val: float, field: str, axis: str, default_bounds: tuple[float, float], clamped: list[str]) -> float:
    lo, hi = _bounds(field, default_bounds)
    if val < lo:
        clamped.append(f"{field}_{axis}={val:.4g}->lo={lo:g}")
        return lo
    if val > hi:
        clamped.append(f"{field}_{axis}={val:.4g}->hi={hi:g}")
        return hi
    return float(val)


def resolve_session(session: str | Path, root: str | Path = ".") -> Path:
    """Resolve a session path or id under root, logs/sessions, or logs/campaigns."""
    p = Path(session)
    if p.is_file() and p.name == "telemetry.csv":
        return p.parent
    if p.is_dir() and (p / "telemetry.csv").is_file():
        return p
    for base in (Path(root), Path(root) / "logs" / "sessions", Path(root) / "logs" / "campaigns"):
        cand = base / session
        if cand.is_dir() and (cand / "telemetry.csv").is_file():
            return cand
    raise FileNotFoundError(f"mrac_ref_fit: no session with telemetry.csv for {session!r}")


def _pick_key(series: lc.Series, candidates: tuple[str, ...]) -> str | None:
    for k in candidates:
        if k in series and len(series[k][0]) >= 4:
            return k
    return None


def fit_axis_order(r: np.ndarray, x: np.ndarray, mask: np.ndarray, order: int, dt: float = rr.DT) -> dict[str, float]:
    """Least-squares fit of refmodel_replay.ref_model(r, order, bw, zeta, delay_s, dt) to x on mask."""
    r = np.asarray(r, dtype=float)
    x = np.asarray(x, dtype=float)
    mask = np.asarray(mask, dtype=bool)
    xm_target = x[mask]
    denom = float(np.sqrt(np.mean((xm_target - np.mean(xm_target)) ** 2))) or 1.0

    # Search a wider physical range (0..80 ms delay, 0.2..150 rad/s bw) so _clamp detects when the plant hits a bound
    min_ticks, max_ticks = 0, 16
    best: dict[str, float] | None = None
    if order == 1:
        for d_ticks in range(min_ticks, max_ticks + 1):
            delay_s = d_ticks * dt

            def obj1(bw_v: float) -> float:
                xm, _ = rr.ref_model(r, 1, float(bw_v), 0.8, delay_s, dt)
                err = xm_target - xm[mask]
                return float(np.sqrt(np.mean(err * err)))

            res = minimize_scalar(obj1, bounds=(0.1, 150.0), method="bounded")
            rmse = float(res.fun)
            if best is None or rmse < best["rmse"]:
                best = {"bw": float(res.x), "zeta": 0.8, "delay_s": delay_s,
                        "rmse": rmse, "nrmse": rmse / denom}
        assert best is not None
        return best

    bws = np.geomspace(0.5, 80.0, 24)
    zetas = [0.4, 0.6, 0.8, 1.0, 1.3]
    for d_ticks in range(min_ticks, max_ticks + 1):
        delay_s = d_ticks * dt
        for bw0 in bws:
            for z0 in zetas:
                xm, _ = rr.ref_model(r, 2, float(bw0), float(z0), delay_s, dt)
                err = xm_target - xm[mask]
                rmse = float(np.sqrt(np.mean(err * err)))
                if best is None or rmse < best["rmse"]:
                    best = {"bw": float(bw0), "zeta": float(z0), "delay_s": float(delay_s),
                            "rmse": rmse, "nrmse": rmse / denom, "d_ticks": float(d_ticks)}
    assert best is not None
    cand_ticks = sorted({
        max(min_ticks, min(max_ticks, int(best["d_ticks"]) + delta))
        for delta in (-1, 0, 1)
    })
    for d_ticks in cand_ticks:
        delay_s = d_ticks * dt

        def obj2(p: np.ndarray) -> float:
            bw_v = float(np.clip(p[0], 0.1, 150.0))
            z_v = float(np.clip(p[1], 0.05, 3.0))
            xm, _ = rr.ref_model(r, 2, bw_v, z_v, delay_s, dt)
            err = xm_target - xm[mask]
            return float(np.sqrt(np.mean(err * err)))

        res = minimize(
            obj2,
            x0=[best["bw"], best["zeta"]],
            bounds=[(0.1, 150.0), (0.05, 3.0)],
            method="Nelder-Mead",
        )
        rmse = float(res.fun)
        if rmse < best["rmse"]:
            best = {"bw": float(res.x[0]), "zeta": float(res.x[1]), "delay_s": delay_s,
                    "rmse": rmse, "nrmse": rmse / denom}
    return best


def fit_session(
    session: str | Path,
    kind: str,
    root: str | Path = ".",
    order_pref: str = "auto",
    out_path: str | Path | None = None,
    dt: float = rr.DT,
) -> dict[str, Any]:
    """Fit x/y reference models from a session and write logs/ref_fit/<kind>.json."""
    if kind not in KINDS:
        raise ValueError(f"mrac_ref_fit: kind must be one of {KINDS} (got {kind!r})")
    sess_dir = resolve_session(session, root)
    series = lc.load(lc.LogRef(sess_dir.name, sess_dir, "session"))
    if not series:
        raise ValueError(f"mrac_ref_fit: session {sess_dir} has no telemetry rows")

    clamped: list[str] = []
    per_axis: dict[str, dict[str, Any]] = {}
    for axis, (r_keys, x_keys, u_keys) in AXIS_CHANNELS.items():
        rk = _pick_key(series, r_keys)
        xk = _pick_key(series, x_keys)
        if rk is None or xk is None:
            raise ValueError(
                f"mrac_ref_fit: session {sess_dir.name} is missing {axis} velocity channels "
                f"(wanted {r_keys[0]} and {x_keys[0]})"
            )
        uk = _pick_key(series, u_keys)
        keys = [rk, xk] + ([uk] if uk else [])
        t, gridded = lc.grid(series, keys, dt)
        if len(t) < 20:
            raise ValueError(f"mrac_ref_fit: session {sess_dir.name} has too few samples ({len(t)}) on axis {axis}")
        r, x = gridded[rk], gridded[xk]
        u_nom = gridded[uk] if uk else np.zeros_like(x)

        air = lc.airborne(series, t)
        # Exclude the first 1 s climb transient when airborne is long enough; fall back to all samples in synthetic logs
        if air.sum() >= 40:
            idx = np.flatnonzero(air)
            trim = min(int(1.0 / dt), len(idx) // 4)
            mask = np.zeros_like(air)
            mask[idx[trim:]] = True
        elif air.any():
            mask = air
        else:
            mask = np.ones(len(t), dtype=bool)
            mask[: min(int(0.5 / dt), len(t) // 5)] = False

        f1 = fit_axis_order(r, x, mask, order=1, dt=dt)
        f2 = fit_axis_order(r, x, mask, order=2, dt=dt)

        bw1 = _clamp(f1["bw"], "ref_model_bw", axis, (0.5, 100.0), clamped)
        d1 = _clamp(f1["delay_s"], "ref_delay_s", axis, (0.0, 0.035), clamped)
        bw2 = _clamp(f2["bw"], "ref_model_bw", axis, (0.5, 100.0), clamped)
        d2 = _clamp(f2["delay_s"], "ref_delay_s", axis, (0.0, 0.035), clamped)
        z2 = _clamp(f2["zeta"], "ref_model_zeta", axis, (0.1, 2.0), clamped)

        # Replay MRAC tracking error with the fitted reference vs the firmware PID row (bw=2.0, delay=0.0)
        proxy_axis = "pitch" if axis == "x" else "roll"
        cross = np.zeros_like(x)
        rep_pid = rr.replay_axis(proxy_axis, x, r, u_nom, cross, mask, kind=1,
                                 bw=PID_ROW["bw"], zeta=PID_ROW["zeta"], delay_s=PID_ROW["delay_s"], dt=dt)
        rep_fit1 = rr.replay_axis(proxy_axis, x, r, u_nom, cross, mask, kind=1,
                                  bw=bw1, zeta=0.8, delay_s=d1, dt=dt)
        rep_fit2 = rr.replay_axis(proxy_axis, x, r, u_nom, cross, mask, kind=2,
                                  bw=bw2, zeta=z2, delay_s=d2, dt=dt)
        rms_pid = rr._rms(rep_pid["e"][mask])
        rms_fit1 = rr._rms(rep_fit1["e"][mask])
        rms_fit2 = rr._rms(rep_fit2["e"][mask])

        per_axis[axis] = {
            "bw_1st": bw1,
            "delay_1st": d1,
            "rmse_1st": f1["rmse"],
            "nrmse_1st": f1["nrmse"],
            "bw_2nd": bw2,
            "zeta_2nd": z2,
            "delay_2nd": d2,
            "rmse_2nd": f2["rmse"],
            "nrmse_2nd": f2["nrmse"],
            "rms_e_pid_row": rms_pid,
            "rms_e_fit_1st": rms_fit1,
            "rms_e_fit_2nd": rms_fit2,
            "replay_gain_1st": (rms_pid - rms_fit1) / rms_pid if rms_pid > 0 else 0.0,
            "replay_gain_2nd": (rms_pid - rms_fit2) / rms_pid if rms_pid > 0 else 0.0,
        }

    res1 = 0.5 * (per_axis["x"]["rmse_1st"] + per_axis["y"]["rmse_1st"])
    res2 = 0.5 * (per_axis["x"]["rmse_2nd"] + per_axis["y"]["rmse_2nd"])
    has_zeta = gp.field_number("ref_model_zeta") is not None
    if order_pref in ("1st", "2nd"):
        order = order_pref
    else:
        order = "2nd" if (has_zeta and res2 < 0.95 * res1) else "1st"

    use_2nd = order == "2nd"
    bw_x = per_axis["x"]["bw_2nd" if use_2nd else "bw_1st"]
    bw_y = per_axis["y"]["bw_2nd" if use_2nd else "bw_1st"]
    delay_x = per_axis["x"]["delay_2nd" if use_2nd else "delay_1st"]
    delay_y = per_axis["y"]["delay_2nd" if use_2nd else "delay_1st"]
    zeta_x = per_axis["x"]["zeta_2nd"]
    zeta_y = per_axis["y"]["zeta_2nd"]
    gain_key = "replay_gain_2nd" if use_2nd else "replay_gain_1st"
    replay_gain = 0.5 * (per_axis["x"][gain_key] + per_axis["y"][gain_key])

    payload: dict[str, Any] = {
        "kind": kind,
        "session": sess_dir.name,
        "order": order,
        "ref_model_bw_x": round(bw_x, 4),
        "ref_model_bw_y": round(bw_y, 4),
        "ref_delay_s_x": round(delay_x, 4),
        "ref_delay_s_y": round(delay_y, 4),
        "ref_model_zeta_x": round(zeta_x, 4),
        "ref_model_zeta_y": round(zeta_y, 4),
        "bw_x": round(bw_x, 4),
        "bw_y": round(bw_y, 4),
        "zeta_x": round(zeta_x, 4),
        "zeta_y": round(zeta_y, 4),
        "delay_x": round(delay_x, 4),
        "delay_y": round(delay_y, 4),
        "residual_1st": round(res1, 5),
        "residual_2nd": round(res2, 5),
        "replay_gain": round(replay_gain, 4),
        "clamped": clamped,
        "axes": per_axis,
    }
    target = Path(out_path) if out_path else gp.ref_fit_path(kind, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("session", help="session directory or id under logs/sessions/")
    ap.add_argument("--kind", required=True, choices=KINDS, help="controller kind (lqr, mpc, adrc, pid)")
    ap.add_argument("--order", default="auto", choices=("auto", "1st", "2nd"), help="reference model order")
    ap.add_argument("--root", default=".", help="repository root (writes <root>/logs/ref_fit/<kind>.json)")
    ap.add_argument("--out", help="explicit output json path")
    args = ap.parse_args(argv)
    res = fit_session(args.session, args.kind, root=args.root, order_pref=args.order, out_path=args.out)
    clamp_note = f" [CLAMPED: {', '.join(res['clamped'])}]" if res["clamped"] else ""
    print(
        f"mrac_ref_fit {res['kind']} ({res['session']}, {res['order']}): "
        f"bw=({res['bw_x']:.2f}, {res['bw_y']:.2f}) rad/s "
        f"zeta=({res['zeta_x']:.2f}, {res['zeta_y']:.2f}) "
        f"delay=({res['delay_x']*1e3:.0f}, {res['delay_y']*1e3:.0f}) ms "
        f"res1={res['residual_1st']:.4f} res2={res['residual_2nd']:.4f} "
        f"replay_gain={res['replay_gain']*100:+.1f}%{clamp_note}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
