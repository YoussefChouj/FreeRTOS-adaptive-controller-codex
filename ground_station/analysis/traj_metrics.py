"""Trajectory preset kinematics, speed/curvature caps and tracking metrics (WP-D, 2026-10-17).

The preset model mirrors the firmware: shapes from API/traj_shape.c, the traj_go refusals (0xEE range / too fast,
0xEF fence) from TASK/AutoflyTask.c, and the limit modes (s_lim: tilt, vxy, trajv, yawr) parsed from
TASK/StabilizerTask.c, so a .fly file is checked on the ground before it reaches the drone. On top of the firmware
checks it adds a curvature cap (the reference's horizontal acceleration must fit k * g * tan(tilt), k = K_ACCEL
PROPOSED) and a yaw-follow lag warning. Metrics for flown logs: along/cross-track RMSE and the discrete Frechet
distance.

CLI:  python -m ground_station.analysis.traj_metrics check FILE.fly [--lim N] [--k 0.6]
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
STAB_SRC = ROOT / "TASK" / "StabilizerTask.c"

G_CMS2 = 981.0
K_ACCEL = 0.6                 # PROPOSED: share of the tilt budget the reference may spend on path acceleration
TRAJ_TOTAL_MAX_S = 25.0       # AutoflyTask.c TRAJ_TOTAL_MAX_S
TRAJ_Z_OFF_MAX_CM = 40.0      # AutoflyTask.c TRAJ_Z_OFF_MAX_CM
FENCE_X_M, FENCE_Y_M = 1.3, 1.7   # API/wfb_traj.c s_default_limits x_abs, y_abs
CEILING_M = 1.7 - 0.3         # API/wfb_safety.c ceiling - soft margin
PRESET_IDS = {"step": 1, "zigzag": 2, "circle": 3, "figure8": 4}   # platform/wfb_var.py PRESETS
LAPS_MAX = {3: 3.0, 4: 4.0}   # circ_laps, f8_laps caps at traj_go

# AutoflyTask.c `TrajParams_t traj_p` boot values (field names as in the VAR_WRITE_ROW lines, traj_p. stripped), after
# StabilizerTask.c Lab_StagePoll applies the boot lab_stage 1 row at the first ground tick: f8 tilt 30, 2 laps, 90 deg/s
TRAJ_DEFAULTS: dict[str, float] = {
    "profile": 1, "axis": 0, "zz_shape": 0, "f8_type": 1,
    "move_s": 20.0, "ret_s": 3.0, "hold_s": 2.0, "ramp_s": 2.0,
    "step_cm": 40.0, "zz_amp_cm": 30.0, "zz_hz": 0.2,
    "circ_r_cm": 40.0, "circ_laps": 1.0, "f8_a_cm": 70.0, "f8_laps": 2.0, "f8_tilt_deg": 30.0,
    "yaw_follow": 1, "yaw_rate_dps": 90.0, "yaw_nose_deg": 90.0,
    "start_fixed": 1, "start_x_cm": 0.0, "start_y_cm": 0.0, "start_z_m": 0.85, "goto_s": 4.0, "settle_s": 2.0,
}


@dataclass(frozen=True)
class LimitMode:
    tilt_deg: float
    vxy_cms: float
    trajv_cms: float
    yawr_dps: float


_NUM = r"([-+]?(?:\d+\.?\d*|\.\d+))[fF]?"
_LIM_ROW = re.compile(r"^[ \t]*LIM_ROW\(\s*%s\s*,\s*%s\s*,\s*%s\s*,\s*%s\s*\)" % ((_NUM,) * 4), re.M)


def load_limits(path: Path = STAB_SRC) -> list[LimitMode]:
    """s_lim rows in lim_mode order (0 slow, 1 conservative, 2 max)."""
    rows = [LimitMode(*map(float, m.groups())) for m in _LIM_ROW.finditer(path.read_text(encoding="utf-8"))]
    if not rows:
        raise ValueError(f"{path.name}: no LIM_ROW lines")
    return rows


def path_seconds(p: dict) -> float:
    """Time over which the full phase runs at cruise rate: the two cosine ramps together cost one ramp_s."""
    return p["move_s"] - (p["ramp_s"] if int(p["profile"]) == 1 else 0.0)


def phase(t: np.ndarray, p: dict, laps: float) -> np.ndarray:
    """theta(t): cruise rate 2 pi laps / path_s, rate ramped by half cosines over ramp_s at both ends (profile 1)."""
    om = 2.0 * math.pi * laps / path_seconds(p)
    move, ramp = p["move_s"], p["ramp_s"]
    if int(p["profile"]) != 1:
        return om * t
    t = np.clip(t, 0.0, move)
    up = 0.5 * om * (t - ramp / math.pi * np.sin(math.pi * np.minimum(t, ramp) / ramp))
    th = np.where(t < ramp, up, 0.5 * om * ramp + om * (t - ramp))
    td = np.maximum(t - (move - ramp), 0.0)   # time into the final ramp, rate 0.5 om (1 + cos(pi td / ramp))
    lost = 0.5 * om * (td - ramp / math.pi * np.sin(math.pi * td / ramp))
    return np.where(t > move - ramp, th - lost, th)


def shape_offset(p: dict, traj_id: int, th: np.ndarray) -> np.ndarray:
    """(N, 3) offset [cm] from the start point, API/traj_shape.c TrajShape_Offset."""
    z = np.zeros_like(th)
    if traj_id == 3:
        r = p["circ_r_cm"]
        return np.stack([r * (np.cos(th) - 1.0), r * np.sin(th), z], axis=1)
    a = p["f8_a_cm"]
    s, c = np.sin(th), np.cos(th)
    if int(p["f8_type"]) == 1:   # Gerono
        dx, y = 0.5 * a * np.sin(2.0 * th), a * s
    else:                        # Bernoulli
        d = 1.0 + s * s
        dx, y = a * (c / d - 1.0), a * s * c / d
    tr = math.radians(p["f8_tilt_deg"])
    return np.stack([dx, y * math.cos(tr), y * math.sin(tr)], axis=1)


@dataclass
class Kinematics:
    traj_id: int
    v_peak: float          # cm/s, the firmware's own peak-speed estimate (what trajv is compared with)
    v_ret: float           # cm/s, return-leg cosine peak to the farthest box corner
    a_xy_peak: float       # cm/s^2, peak horizontal reference acceleration (numeric), nan for a triangle zigzag
    kappa_max: float       # 1/cm, peak curvature (circle / figure-8), 0 otherwise
    yaw_rate_peak: float   # deg/s, peak tangent heading rate (circle / figure-8), 0 otherwise
    box: tuple             # x0, x1, y0, y1, z0, z1 [cm] offsets
    refuse: str            # "" or the firmware refusal this preset would get (0xEE ...)


def kinematics(p: dict, traj_id: int, dt: float = 0.005) -> Kinematics:
    """Peak speed / accel / curvature of one preset with traj_p fields `p` (defaults merged by the caller)."""
    refuse = ""
    if (p["move_s"] < 5.0 or not 3.0 <= p["ret_s"] <= 10.0 or not 0.0 <= p["hold_s"] <= 5.0
            or p["move_s"] + p["ret_s"] + p["hold_s"] > TRAJ_TOTAL_MAX_S + 1e-3
            or (int(p["profile"]) == 1 and not 0.5 <= p["ramp_s"] <= 0.25 * p["move_s"])):
        refuse = "0xEE timing (move>=5, ret 3-10, hold 0-5, sum<=25, ramp 0.5..move/4)"
    if int(p["yaw_follow"]) == 1 and p["ret_s"] + p["hold_s"] < 180.0 / p["yaw_rate_dps"]:
        refuse = refuse or "0xEE ret_s+hold_s too short for the yaw swing back"
    a_xy, kmax, yawr = float("nan"), 0.0, 0.0
    x0 = x1 = y0 = y1 = z0 = z1 = 0.0
    if traj_id == 1:
        step = p["step_cm"]
        v = 0.5 * math.pi * abs(step) / p["ramp_s"]
        a_xy = 0.5 * math.pi ** 2 * abs(step) / p["ramp_s"] ** 2
        lo, hi = min(step, 0.0), max(step, 0.0)
    elif traj_id == 2:
        amp, hz = p["zz_amp_cm"], p["zz_hz"]
        v = (4.0 if int(p["zz_shape"]) == 0 else 2.0 * math.pi) * amp * hz
        if int(p["zz_shape"]) == 1:
            a_xy = amp * (2.0 * math.pi * hz) ** 2
        lo, hi = -amp, amp
    else:
        laps = p["circ_laps"] if traj_id == 3 else p["f8_laps"]
        size = p["circ_r_cm"] if traj_id == 3 else p["f8_a_cm"]
        if not (10.0 <= size <= 80.0 and 0.0 < laps <= LAPS_MAX[traj_id]):
            refuse = refuse or "0xEE size 10-80 cm / laps range"
        if traj_id == 4 and abs(p["f8_tilt_deg"]) > 60.0:
            refuse = refuse or "0xEE f8_tilt_deg > 60"
        om = 2.0 * math.pi * laps / path_seconds(p)
        if traj_id == 3:
            v = om * size
            x0, y0, y1 = -2.0 * size, -size, size
        elif int(p["f8_type"]) == 1:
            v = 1.42 * om * size
            x0, x1, y0, y1 = -0.5 * size, 0.5 * size, -size, size
        else:
            v = om * size
            x0, y0, y1 = -2.0 * size, -0.36 * size, 0.36 * size
        if traj_id == 4:
            tr = math.radians(p["f8_tilt_deg"])
            z1 = abs(y1 * math.sin(tr))
            z0, y0, y1 = -z1, y0 * math.cos(tr), y1 * math.cos(tr)
            if z1 > TRAJ_Z_OFF_MAX_CM:
                refuse = refuse or "0xEE tilted figure-8 z > 40 cm"
        t = np.arange(0.0, p["move_s"] + dt / 2, dt)
        pos = shape_offset(p, traj_id, phase(t, p, laps))
        vel = np.gradient(pos, dt, axis=0)
        acc = np.gradient(vel, dt, axis=0)
        a_xy = float(np.max(np.hypot(acc[:, 0], acc[:, 1])))
        sp = np.linalg.norm(vel, axis=1)
        moving = sp > 5.0
        cr = np.linalg.norm(np.cross(vel, acc), axis=1)
        kmax = float(np.max(cr[moving] / sp[moving] ** 3)) if moving.any() else 0.0
        hd = np.unwrap(np.arctan2(vel[:, 1], vel[:, 0]))
        hr = np.abs(np.gradient(hd, dt))
        mxy = np.hypot(vel[:, 0], vel[:, 1]) > 5.0
        yawr = float(np.degrees(np.max(hr[mxy]))) if mxy.any() else 0.0
    if traj_id <= 2:
        ax = int(p["axis"])
        if ax == 0:
            x0, x1 = lo, hi
        elif ax == 1:
            y0, y1 = lo, hi
        else:
            z0, z1 = lo, hi
            if -lo > TRAJ_Z_OFF_MAX_CM or hi > TRAJ_Z_OFF_MAX_CM:
                refuse = refuse or "0xEE z offset > 40 cm"
    corner = math.sqrt(max(-x0, x1) ** 2 + max(-y0, y1) ** 2 + max(-z0, z1) ** 2)
    v_ret = 0.5 * math.pi * corner / p["ret_s"]
    return Kinematics(traj_id, v, v_ret, a_xy, kmax, yawr, (x0, x1, y0, y1, z0, z1), refuse)


def accel_cap(lim: LimitMode, k: float = K_ACCEL) -> float:
    """Horizontal reference acceleration the limit mode allows [cm/s^2]: k * g * tan(tilt)."""
    return k * G_CMS2 * math.tan(math.radians(lim.tilt_deg))


def curvature_speed_cap(kappa: float, lim: LimitMode, k: float = K_ACCEL) -> float:
    """Highest speed through curvature kappa [1/cm] with a_n = v^2 kappa <= accel_cap (v = sqrt(a_n / kappa))."""
    return math.inf if kappa <= 0.0 else math.sqrt(accel_cap(lim, k) / kappa)


def check(p: dict, traj_id: int, lim: LimitMode, home_m=(0.0, 0.0, 0.85), k: float = K_ACCEL):
    """-> (Kinematics, [(level, message)]); level REFUSE = traj_go would refuse, WARN = over the ground caps."""
    kin = kinematics(p, traj_id)
    out = []
    if kin.refuse:
        out.append(("REFUSE", kin.refuse))
    if kin.v_peak > lim.trajv_cms or kin.v_ret > lim.trajv_cms:
        out.append(("REFUSE", f"0xEE too fast: peak {kin.v_peak:.0f} / return {kin.v_ret:.0f} cm/s > trajv "
                              f"{lim.trajv_cms:.0f}"))
    x0, x1, y0, y1, _, z1 = kin.box
    hx, hy, hz = home_m
    if (hx + x0 / 100 < -FENCE_X_M or hx + x1 / 100 > FENCE_X_M or hy + y0 / 100 < -FENCE_Y_M
            or hy + y1 / 100 > FENCE_Y_M or hz + z1 / 100 > CEILING_M):
        out.append(("REFUSE", "0xEF fence: box leaves x +-1.3 / y +-1.7 m or the 1.4 m ceiling"))
    cap = accel_cap(lim, k)
    if not math.isnan(kin.a_xy_peak) and kin.a_xy_peak > cap:
        out.append(("WARN", f"accel {kin.a_xy_peak:.0f} cm/s^2 > {k:g} g tan{lim.tilt_deg:.0f} = {cap:.0f}"))
    if kin.yaw_rate_peak > min(p["yaw_rate_dps"], lim.yawr_dps) and int(p["yaw_follow"]) == 1:
        out.append(("WARN", f"yaw-follow lags: tangent turns {kin.yaw_rate_peak:.0f} deg/s > "
                            f"{min(p['yaw_rate_dps'], lim.yawr_dps):.0f}"))
    return kin, out


def max_laps(p: dict, traj_id: int, lim: LimitMode, k: float = K_ACCEL) -> float:
    """Most laps (circle / figure-8) inside trajv and the accel cap: speed ~ laps, accel ~ laps^2."""
    laps = p["circ_laps"] if traj_id == 3 else p["f8_laps"]
    kin = kinematics(p, traj_id)
    f = min(lim.trajv_cms / kin.v_peak, math.sqrt(accel_cap(lim, k) / kin.a_xy_peak))
    return min(LAPS_MAX[traj_id], laps * f)


# ---------------------------------------------------------------- tracking metrics (flown logs)

def along_cross_rmse(ref: np.ndarray, act: np.ndarray) -> dict:
    """Time-aligned (N, 2|3) reference and actual -> along / cross-track RMSE along the reference tangent.

    Samples where the reference is not moving (no tangent) count only in the total RMSE.
    """
    ref, act = np.asarray(ref, float), np.asarray(act, float)
    e = act - ref
    d = np.gradient(ref, axis=0)
    n = np.linalg.norm(d, axis=1)
    mv = n > 1e-9 * max(1.0, float(np.max(np.abs(ref))))
    tot = float(np.sqrt(np.mean(np.sum(e * e, axis=1))))
    if not mv.any():
        return {"along_rmse": float("nan"), "cross_rmse": float("nan"), "rmse": tot, "n_moving": 0}
    tng = d[mv] / n[mv, None]
    al = np.sum(e[mv] * tng, axis=1)
    cr = np.linalg.norm(e[mv] - al[:, None] * tng, axis=1)
    return {"along_rmse": float(np.sqrt(np.mean(al ** 2))), "cross_rmse": float(np.sqrt(np.mean(cr ** 2))),
            "rmse": tot, "n_moving": int(mv.sum())}


def discrete_frechet(P: np.ndarray, Q: np.ndarray, max_points: int = 500) -> float:
    """Discrete Frechet distance (Eiter & Mannila 1994), both curves resampled to <= max_points by index."""
    P, Q = np.asarray(P, float), np.asarray(Q, float)
    if len(P) > max_points:
        P = P[np.linspace(0, len(P) - 1, max_points).round().astype(int)]
    if len(Q) > max_points:
        Q = Q[np.linspace(0, len(Q) - 1, max_points).round().astype(int)]
    ca = np.maximum.accumulate(np.linalg.norm(Q - P[0], axis=1))
    for i in range(1, len(P)):
        di = np.linalg.norm(Q - P[i], axis=1)
        m1 = np.minimum(ca, np.concatenate(([np.inf], ca[:-1])))
        new = np.empty_like(ca)
        new[0] = max(ca[0], di[0])
        for j in range(1, len(Q)):
            new[j] = max(di[j], min(m1[j], new[j - 1]))
        ca = new
    return float(ca[-1])


# ---------------------------------------------------------------- .fly checker

def _sets(step: dict) -> dict:
    body = next(iter(step.values())) if isinstance(step, dict) else None
    if isinstance(body, dict) and isinstance(body.get("set"), dict):
        return body["set"]
    return {}


def check_fly(path: Path, lim_mode: int | None = None, k: float = K_ACCEL) -> list[dict]:
    """Every preset of a .fly file, with the firmware's sticky traj_p state and the lim_mode in force."""
    from ground_station.platform.wfb_var import traj_defaults   # read-only use
    from ground_station.service.fly_file import compile_fly
    camp, _ = compile_fly(Path(path).read_text(encoding="utf-8"), Path(path))
    lims = load_limits()
    # scenario_schema._compile_preset writes ALL of traj_p before every traj_go: the ground copy of the boot values
    # (f8 tilt 0, not the lab_stage row) overlaid with the step's own fields, start_z_m = the takeoff hover z
    base = {k[7:]: v for k, v in traj_defaults().items()}
    rows = []
    for exp in camp["experiments"]:
        lm, home, hz = 0, (0.0, 0.0, 0.85), base["start_z_m"]
        for step in exp["scenario"]["steps"]:
            for key, val in _sets(step).items():
                if key == "lim_mode":
                    lm = int(val)
            if isinstance(step, dict) and "takeoff" in step:
                hz = float(step["takeoff"]["z"])
                home = (home[0], home[1], hz)
            if isinstance(step, dict) and "goto" in step:
                g = step["goto"]
                home = (g.get("x", home[0]), g.get("y", home[1]), g.get("z", home[2]))
            if not (isinstance(step, dict) and "preset" in step):
                continue
            pr = dict(step["preset"])
            name = pr.pop("name")
            tp = dict(base, start_z_m=hz)
            tp.update(pr)           # fly_file has already folded the sticky `set traj_p.*` lines into pr
            mode = lm if lim_mode is None else lim_mode
            st = (tp["start_x_cm"] / 100, tp["start_y_cm"] / 100, tp["start_z_m"]) if tp["start_fixed"] else home
            kin, notes = check(tp, PRESET_IDS[name], lims[mode], st, k)
            rows.append({"exp": exp["name"], "preset": name, "lim": mode, "kin": kin, "notes": notes})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="check every preset of a .fly file against traj_go and the caps")
    c.add_argument("fly", type=Path)
    c.add_argument("--lim", type=int, default=None, help="force a lim_mode (default: the file's set lim_mode, else 0)")
    c.add_argument("--k", type=float, default=K_ACCEL)
    a = ap.parse_args(argv)
    bad = 0
    print(f"{'exp':24} {'preset':8} lim  v_pk  v_ret  a_xy  yaw/s  verdict")
    for r in check_fly(a.fly, a.lim, a.k):
        kin = r["kin"]
        verdict = "; ".join(f"{lv} {msg}" for lv, msg in r["notes"]) or "OK"
        bad += any(lv == "REFUSE" for lv, _ in r["notes"])
        print(f"{r['exp'][:24]:24} {r['preset']:8} {r['lim']:3} {kin.v_peak:5.0f} {kin.v_ret:6.0f} "
              f"{kin.a_xy_peak:5.0f} {kin.yaw_rate_peak:6.0f}  {verdict}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
