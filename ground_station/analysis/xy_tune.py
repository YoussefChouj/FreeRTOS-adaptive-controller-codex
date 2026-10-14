"""xy position-loop tuning from flight logs (2026-10-14).

1. ident: per log and axis, fit the velocity plant from the logged accel command to the logged velocity
   (slot1 50 Hz `mrac_state.<x|y>_vel`: u_nom + u_ad m/s^2 -> x m/s):
       a = first-order lag (tau) of the command delayed d samples,  v' = k*a - drag*v + bias.
   Fit = least squares on the velocity simulated 1 s ahead from a start every 0.2 s inside airborne spans
   (the velocity loop is closed in flight, so this is closed-loop data; the fit is an output-error fit).
2. sim: the firmware cascade at 100 Hz (API/pid.c ComputePID AW_LEGACY: locx -> clamp g_vxy_des_max -> + traj FF
   -> locxs -> UMax and tilt cap) on many fitted plants at once (numpy columns). Any outer controller with
   `step(des, pos, vel) -> accel cm/s^2` plugs in (ground_station/analysis/xy_ctrl.py: ADRC, LQR, MPC).
3. replay: a step flight's logged locxPID.Des and traj FF through the sim; sim vs logged position and the
   flight_debrief step_settle rows (overshoot, settle, swings, period).
4. tune: CMA-ES (ground_station/livetune/cmaes.py) over locx Kp/Ki/Kd + locxs Kp/Ki/Kd against every fitted plant,
   each also with +20 ms delay and -20 % gain, on a 50 cm step out and back with measured velocity noise.

    python -m ground_station.analysis.xy_tune --ident r6_f4_sysid r6_f4b_sysid_p5300 r6_f4c_sysid_p4000 \
        --replay 20261013-213613 --out logs/xy_tune/<stamp>

Cost weights, bounds and robustness margins are PROPOSED; plant numbers are fitted, see plants.json.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from ground_station.analysis import log_corpus as lc

DT_ID = 0.02          # slot1 rate (50 Hz)
DT = 0.01             # firmware position loops: POS_LOOP_DIV 2 at 200 Hz
H_ID = 50             # ident horizon: 1 s simulated ahead
WARM = 25             # lag state warm-up before each start (0.5 s)
START_EVERY = 10      # one start every 0.2 s
D_MAX = 20            # delays tried: 0..400 ms
EDGE_S = 1.0          # skip the first/last second of each airborne span (takeoff, landing)
G_CMS2 = 981.0
# API/pid.c PID_ROW: Kp, Ki, Kd, UMax, UpMax, UiMax, UdMax, SumEMax, EMin
LOCX_ROW = (0.8, 0.0013, 4.0, 300.0, 300.0, 5.0, 50.0, 3850.0, 10.0)
LOCXS_ROW = (3.0, 0.008, 6.0, 600.0, 600.0, 100.0, 100.0, 12500.0, 10.0)
VXY_MAX = 120.0       # g_vxy_des_max cm/s, limit mode 0
TILT_DEG = 15.0       # gs_max_pitch/roll_deg, limit mode 0 (accel_to_lean_angles cap)
SETTLE_BAND_CM = 5.0  # flight_debrief SETTLE_BAND_CM
AXES = {"x": ("mrac_state.x_vel", "Ctrler.locxPID", "Ctrler.locxsPID"),
        "y": ("mrac_state.y_vel", "Ctrler.locyPID", "Ctrler.locysPID")}
# tuned knobs: (row, field index, lower, upper)
KNOBS = (("locx", 0, 0.3, 2.5), ("locx", 1, 0.0, 0.01), ("locx", 2, 0.0, 10.0),
         ("locxs", 0, 1.0, 6.0), ("locxs", 1, 0.0, 0.05), ("locxs", 2, 0.0, 15.0))
KNOB_NAMES = ("locx.Kp", "locx.Ki", "locx.Kd", "locxs.Kp", "locxs.Ki", "locxs.Kd")


@dataclass
class Plant:
    log: str
    axis: str
    k: float          # accel gain (m/s^2 per m/s^2 commanded)
    tau: float        # s, command -> accel lag (attitude loop)
    drag: float       # 1/s
    bias: float       # m/s^2 (trim, wind)
    delay: int        # samples at 50 Hz
    rmse: float       # m/s, 1 s simulated-ahead velocity
    n_starts: int
    sigma_v: float    # cm/s, velocity FB noise (v minus its 5-sample moving average)


# ---------------------------------------------------------------- ident

def _airborne(series: lc.Series, t: np.ndarray) -> np.ndarray:
    if "flight_phase" in series:
        return lc.airborne(series, t)
    arm = lc.hold(series, "DroneStatus.ARM_Status", t, 0.0) > 0.5
    return arm & (lc.hold(series, "Ctrler.Z_posPID.FB", t, 0.0) > 0.25)


def ident_data(series: lc.Series, axis: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(u m/s^2, v m/s, starts) on the 50 Hz grid; starts are indices with warm-up and horizon airborne."""
    base = AXES[axis][0]
    t, d = lc.grid(series, [base + ".u_nom", base + ".u_ad", base + ".x"], DT_ID)
    u = d[base + ".u_nom"] + np.nan_to_num(d[base + ".u_ad"])
    v = d[base + ".x"]
    air = _airborne(series, t)
    starts = []
    for a, b in lc.spans(air, t):
        i0 = int(np.searchsorted(t, a + EDGE_S)) + WARM + D_MAX
        i1 = int(np.searchsorted(t, b - EDGE_S)) - H_ID - 1
        starts += list(range(i0, max(i0, i1), START_EVERY))
    starts = np.array([s for s in starts if np.isfinite(u[s - WARM - D_MAX:s + H_ID + 1]).all()
                       and np.isfinite(v[s:s + H_ID + 1]).all()], int)
    return u, v, starts


def predict(p, u: np.ndarray, v: np.ndarray, starts: np.ndarray, d: int, dt: float = DT_ID, h: int = H_ID):
    """(S, h) velocity simulated from v[s] with the measured command, for v[s+1..s+h]."""
    k, tau, drag, bias = p
    al = math.exp(-dt / tau)
    a = u[starts - d - WARM]
    for j in range(1, WARM):
        a = al * a + (1 - al) * u[starts - d - WARM + j]
    vv = v[starts].astype(float)
    out = np.empty((len(starts), h))
    for j in range(h):
        a = al * a + (1 - al) * u[starts + j - d]
        vv = vv + dt * (k * a - drag * vv + bias)
        out[:, j] = vv
    return out


def fit_plant(u: np.ndarray, v: np.ndarray, starts: np.ndarray, log: str = "", axis: str = "") -> Plant:
    from scipy.optimize import least_squares
    target = np.stack([v[starts + 1 + j] for j in range(H_ID)], 1)
    best = None
    for d in range(D_MAX + 1):
        r = least_squares(lambda p: (predict(p, u, v, starts, d) - target).ravel(), x0=[1.0, 0.15, 0.3, 0.0],
                          bounds=([0.1, 0.02, 0.0, -2.0], [4.0, 1.5, 5.0, 2.0]))
        if best is None or r.cost < best[1].cost:
            best = (d, r)
    d, r = best
    rmse = float(np.sqrt(np.mean(r.fun ** 2)))
    vm = np.convolve(v, np.ones(5) / 5, mode="same")
    ok = np.isfinite(v) & np.isfinite(vm)
    seg = np.zeros(len(v), bool)
    for s in starts:
        seg[s:s + START_EVERY] = True
    sigma = float(np.std((v - vm)[ok & seg]) * 100.0)
    k, tau, drag, bias = (float(x) for x in r.x)
    return Plant(log, axis, k, tau, drag, bias, d, rmse, len(starts), sigma)


def ident(names: list[str]) -> list[Plant]:
    refs = {r.name.split("/", 1)[-1]: r for r in lc.find_logs()}
    out = []
    for n in names:
        series = lc.load(refs[n])
        for axis in AXES:
            u, v, starts = ident_data(series, axis)
            if len(starts) >= 20:
                out.append(fit_plant(u, v, starts, n, axis))
    return out


# ---------------------------------------------------------------- firmware cascade

class FwPid:
    """API/pid.c ComputePID, AW_LEGACY branch, on B columns. D on the error per tick (not divided by dt)."""

    def __init__(self, row, b: int):
        r = np.broadcast_to(np.asarray(row, float).reshape(9, -1), (9, b)).copy()
        self.kp, self.ki, self.kd, self.umax, self.upmax, self.uimax, self.udmax, self.summax, self.emin = r
        self.sum = np.zeros(b)
        self.pre = np.zeros(b)
        self.u = np.zeros(b)

    def step(self, e: np.ndarray) -> np.ndarray:
        # (U <= UMax && E > 0) || (U >= -UMax && E < 0) holds whenever E != 0 (U is clamped), so: |E| < EMin
        self.sum = np.clip(self.sum + np.where(np.abs(e) < self.emin, e, 0.0), -self.summax, self.summax)
        ui = np.clip(self.ki * self.sum, -self.uimax, self.uimax)
        up = np.clip(self.kp * e, -self.upmax, self.upmax)
        ud = np.clip(self.kd * (e - self.pre), -self.udmax, self.udmax)
        self.u = np.clip(up + ui + ud, -self.umax, self.umax)
        self.pre = e
        return self.u


class FwCascade:
    """locx (cm -> cm/s, clamp vxy) + traj FF -> locxs (cm/s -> cm/s^2): StabilizerTask.c Pos_Compute.
    ff_shift s: the position target moves back by ff_shift x vff (~ the reference ff_shift s ago), so the FF leads the
    position loop by the plant lag; 0 = the firmware today (PROPOSED change, see report)."""

    def __init__(self, locx_row, locxs_row, b: int, vxy: float = VXY_MAX, ff_shift: float = 0.0):
        self.pos, self.vel, self.vxy, self.ff_shift = FwPid(locx_row, b), FwPid(locxs_row, b), vxy, ff_shift

    def prime(self, e_pos: np.ndarray) -> None:
        self.pos.pre = np.asarray(e_pos, float) + 0 * self.pos.pre

    def step(self, des, pos, vel, vff=0.0) -> np.ndarray:
        vd = np.clip(self.pos.step(des - self.ff_shift * vff - pos), -self.vxy, self.vxy) + vff
        return self.vel.step(vd - vel)


def plant_arrays(plants: list[Plant], b_rep: int = 1, delay_add: float = 0.0, k_scale: float = 1.0) -> dict:
    """Plants as (B,) columns at DT (each plant repeated b_rep times, plant-major)."""
    col = lambda f: np.repeat(np.array([f(p) for p in plants], float), b_rep)
    return {"k": col(lambda p: p.k * k_scale), "tau": col(lambda p: p.tau), "drag": col(lambda p: p.drag),
            "bias": col(lambda p: p.bias), "sigma_v": col(lambda p: p.sigma_v),
            "delay": np.rint(col(lambda p: p.delay * DT_ID / DT + delay_add / DT)).astype(int)}


def simulate(ctrl, pa: dict, des: np.ndarray, vff: np.ndarray | None = None, noise: np.ndarray | None = None,
             pos0=0.0, v0=0.0, tilt_deg: float = TILT_DEG) -> dict:
    """ctrl.step(des, pos, vel_meas, vff) -> accel cm/s^2 on B columns; plant pa from plant_arrays; des (T,) or
    (T, B) cm. Returns pos cm, vel cm/s, acc (commanded, after the tilt cap) cm/s^2, sat (cap active), all (T, B)."""
    b = len(pa["k"])
    n = len(des)
    des = np.broadcast_to(np.asarray(des, float).reshape(n, -1), (n, b))
    vff = np.zeros((n, b)) if vff is None else np.broadcast_to(np.asarray(vff, float).reshape(n, -1), (n, b))
    noise = np.zeros((n, b)) if noise is None else noise
    amax = G_CMS2 * math.tan(math.radians(tilt_deg))
    al = np.exp(-DT / pa["tau"])
    lmax = int(pa["delay"].max()) + 1
    buf = np.zeros((lmax, b))
    cols = np.arange(b)
    pos = np.broadcast_to(np.asarray(pos0, float), (b,)).copy()
    v = np.broadcast_to(np.asarray(v0, float), (b,)).copy() / 100.0      # m/s
    a = np.zeros(b)
    out = {key: np.empty((n, b)) for key in ("pos", "vel", "acc")}
    sat = np.empty((n, b), bool)
    for i in range(n):
        acmd = ctrl.step(des[i], pos, v * 100.0 + noise[i], vff[i])
        acc = np.clip(acmd, -amax, amax)
        sat[i] = np.abs(acmd) > amax
        buf[i % lmax] = acc / 100.0
        a = al * a + (1 - al) * buf[(i - pa["delay"]) % lmax, cols]
        v = v + DT * (pa["k"] * a - pa["drag"] * v + pa["bias"])
        pos = pos + v * 100.0 * DT
        out["pos"][i], out["vel"][i], out["acc"][i] = pos, v * 100.0, acc
    out["sat"] = sat
    return out


# ---------------------------------------------------------------- scoring

def ramp_scenario(moves=(50.0, -50.0, -50.0, 50.0), v=30.0, a=70.0, pre=8.0, hold=3.5, tail=6.0):
    """The 10-13 step flights' reference: trapezoid ramps (30 cm/s, 70 cm/s^2: 50 cm in 2.1 s, as flown) with the
    traj FF = the ramp velocity, 3.5 s holds. pre lets the integrators take the plant bias first.
    Returns t, des cm, vff cm/s, holds [(t_from, t_to, level, move)]."""
    vel, holds, level, now = [np.zeros(int(round(pre / DT)))], [], 0.0, pre
    for i, d in enumerate(moves):
        tr = abs(d) / v + v / a
        tt = np.arange(0.0, tr, DT)
        vel.append(np.sign(d) * np.minimum.reduce([a * tt, np.full_like(tt, v), a * (tr - tt)]))
        level += d
        now += len(tt) * DT
        h = tail if i == len(moves) - 1 else hold
        vel.append(np.zeros(int(round(h / DT))))
        holds.append((now, now + h, level, d))
        now += h
    vff = np.concatenate(vel)
    des = np.cumsum(vff) * DT
    for k, (a0, b0, lv, d) in enumerate(holds):      # remove the summation drift: holds sit exactly on the level
        des[int(round(a0 / DT)):int(round(b0 / DT))] = lv
    return np.arange(len(vff)) * DT, des, vff, holds


def step_metrics(t: np.ndarray, pos: np.ndarray, holds) -> dict:
    """Per column: mean over the moves of overshoot cm, settle s (never = hold + 2 s), swings."""
    ov, st, sw = [], [], []
    for a, b, level, move in holds:
        m = (t >= a) & (t < b)
        err = pos[m] - level
        tt = t[m] - a
        ov.append(np.maximum(0.0, np.max(np.sign(move) * err, 0)))
        out = np.abs(err) > SETTLE_BAND_CM
        last = np.where(out.any(0), (len(tt) - 1) - np.argmax(out[::-1], 0), -1)
        st.append(np.where(last == len(tt) - 1, (b - a) + 2.0, np.where(last < 0, 0.0, tt[np.maximum(last, 0)])))
        big = np.abs(err) > SETTLE_BAND_CM / 2
        sgn = np.where(big, np.sign(err), 0.0)
        sw.append(_sign_flips(sgn))
    return {"overshoot_cm": np.mean(ov, 0), "settle_s": np.mean(st, 0), "swings": np.mean(sw, 0)}


def _sign_flips(sgn: np.ndarray) -> np.ndarray:
    flips = np.zeros(sgn.shape[1])
    last = np.zeros(sgn.shape[1])
    for row in sgn:
        nz = row != 0
        flips += nz & (last != 0) & (row != last)
        last = np.where(nz, row, last)
    return flips


def jitter_deg(acc: np.ndarray) -> np.ndarray:
    """Lean jitter: std of the accel command minus its 10-tick moving average, as degrees."""
    k = np.ones(10) / 10
    sm = np.apply_along_axis(lambda c: np.convolve(c, k, mode="same"), 0, acc)
    return np.degrees(np.std(acc - sm, 0) / G_CMS2)


def score(ctrl_factory: Callable[[int], object], plants: list[Plant], seed: int = 0) -> dict:
    """Every plant plus a +20 ms delay and a -20 % gain copy (PROPOSED margins), on the ramp scenario with noise."""
    t, des, vff, holds = ramp_scenario()
    pa = {key: np.concatenate([plant_arrays(plants)[key], plant_arrays(plants, delay_add=0.02)[key],
                               plant_arrays(plants, k_scale=0.8)[key]]) for key in plant_arrays(plants)}
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal((len(t), len(pa["k"]))) * pa["sigma_v"]
    r = simulate(ctrl_factory(len(pa["k"])), pa, des, vff=vff, noise=noise)
    m = step_metrics(t, r["pos"], holds)
    m["jitter_deg"] = jitter_deg(r["acc"])
    m["sat_frac"] = r["sat"].mean(0)
    return m


def cost(m: dict) -> float:
    """PROPOSED weights: settle s + 0.2 x overshoot cm + lean jitter deg + 2 x tilt-cap fraction; mean + 0.5 worst."""
    j = m["settle_s"] + 0.2 * m["overshoot_cm"] + m["jitter_deg"] + 2.0 * m["sat_frac"]
    return float(np.mean(j) + 0.5 * np.max(j))


def summary(m: dict, n: int) -> dict:
    """Nominal plants only (the first n columns): mean and worst."""
    return {k: {"mean": round(float(np.mean(v[:n])), 2), "worst": round(float(np.max(v[:n])), 2)} for k, v in m.items()}


# ---------------------------------------------------------------- tune

def rows_from_x(x) -> tuple[tuple, tuple]:
    locx, locxs = list(LOCX_ROW), list(LOCXS_ROW)
    for (row, i, _lo, _hi), val in zip(KNOBS, x):
        (locx if row == "locx" else locxs)[i] = float(val)
    return tuple(locx), tuple(locxs)


FF_KNOB = ("ff", 0, 0.0, 0.6)    # ff_shift s: needs the firmware change (PROPOSED)


def cascade_from_x(x) -> Callable[[int], FwCascade]:
    rx, rxs = rows_from_x(x[:len(KNOBS)])
    sh = float(x[len(KNOBS)]) if len(x) > len(KNOBS) else 0.0
    return lambda b: FwCascade(rx, rxs, b, ff_shift=sh)


def cmaes_box(f: Callable[[np.ndarray], float], x0, lo, hi, gens: int, seed: int = 0):
    """CMA-ES (livetune.cmaes) on the box lo..hi, normalized to 0..1. Returns best x, best cost, trace."""
    from ground_station.livetune.cmaes import CMAES
    x0, lo, hi = (np.asarray(v, float) for v in (x0, lo, hi))
    es = CMAES((x0 - lo) / (hi - lo), 0.2, lower=np.zeros(len(lo)), upper=np.ones(len(lo)), seed=seed)
    best, best_f = x0, f(x0)
    trace = [best_f]
    for _ in range(gens):
        xs = es.ask()
        fs = [f(lo + np.clip(z, 0, 1) * (hi - lo)) for z in xs]
        es.tell(xs, np.array(fs))
        i = int(np.argmin(fs))
        if fs[i] < best_f:
            best_f, best = fs[i], lo + np.clip(xs[i], 0, 1) * (hi - lo)
        trace.append(best_f)
    return best, best_f, trace


def tune(plants: list[Plant], gens: int = 60, seed: int = 0, shift: bool = False):
    """CMA-ES over the PID knobs (and ff_shift when shift). Returns best x, best cost, trace."""
    knobs = KNOBS + ((FF_KNOB,) if shift else ())
    x0 = [(LOCX_ROW if k[0] == "locx" else LOCXS_ROW)[k[1]] for k in KNOBS] + ([0.2] if shift else [])
    return cmaes_box(lambda x: cost(score(cascade_from_x(x), plants, seed)), x0,
                     [k[2] for k in knobs], [k[3] for k in knobs], gens, seed)


def tune_ctrl(kind: str, plants: list[Plant], gens: int = 40, seed: int = 0):
    """CMA-ES over an xy_ctrl controller's log10 knobs on the nominal (mean) model; scored on every plant."""
    from ground_station.analysis import xy_ctrl as xc
    m = xc.nominal(plants)
    return cmaes_box(lambda x: cost(score(xc.factory(kind, m, x), plants, seed)), xc.X0[kind],
                     [k[1] for k in xc.KNOBS[kind]], [k[2] for k in xc.KNOBS[kind]], gens, seed)


# ---------------------------------------------------------------- replay

def replay_data(series: lc.Series, axis: str) -> dict | None:
    """Longest airborne span of a step flight on the 100 Hz grid: des, pos cm, vel cm/s, vff cm/s."""
    """Stream logs without the cascade keys (r6/r7: locxPID Des/FB + mrac_state x_vel x/r in m/s): vel = x_vel.x,
    vff = x_vel.r minus locx.U recomputed with the current LOCX_ROW (exact only if the flight flew LOCX_ROW)."""
    mrac_b, pos_b, vel_b = AXES[axis]
    keys = [pos_b + ".Des", pos_b + ".FB", pos_b + ".U", vel_b + ".Des", vel_b + ".FB"]
    alt = [pos_b + ".Des", pos_b + ".FB", mrac_b + ".x", mrac_b + ".r"]
    full = all(k in series for k in keys)
    if not full and any(k not in series for k in alt):
        return None
    t, d = lc.grid(series, keys if full else alt, DT)
    sp = lc.spans(_airborne(series, t), t)
    if not sp:
        return None
    a, b = max(sp, key=lambda s: s[1] - s[0])
    m = (t >= a + EDGE_S) & (t <= b - EDGE_S)
    des, pos = d[pos_b + ".Des"][m], d[pos_b + ".FB"][m]
    if full:
        vel, vff = d[vel_b + ".FB"][m], (d[vel_b + ".Des"] - np.clip(d[pos_b + ".U"], -VXY_MAX, VXY_MAX))[m]
    else:
        pid = FwPid(LOCX_ROW, 1)
        u_pos = np.array([pid.step(np.array([e]))[0] for e in np.nan_to_num(des - pos)])
        vel, vff = d[mrac_b + ".x"][m] * 100.0, d[mrac_b + ".r"][m] * 100.0 - np.clip(u_pos, -VXY_MAX, VXY_MAX)
    ok = np.isfinite(des) & np.isfinite(pos) & np.isfinite(vel) & np.isfinite(vff)
    return {"t": t[m][ok] - t[m][ok][0], "des": des[ok], "pos": pos[ok], "vel": vel[ok], "vff": vff[ok],
            "t0": float(t[m][0]), "source": "cascade" if full else "mrac_state"}


def replay(rd: dict, plants: list[Plant], ctrl_factory: Callable[[int], object]) -> dict:
    """Simulated positions (T, plants) for the logged des + FF, from the logged start state."""
    pa = plant_arrays(plants)
    ctrl = ctrl_factory(len(plants))
    if hasattr(ctrl, "prime"):
        ctrl.prime(np.full(len(plants), rd["des"][0] - rd["pos"][0]))
    return simulate(ctrl, pa, rd["des"], vff=rd["vff"], pos0=rd["pos"][0], v0=rd["vel"][0])


def settle_rows(t: np.ndarray, des: np.ndarray, pos: np.ndarray) -> list[dict]:
    from ground_station.analysis.flight_debrief import step_settle
    return [r for r in step_settle({"Ctrler.locxPID.Des": (t, des), "Ctrler.locxPID.FB": (t, pos)}) if r["axis"] == "x"]


def rows_summary(rows: list[dict]) -> dict:
    f = lambda k: [r[k] for r in rows if r[k] is not None]
    med = lambda xs: round(float(np.median(xs)), 2) if xs else None
    return {"moves": len(rows), "overshoot_cm": med(f("overshoot_cm")), "settle_s": med(f("settle_s")),
            "never_settled": sum(r["settle_s"] is None for r in rows), "swings": med(f("swings")),
            "period_s": med(f("period_s"))}


# ---------------------------------------------------------------- report

def load_plants(path: str | Path) -> list[Plant]:
    return [Plant(**d) for d in json.loads(Path(path).read_text())]


def replay_table(refs_pre: list[str], plants: list[Plant], ctrls: dict) -> list[dict]:
    """Per replay log and axis: logged step_settle summary and, per controller, the per-plant sim summaries."""
    rows, refs = [], {r.name: r for r in lc.find_logs()}
    for pre in refs_pre:
        ref = next(r for n, r in refs.items() if n.startswith(pre))
        series = lc.load(ref)
        for axis in AXES:
            rd, pl = replay_data(series, axis), [p for p in plants if p.axis == axis]
            if rd is None or not pl:
                continue
            row = {"log": ref.name, "axis": axis, "span_s": round(float(rd["t"][-1]), 1),
                   "logged": rows_summary(settle_rows(rd["t"], rd["des"], rd["pos"]))}
            for name, fac in ctrls.items():
                sim = replay(rd, pl, fac)["pos"]
                row[name] = [rows_summary(settle_rows(rd["t"], rd["des"], sim[:, j])) for j in range(len(pl))]
                row[name + "_pos_rms_vs_log_cm"] = [round(float(np.sqrt(np.mean((sim[:, j] - rd["pos"]) ** 2))), 1)
                                                    for j in range(len(pl))]
            rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split(chr(10) * 2)[0])
    ap.add_argument("--ident", nargs="*", default=[], help="stream logs (logs/<name>.slot*.csv) with mrac_state")
    ap.add_argument("--plants", help="plants.json from an earlier run instead of --ident")
    ap.add_argument("--replay", nargs="*", default=[], help="session dir name prefixes (step flights)")
    ap.add_argument("--gens", type=int, default=60)
    ap.add_argument("--gains", nargs="*", default=[],
                    help="result.json files from earlier runs: reuse their tuned PID / xy_ctrl knobs, no CMA-ES")
    ap.add_argument("--ctrl", nargs="*", default=[], choices=("adrc", "lqr", "mpc"),
                    help="also tune (or reuse from --gains) and replay these xy_ctrl controllers")
    ap.add_argument("--ctrl-gens", type=int, default=40)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    plants = load_plants(a.plants) if a.plants else ident(a.ident)
    (out / "plants.json").write_text(json.dumps([asdict(p) for p in plants], indent=1))
    x_cur = np.array(LOCX_ROW[:3] + LOCXS_ROW[:3])
    prev = [json.loads(Path(f).read_text()) for f in a.gains]
    g = next((r["gains"] for r in prev if "tuned" in r.get("gains", {})), None)
    if g:
        (x_g, tr_g), (x_s, tr_s) = ((np.array(list(g[n].values())), []) for n in ("tuned", "tuned_shift"))
    else:
        x_g, _, tr_g = tune(plants, a.gens)
        x_s, _, tr_s = tune(plants, a.gens, shift=True)
    ctrls = {"current": cascade_from_x(x_cur), "tuned": cascade_from_x(x_g), "tuned_shift": cascade_from_x(x_s)}
    from ground_station.analysis import xy_ctrl as xc
    m_nom, xy = xc.nominal(plants), {}
    for kind in a.ctrl:
        old = next((r["xy_ctrl"][kind] for r in prev if kind in r.get("xy_ctrl", {})), None)
        lx, tr = (np.array(old["log10"]), []) if old else tune_ctrl(kind, plants, a.ctrl_gens)[::2]
        xy[kind] = {"nominal": m_nom, "log10": np.round(lx, 4).tolist(), "trace": tr,
                    "knobs": {k[0]: round(10.0 ** float(v), 5) for k, v in zip(xc.KNOBS[kind], lx)}}
        ctrls[kind] = xc.factory(kind, m_nom, lx)
    res = {"plants": [asdict(p) for p in plants], "scenario": "ramp_scenario() defaults",
           "gains": {"current": dict(zip(KNOB_NAMES, x_cur.tolist())),
                     "tuned": dict(zip(KNOB_NAMES, np.round(x_g, 4).tolist())),
                     "tuned_shift": dict(zip(KNOB_NAMES + ("ff_shift_s",), np.round(x_s, 4).tolist()))},
           "cost": {n: round(cost(score(f, plants)), 3) for n, f in ctrls.items()},
           "trace": {"tuned": tr_g, "tuned_shift": tr_s},
           "sim": {n: summary(score(f, plants), len(plants)) for n, f in ctrls.items()},
           "xy_ctrl": xy, "replay": replay_table(a.replay, plants, ctrls)}
    (out / "result.json").write_text(json.dumps(res, indent=1))
    for p in plants:
        print(f"plant {p.log} {p.axis}: k {p.k:.2f} tau {p.tau:.3f} s drag {p.drag:.2f} bias {p.bias:+.2f} "
              f"delay {p.delay * DT_ID * 1000:.0f} ms rmse {p.rmse:.3f} m/s starts {p.n_starts} sigma_v {p.sigma_v:.1f} cm/s")
    for n in ctrls:
        print(n, res["gains"][n] if n in res["gains"] else xy[n]["knobs"], "cost", res["cost"][n])
        print("   sim", {k: v["mean"] for k, v in res["sim"][n].items()})
    for r in res["replay"]:
        print(f"replay {r['log'][:15]} {r['axis']}: logged ov {r['logged']['overshoot_cm']} st {r['logged']['settle_s']} "
              f"never {r['logged']['never_settled']}")
        for n in ctrls:
            print(f"   {n:12s} ov {[s['overshoot_cm'] for s in r[n]]} st {[s['settle_s'] for s in r[n]]} "
                  f"rms {r[n + '_pos_rms_vs_log_cm']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
