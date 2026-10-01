"""Calibrate the cascade sim on f17 logs, then rank the drift fixes (WP-10).

    python -m ground_station.research.sim.cascade_rank --logs logs/vofa [--quick]

Prints the calibration tables (log targets, plant, sim vs log) and one ranking table per scenario
(S1 hover, S2 asymmetric load, S3 dense waypoints). Every candidate runs PID-only and with the MRAC
offload model, nominal and with the plant +-30% (robustness). Positions are true positions, cm, log frame.
"""
from __future__ import annotations

import argparse
import itertools
import pathlib
import time

import numpy as np

from ground_station.research.sim import cascade as c
from ground_station.research.sim.constants import (
    MIXER_R_P,
    PITCH_DELAY,
    PITCH_K,
    PITCH_POLE,
    ROLL_DELAY,
    ROLL_K,
    ROLL_POLE,
)

AXES = ("roll", "pitch")
FLIGHTS = {  # name prefix, rows, MRAC on
    "shadow14": ("f17_hover_shadow14_", "F0", False),
    "shadow4": ("f17_hover_shadow4_", "F2", False),
    "active15": ("f17_hover_active15_", "F0", True),
}
WP9_TRIM = {"roll": -1.24, "pitch": -0.90}       # deg, controller frame (WP-9 drift_rootcause, fleet mean)
WP9_PUSH = {"roll": -15.9, "pitch": 14.3}         # cm/s^2, WP-9 mean push to absorb (a_A + a_B), log frame


# --------------------------------------------------------------------------- candidates

def _rows(**over: dict) -> dict[str, c.PidRow]:
    return {k: (c.F0_ROWS[k].with_(**over[k]) if k in over else c.F0_ROWS[k]) for k in c.F0_ROWS}


F1A = {"ang": {"SumEMax": 600}}
F1B = {"rate": {"SumEMax": 4000}}
F1C = {"rate": {"SumEMax": 6000}}
F1W = {"ang": {"UiMax": 26, "SumEMax": 1300}, "rate": {"UiMax": 160, "SumEMax": 16000}}
# CTE addition: F1w with the integral separation opened (EMin) so a load step can still charge the integrators.
F1X = {"ang": {"UiMax": 26, "SumEMax": 1300, "EMin": 10}, "rate": {"UiMax": 160, "SumEMax": 16000, "EMin": 50}}
F3 = {"pos": dict(zip(c.ROW_FIELDS, (0.8, 0.0013, 4.0, 300, 300, 5, 50, 3850, 10))),
      "vel": dict(zip(c.ROW_FIELDS, (3.0, 0.008, 6.0, 600, 600, 100, 100, 12500, 10)))}
F4 = {"pos": F3["pos"], "vel": dict(F3["vel"], Ki=0.005)}


def _merge(*parts: dict) -> dict:
    out: dict = {}
    for p in parts:
        for k, v in p.items():
            out[k] = dict(out.get(k, {}), **v)
    return out


# name -> (row overrides, trim feed-forward, velocity feed-forward, accel feed-forward)
CANDIDATES = {
    "F0": ({}, False, False, False),
    "F1a": (F1A, False, False, False),
    "F1b": (F1B, False, False, False),
    "F1c": (F1C, False, False, False),
    "F1": (_merge(F1A, F1C), False, False, False),
    "F1w": (F1W, False, False, False),
    "F2": ({"ang": {"Ki": 0.1}}, False, False, False),
    "F3": (F3, False, False, False),
    "F4": (F4, False, False, False),
    "F5w": ({}, True, False, False),
    "F6": ({}, False, True, False),
    "F6a": ({}, False, True, True),
    "F1+F3": (_merge(F1A, F1C, F3), False, False, False),
    "F1+F3+F5w": (_merge(F1A, F1C, F3), True, False, False),
    "F1+F3+F5w+F6": (_merge(F1A, F1C, F3), True, True, False),
    "F1w+F5w": (F1W, True, False, False),
    "F1w+F3": (_merge(F1W, F3), False, False, False),
    "F1w+F3+F5w": (_merge(F1W, F3), True, False, False),
    "F1w+F3+F5w+F6": (_merge(F1W, F3), True, True, False),
    "F1w+F3+F5w+F6a": (_merge(F1W, F3), True, True, True),
    "F1w+F4+F5w+F6": (_merge(F1W, F4), True, True, False),
    "F3+F5w": (F3, True, False, False),
    "F3+F5w+F6": (F3, True, True, False),
    "F1x": (F1X, False, False, False),
    "F1x+F3+F5w": (_merge(F1X, F3), True, False, False),
    "F1x+F3+F5w+F6": (_merge(F1X, F3), True, True, False),
    "F1x+F3+F5w+F6a": (_merge(F1X, F3), True, True, True),
}


# --------------------------------------------------------------------------- calibration

def find_flight(logs: pathlib.Path, prefix: str) -> str | None:
    hits = sorted(logs.glob(f"{prefix}*.slot1.csv"))
    return hits[0].name.split(".slot")[0] if hits else None


def sysid_plant() -> dict[str, dict[str, float]]:
    """docs/sysid_results.md rate plants K/(s(1+s/p))e^-sT, K per Nm -> per tick via mrac_to_mixer 1170."""
    return {"roll": {"k": ROLL_K / MIXER_R_P * c.RAD2DEG, "tau_m": 1.0 / ROLL_POLE, "delay": ROLL_DELAY},
            "pitch": {"k": PITCH_K / MIXER_R_P * c.RAD2DEG, "tau_m": 1.0 / PITCH_POLE, "delay": PITCH_DELAY}}


def flight_runs(stats: dict, inner: dict, outer: dict, rows: dict, mrac_tau: float, rep: int) -> list[c.Run]:
    return [c.Run(rows, c.Plant(bias=stats[f"{ax}_need"], lean=stats[f"{ax}_fb"], **inner[ax], **outer), a, rep,
                  mrac_tau=mrac_tau) for a, ax in enumerate(AXES)]


CAL_KEEP = ("sp", "pfb", "th", "ang_des", "ang_u", "rate_u", "u_ad", "vel_u")


def rep_mean(sim: dict, keys: list) -> dict:
    """Average sim_stats over the noise replicates (last element of each key)."""
    acc: dict = {}
    for j, k in enumerate(keys):
        acc.setdefault(k[:-1], []).append(j)
    return {k: {s: float(np.nanmean(v[js])) for s, v in sim.items()} for k, js in acc.items()}


def outer_loss(sim: dict, j: int, st: dict, ax: str) -> float:
    loss = (np.log(sim["sway_amp"][j] / st[f"{ax}_sway_amp"]) / 0.3) ** 2
    loss += (np.log(sim["pos_rms"][j] / st[f"{ax}_pos_rms"]) / 0.3) ** 2
    loss += ((sim["lag_ms"][j] - st[f"{ax}_lag_ms"]) / 60.0) ** 2
    if st[f"{ax}_sway_hz"] > 0.3:                    # a real sway peak, not the band floor
        loss += ((sim["sway_hz"][j] - st[f"{ax}_sway_hz"]) / 0.1) ** 2
    return float(loss)


MRAC_TAUS = (0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0)


def fit_mrac(st: dict, slots: dict, inner: dict, outer: dict, t_on: float = 10.0) -> tuple[float, dict]:
    """tau of the MRAC offload: simulate active15 (F0, its need and lean), switch u_ad on at t_on, and pick the tau
    whose u_ad over the next 10 s best matches the logged u_ad after output_injection_on (both axes, each
    normalised by its final value). Returns (tau, per axis (log t63 s, log final ticks, sim t63 s))."""
    runs = [c.Run(cand_rows("F0"), c.Plant(bias=st[f"{ax}_need"], lean=st[f"{ax}_fb"], **inner[ax], **outer), a,
                  mrac_tau=tau, mrac_t0=t_on) for tau in MRAC_TAUS for a, ax in enumerate(AXES)]
    out = c.simulate(runs, t_on + 10.0, keep=("u_ad",))
    ts = out["t"] - t_on
    rise = {ax: c.mrac_rise(slots, ax) for ax in AXES}
    sse = np.zeros(len(MRAC_TAUS))
    t63 = np.zeros((len(MRAC_TAUS), 2))
    for j, (i, a) in enumerate(itertools.product(range(len(MRAC_TAUS)), range(2))):
        tt, ua, final = rise[AXES[a]]
        u = out["u_ad"][:, j]
        sim_u = np.interp(tt, ts, u)
        sse[i] += np.mean((sim_u - ua) ** 2) / final ** 2 if np.all(np.isfinite(u)) else np.inf
        hit = np.flatnonzero((ts >= 0) & (u * np.sign(final) >= 0.632 * abs(final)))
        t63[i, a] = ts[hit[0]] if len(hit) else np.nan
    best = int(np.argmin(sse))
    info = {}
    for a, ax in enumerate(AXES):
        tt, ua, final = rise[ax]
        hit = np.flatnonzero(ua * np.sign(final) >= 0.632 * abs(final))
        info[ax] = (float(tt[hit[0]]) if len(hit) else np.nan, final, float(t63[best, a]))
    return MRAC_TAUS[best], info


def calibrate(logs: pathlib.Path, quick: bool = False) -> dict:
    stats, slots = {}, {}
    for key, (prefix, _, _) in FLIGHTS.items():
        name = find_flight(logs, prefix)
        if name is None:
            raise SystemExit(f"cascade_rank: no {prefix}* slot CSVs in {logs}")
        slots[key] = c.load_flight(logs, name)
        stats[key] = c.flight_stats(slots[key])
    inner = sysid_plant()
    ls = {ax: c.fit_rate_plant(slots["shadow14"], ax) for ax in AXES}
    hf = {ax: c.log_rate_hf(slots["shadow14"], ax) for ax in AXES}
    for ax in AXES:                       # the logged 25 Hz limit cycle keeps |E| > EMin, so gyro Ui stays near 0
        inner[ax]["gyro_lc"] = hf[ax]["w_hf"]
    dur, reps = (30.0, 1) if quick else (60.0, 3)
    grid = list(itertools.product((0.0, 0.02, 0.04, 0.07, 0.1, 0.15), (0.1, 0.2, 0.4, 0.8),
                                  (0.0, 5.0, 10.0, 15.0, 20.0)))
    if quick:
        grid = grid[::13]
    runs, keys = [], []
    for g, rep in itertools.product(grid, range(reps)):
        outer = dict(zip(("of_delay", "of_noise", "dist"), g))
        for fl in ("shadow14", "shadow4"):
            runs += flight_runs(stats[fl], inner, outer, cand_rows(FLIGHTS[fl][1]), 0.0, rep)
            keys += [(g, fl, ax, rep) for ax in AXES]
    sim = rep_mean(c.sim_stats(c.simulate(runs, dur, keep=CAL_KEEP)), keys)
    loss: dict = {}
    for (g, fl, ax), s in sim.items():
        loss[g] = loss.get(g, 0.0) + outer_loss({k: [v] for k, v in s.items()}, 0, stats[fl], ax)
    best = min(loss, key=loss.get)
    outer = dict(zip(("of_delay", "of_noise", "dist"), best))
    mrac_tau, rise = fit_mrac(stats["active15"], slots["active15"], inner, outer)

    runs, keys = [], []
    for (fl, (_, cand, mrac)), rep in itertools.product(FLIGHTS.items(), range(reps)):
        runs += flight_runs(stats[fl], inner, outer, cand_rows(cand), mrac_tau if mrac else 0.0, rep)
        keys += [(fl, ax, rep) for ax in AXES]
    val = rep_mean(c.sim_stats(c.simulate(runs, dur, seed=1, keep=CAL_KEEP)), keys)
    return {"stats": stats, "inner": inner, "ls": ls, "hf": hf, "rise": rise, "mrac_tau": mrac_tau,
            "outer": outer, "loss": loss[best], "grid_n": len(grid), "reps": reps, "val": val}


def print_calibration(cal: dict) -> None:
    st = cal["stats"]
    print("== Calibration 1: log targets, hover window, controller frame (pitch = pitchPID frame, y' = -y) ==")
    cols = ("e", "fb", "u", "rate_u", "need", "u_ad", "pos_e", "pos_rms", "sway_hz", "sway_amp", "lag_ms", "vel_u")
    table(["flight", "axis", "win s"] + list(cols),
          [[fl, ax, f"{s['dur']:.0f}"] + [f"{s[f'{ax}_{k}']:.2f}" for k in cols] for fl, s in st.items() for ax in AXES])
    print("e = Des-FB deg, fb = mean FB deg (= lean), u = angle U deg/s, rate_u = gyro U ticks, need = mixer torque"
          " ticks/motor, u_ad = need - rate_u, pos_e/pos_rms = locPID Des-FB cm, sway = rollPID.FB peak 0.2-3 Hz"
          " (amp deg), lag_ms = Des->FB phase delay 0.3-1.5 Hz, vel_u = locsPID.U cm/s^2")
    print("\n== Calibration 2: plant ==")
    rows = []
    for ax in AXES:
        i, ls, hf = cal["inner"][ax], cal["ls"][ax], cal["hf"][ax]
        rows.append([ax, f"{i['k']:.2f}", f"{i['tau_m'] * 1000:.0f}", f"{i['delay'] * 1000:.0f}",
                     f"{ls['k']:.1f} / {ls['tau_m'] * 1000:.0f} / {ls['delay'] * 1000:.0f} (r2 {ls['r2']:.2f})",
                     f"{hf['hf_hz']:.1f} Hz, {hf['w_hf']:.1f} dps"] +
                    [f"{cal['rise'][ax][0]:.2f} s / {cal['rise'][ax][2]:.2f} s / {cal['rise'][ax][1]:.1f}"])
    table(["axis", "k deg/s2/tick", "tau_m ms", "delay ms", "closed-loop LS k/tau/delay (not used)",
           "log rate limit cycle >5 Hz", "active15 u_ad t63 log / sim, final ticks"], rows)
    o = cal["outer"]
    print("inner plant = SysID (constants.py, docs/sysid_results.md) + a 25 Hz dither on the gyro feedback with the "
          "logged limit-cycle rms (column 6), which keeps |E| > EMin like the flights. Outer fit over")
    print(f" {cal['grid_n']} grid points "
          f"on shadow14 + shadow4: OF delay {o['of_delay'] * 1000:.0f} ms, OF noise {o['of_noise']:.1f} cm/s per "
          f"100 Hz sample, slow push {o['dist']:.0f} cm/s^2 rms (tau 2 s), loss {cal['loss']:.1f}. "
          f"MRAC offload tau fitted to the active15 u_ad rise (grid {MRAC_TAUS[0]}-{MRAC_TAUS[-1]} s): "
          f"{cal['mrac_tau']:.2f} s.")
    print("\n== Calibration 3: sim vs log (same rows, bias = need, lean = fb per flight) ==")
    keys = ("e", "u", "rate_u", "rate_ui", "u_ad", "pos_e", "pos_rms", "vel_u", "sway_hz", "sway_amp", "lag_ms")
    rows = []
    for fl, (_, cand, mrac) in FLIGHTS.items():
        for ax in AXES:
            v = cal["val"][(fl, ax)]
            rows.append([fl, f"{cand}{'+MRAC' if mrac else ''}", ax] +
                        [f"{v[k]:.2f} / {st[fl][f'{ax}_{k}']:.2f}" for k in keys])
    table(["flight", "rows", "axis"] + [f"{k} sim/log" for k in keys], rows)
    print(f"sim = mean of {cal['reps']} noise replicates, {30 if cal['reps'] == 1 else 60} s each, from 10 s; "
          "rate_ui = rate_u - 5 * u, the gyro integrator implied by the means (gyro Kp 5, mean rate 0).")


# --------------------------------------------------------------------------- scenarios

def cand_rows(name: str) -> dict[str, c.PidRow]:
    return _rows(**CANDIDATES[name][0])


def robust_variants(quick: bool) -> list[tuple[str, dict]]:
    v = [("nominal", {})]
    if not quick:
        v += [("k-30", {"k": 0.7}), ("k+30", {"k": 1.3}), ("lag-30", {"lag": 0.7}), ("lag+30", {"lag": 1.3})]
    return v


def scale_plant(p: c.Plant, var: dict) -> c.Plant:
    lag = var.get("lag", 1.0)
    return p.with_(k=p.k * var.get("k", 1.0), tau_m=p.tau_m * lag, delay=p.delay * lag, of_delay=p.of_delay * lag)


def base_plants(cal: dict) -> dict[str, c.Plant]:
    """S1 plant: shadow14 torque need, WP-9 fleet trim as the lean, the fitted OF and push."""
    st = cal["stats"]["shadow14"]
    return {ax: c.Plant(bias=st[f"{ax}_need"], lean=WP9_TRIM[ax], **cal["inner"][ax], **cal["outer"]) for ax in AXES}


def build_runs(cal, scen, cands, quick):
    """Runs for one scenario: candidate x controller x plant variant x scenario variant x axis.

    scen = dict(variants=[(label, {axis: dict(bias_step=..., sp=..., vff=...)})], duration)."""
    plants = base_plants(cal)
    runs, keys = [], []
    for cand, ctrl, (rv, var), (sv, spec) in itertools.product(
            cands, ("PID", "MRAC"), robust_variants(quick), scen["variants"]):
        _, trim, vff, aff = CANDIDATES[cand]
        rows = cand_rows(cand)
        for a, ax in enumerate(AXES):
            s = spec.get(ax, {})
            p = scale_plant(plants[ax], var)
            step = s.get("bias_mult")
            runs.append(c.Run(rows, p, a, sp=s.get("sp"), vff=s.get("vel") if vff else None,
                              aff=s.get("acc") if aff else None, trim_ff=WP9_TRIM[ax] if trim else 0.0,
                              mrac_tau=cal["mrac_tau"] if ctrl == "MRAC" else 0.0,
                              bias_step=(10.0, p.bias * step) if step is not None else None))
            keys.append((cand, ctrl, rv, sv, ax))
    return runs, keys


def traj_spec(points: np.ndarray, speed: float, hold: float) -> tuple[dict, float]:
    pos, vel = c.waypoint_track(points, speed, hold)
    acc = c.accel_ff(vel)
    flip = np.array([1.0, -1.0])                     # controller frame: y' = -y
    spec = {ax: {"sp": pos[:, a] * flip[a], "vel": vel[:, a] * flip[a], "acc": acc[:, a] * flip[a]}
            for a, ax in enumerate(AXES)}
    return spec, len(pos) * c.TICK


def scenarios(quick: bool) -> dict:
    sq, t_sq = traj_spec(c.square_points(1.0, 0.1), 0.2, 5.0)
    ci, t_ci = traj_spec(c.circle_points(0.5, 0.1), 0.3, 5.0)
    return {
        "S1": {"title": "S1 hover 60 s, lean = WP-9 trim, shadow14 torque need, fitted push/OF noise",
               "duration": 30.0 if quick else 60.0, "variants": [("hover", {})]},
        "S2": {"title": "S2 asymmetric load, torque bias step at 10 s (worst of the variants)",
               "duration": 25.0 if quick else 40.0,
               "variants": [("roll x2", {"roll": {"bias_mult": 2.0}}), ("roll x3", {"roll": {"bias_mult": 3.0}}),
                            ("roll x-1", {"roll": {"bias_mult": -1.0}}), ("pitch x3", {"pitch": {"bias_mult": 3.0}})]},
        "S3": {"title": "S3 dense waypoints: 1 m square 0.2 m/s and r 0.5 m circle 0.3 m/s, a point every 0.1 m",
               "duration": max(t_sq, t_ci), "variants": [("square", sq), ("circle", ci)],
               "t_end": {"square": t_sq - 5.0, "circle": t_ci - 5.0}},
    }


def pair_xy(out: dict, keys: list, sel) -> dict:
    """Stack the x and y' columns of each (cand, ctrl, rv, sv) into log-frame 2D arrays."""
    idx: dict = {}
    for j, k in enumerate(keys):
        idx.setdefault(k[:4], [None, None])[AXES.index(k[4])] = j
    res = {}
    for k4, (jx, jy) in idx.items():
        if sel(k4):
            res[k4] = {name: (out[name][:, jx], -out[name][:, jy] if name in ("p", "sp", "pfb") else out[name][:, jy])
                       for name in ("p", "sp", "th")}
    return res


def metrics(d: dict, t: np.ndarray, t0: float, t_end: float, steady_from: float) -> dict[str, float]:
    ex = d["sp"][0] - d["p"][0]
    ey = d["sp"][1] - d["p"][1]
    err = np.hypot(ex, ey)
    m = (t >= t0) & (t <= t_end)
    fs = 1.0 / (c.TICK * c.LOG_EVERY)
    ss = t >= steady_from
    lean = np.maximum(np.abs(d["th"][0]), np.abs(d["th"][1]))
    ok = bool(np.all(np.isfinite(err)) and err.max() < 300.0)
    fx, ax_ = c.osc(ex[ss], fs)
    fy, ay = c.osc(ey[ss], fs)
    return {"steady": float(np.hypot(ex[ss].mean(), ey[ss].mean())), "rms": float(np.sqrt(np.mean(err[m] ** 2))),
            "max": float(err[m].max()), "lean": float(lean[t >= 1.0].max()),
            "osc_amp": max(ax_, ay), "osc_hz": fx if ax_ >= ay else fy, "ok": ok}


def smooth_dev(d: dict, win_s: float = 1.0) -> np.ndarray:
    """2D tracking error, 1 s moving mean (settle times on the noise-free trend)."""
    dev = np.hypot(*(d["sp"][a] - d["p"][a] for a in range(2)))
    n = int(win_s / (c.TICK * c.LOG_EVERY))
    return np.convolve(np.r_[np.full(n, dev[0]), dev], np.ones(n) / n, mode="valid")[1:]


def table(head: list[str], rows: list[list[str]]) -> None:
    w = [max(len(str(x)) for x in col) for col in zip(head, *rows)]
    print("| " + " | ".join(str(h).ljust(n) for h, n in zip(head, w)) + " |")
    print("|" + "|".join("-" * (n + 2) for n in w) + "|")
    for r in rows:
        print("| " + " | ".join(str(x).ljust(n) for x, n in zip(r, w)) + " |")


def rank_scenario(cal: dict, name: str, scen: dict, cands: list[str], quick: bool) -> list[tuple]:
    runs, keys = build_runs(cal, scen, cands, quick)
    out = c.simulate(runs, scen["duration"], seed=2, keep=("p", "sp", "th"))
    t = out["t"]
    xy = pair_xy(out, keys, lambda k: True)
    res: dict = {}
    for (cand, ctrl, rv, sv), d in xy.items():
        if name == "S1":
            m = metrics(d, t, 10.0, t[-1], t[-1] - 20.0)
        elif name == "S2":
            m = metrics(d, t, 10.0, t[-1], t[-1] - 10.0)
            dev = smooth_dev(d)
            m["settle"] = c.settle_time(dev - dev[t >= t[-1] - 5.0].mean(), t, 10.0, 3.0)
        else:
            te = scen["t_end"][sv]
            m = metrics(d, t, 0.0, te, te + 2.0)
            hold = t > te
            k_end, k_pre = np.searchsorted(t, te), np.searchsorted(t, te - 0.5)
            u = np.array([d["sp"][a][k_end] - d["sp"][a][k_pre] for a in range(2)])
            u /= np.linalg.norm(u)
            past = sum((d["p"][a] - d["sp"][a][k_end]) * u[a] for a in range(2))   # beyond the end point
            m["over"] = float(max(0.0, past[hold].max()))
            dev = smooth_dev(d)
            m["settle"] = c.settle_time(dev - dev[t >= t[-1] - 1.0].mean(), t, te, 3.0)
        res.setdefault((cand, ctrl), {}).setdefault(rv, {})[sv] = m
    rows = []
    for (cand, ctrl), by_rv in res.items():
        nom = list(by_rv["nominal"].values())
        every = [m for v in by_rv.values() for m in v.values()]
        s = {k: max(m[k] for m in nom) for k in ("steady", "rms", "max", "lean")}
        for k in ("settle", "over"):
            if k in nom[0]:
                s[k] = float(np.nanmax([m[k] for m in nom])) if np.isfinite([m[k] for m in nom]).any() else np.nan
        w_osc = max(nom, key=lambda m: m["osc_amp"])
        s["osc"] = f"{w_osc['osc_amp']:.1f} @ {w_osc['osc_hz']:.2f}"
        s["r_rms"] = max(m["rms"] for m in every)
        s["r_osc"] = max(m["osc_amp"] for m in every)
        s["ok"] = all(m["ok"] for m in every)
        rows.append((s["r_rms"] if s["ok"] else np.inf, cand, ctrl, s))
    rows.sort(key=lambda r: r[0])
    return rows


def print_ranking(name: str, scen: dict, rows: list[tuple]) -> None:
    print(f"\n== Ranking {scen['title']} ==")
    extra = {"S2": ["settle"], "S3": ["over", "settle"]}.get(name, [])
    head = ["#", "candidate", "ctrl", "steady cm", "rms cm", "max cm"] + [
        {"settle": "settle s", "over": "overshoot cm"}[k] for k in extra] + [
        "peak lean deg", "osc cm @ Hz", "robust worst rms", "robust worst osc", "stable +-30%"]
    out = []
    for i, (_, cand, ctrl, s) in enumerate(rows, 1):
        out.append([str(i), cand, ctrl] + [f"{s[k]:.1f}" if np.isfinite(s[k]) else "n/s"
                                           for k in ["steady", "rms", "max"] + extra] +
                   [f"{s['lean']:.1f}", s["osc"], f"{s['r_rms']:.1f}", f"{s['r_osc']:.1f}",
                    "yes" if s["ok"] else "NO"])
    table(head, out)
    print("Columns are the worst over the scenario variants (nominal plant); rank key = robust worst rms (all "
          "variants x nominal and +-30% k, lag); osc = position-error peak 0.2-3 Hz over the steady part; "
          "settle = 1 s mean error within 3 cm of its final value (n/s = not by the end of the run).")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--logs", required=True, type=pathlib.Path)
    ap.add_argument("--quick", action="store_true", help="short runs, nominal plant only, 6 candidates (smoke test)")
    args = ap.parse_args(argv)
    t0 = time.time()
    cal = calibrate(args.logs, quick=args.quick)
    print_calibration(cal)
    print(f"(calibration {time.time() - t0:.0f} s)")
    cands = ["F0", "F1w", "F2", "F3", "F1w+F3+F5w+F6", "F1x+F3+F5w+F6"] if args.quick else list(CANDIDATES)
    for name, scen in scenarios(args.quick).items():
        t1 = time.time()
        print_ranking(name, scen, rank_scenario(cal, name, scen, cands, args.quick))
        print(f"({name} {time.time() - t1:.0f} s)")
    print(f"\ntotal {time.time() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
