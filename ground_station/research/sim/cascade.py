"""Lateral cascade sim of the f17 position hold, calibrated on the vofa logs (WP-10).

One axis per run, at the firmware rates (TASK/StabilizerTask.c, yaw 0):
  locxPID (position, cm) -> clamp +-120 -> locxsPID (velocity, cm/s)      100 Hz, cnt_loc >= 2 (~926, ~1346)
  -> accel_to_lean_angles: Des = clamp(fast_atan(U/g) deg, +-15)          200 Hz (~1376-1389, ~1437)
  -> rollPID (angle, deg) -> gyroxPID (rate, deg/s) -> torque ticks       200 Hz (~1055, ~1088, ~1404)
Frames: x <-> roll, rollPID.Des = +atan(locxsPID.U/g), rollPID.FB = imu_rol (~672);
y <-> pitch, pitchPID.Des = -atan(locysPID.U/g), pitchPID.FB = -imu_pit, mixer pitch = -gyroyPID.U.
ComputePID is odd in (Des, FB, state), so the pitch axis runs as the roll axis on y' = -y; callers flip
y-axis positions back to the log frame. Every array holds one column per run, so a whole ranking is
one batched numpy loop.

Plant per axis: torque ticks -> transport delay -> first-order motor lag -> rate = k * (torque - bias)
integrated twice to angle; lateral accel = g * tan(angle - lean). `bias` is the torque the controllers
must carry in hover, `lean` the controller-frame angle that gives zero lateral accel (the WP-9 trim).
OF: the velocity feedback is the true velocity delayed and with white noise; the position feedback
integrates that measured velocity (earth_x_ture), so the measured and true positions drift apart.
MRAC is not mrac.c: it is a first-order offload, du_ad/dt = gyroxPID.U / tau_mrac, which moves the
standing rate-loop output into u_ad (fitted to active15 by cascade_rank).
"""
from __future__ import annotations

import dataclasses
import pathlib
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import signal

F32 = np.float32
G = 980.665              # cm/s^2, GRAVITY_MSS*100 in accel_to_lean_angles
RAD2DEG = 57.29578
TICK = 0.005             # StabilizerTask period, 200 Hz (CalTrim_Init "10 s @ 200 Hz", ~327)
LOC_DIV = 2              # position/velocity loops every 2nd tick (cnt_loc >= 2)
SUB = 5                  # plant substeps per tick (1 ms)
VEL_SP_MAX = 120.0       # cm/s, locxsPID.Des clamp
LEAN_MAX = 15.0          # deg, gs_max_roll_deg / gs_max_pitch_deg in the f17 logs
LOG_EVERY = 5            # sim outputs at 40 Hz (5-tick means)


@dataclass(frozen=True)
class PidRow:
    """One API/pid.c PID_ROW, same column order."""
    Kp: float
    Ki: float
    Kd: float
    UMax: float
    UpMax: float
    UiMax: float
    UdMax: float
    SumEMax: float
    EMin: float

    def with_(self, **kw) -> PidRow:
        return dataclasses.replace(self, **kw)


ROW_FIELDS = tuple(f.name for f in dataclasses.fields(PidRow))

# API/pid.c on 3ae4a23 (lines ~19-33), roll axis; the pitch rows are identical.
F0_ROWS = {
    "pos": PidRow(0.8, 0.01, 4.0, 300, 300, 20, 50, 200, 30),   # locxPID
    "vel": PidRow(3.0, 0.0, 6.0, 600, 600, 100, 100, 200, 10),  # locxsPID
    "ang": PidRow(3.0, 0.02, 8, 200, 200, 10, 10, 120, 3),      # rollPID
    "rate": PidRow(5, 0.01, 10, 300, 300, 20, 100, 1000, 2),    # gyroxPID
}


class Pid:
    """ComputePID, AW_LEGACY branch (API/pid.c ~160-188), on float32 arrays with one column per run.

    The non-finite guards of ComputePID are left out: the sim never feeds NaN or |x| > 1e12.
    `hold` (bool per run) freezes SumE, as an "integrate only while flying" gate would on the ground.
    """

    def __init__(self, rows: list[PidRow]):
        p = np.array([[getattr(r, f) for f in ROW_FIELDS] for r in rows], dtype=F32).T
        self.Kp, self.Ki, self.Kd, self.UMax, self.UpMax, self.UiMax, self.UdMax, self.SumEMax, self.EMin = p
        z = np.zeros(len(rows), dtype=F32)
        self.E, self.PreE, self.SumE, self.U, self.Up, self.Ui, self.Ud = (z.copy() for _ in range(7))

    def step(self, des, fb, hold=None) -> np.ndarray:
        e = np.asarray(des, dtype=F32) - np.asarray(fb, dtype=F32)
        integ = (((self.U <= self.UMax) & (e > 0)) | ((self.U >= -self.UMax) & (e < 0))) & (np.abs(e) < self.EMin)
        if hold is not None:
            integ &= ~hold
        sume = np.where(integ, self.SumE + e, self.SumE).astype(F32)
        self.SumE = np.clip(sume, -self.SumEMax, self.SumEMax)
        self.Ui = np.clip(self.Ki * self.SumE, -self.UiMax, self.UiMax)
        self.Up = np.clip(self.Kp * e, -self.UpMax, self.UpMax)
        self.Ud = np.clip(self.Kd * (e - self.PreE), -self.UdMax, self.UdMax)
        self.U = np.clip(self.Up + self.Ui + self.Ud, -self.UMax, self.UMax)
        self.E = e
        self.PreE = e
        return self.U


def fast_atan(v):
    """StabilizerTask.c fast_atan (~1430)."""
    v2 = v * v
    return v * (1.6867629106 + v2 * 0.4378497304) / (1.6867633134 + v2)


@dataclass(frozen=True)
class Plant:
    k: float               # deg/s^2 per torque tick
    tau_m: float           # s, motor/ESC first-order lag
    delay: float           # s, torque command -> gyro transport delay
    of_delay: float        # s, OF velocity delay
    of_noise: float        # cm/s rms per 100 Hz OF sample
    bias: float = 0.0      # torque ticks the controllers carry in hover
    lean: float = 0.0      # deg, controller-frame angle with zero lateral accel
    dist: float = 0.0      # cm/s^2 rms of a slow random lateral push (room air, battery sag)
    dist_tau: float = 2.0  # s, its correlation time
    gyro_lc: float = 0.0   # deg/s rms of a 25 Hz narrow-band dither on the gyro feedback (the logged rate limit cycle)

    def with_(self, **kw) -> Plant:
        return dataclasses.replace(self, **kw)


@dataclass
class Run:
    rows: dict[str, PidRow]
    plant: Plant
    axis: int = 0                    # 0 = x/roll, 1 = y/pitch
    rep: int = 0                     # noise replicate; runs with the same (axis, rep) share the noise
    sp: np.ndarray | None = None     # position setpoint per tick, cm (controller frame); None = hold 0
    vff: np.ndarray | None = None    # velocity feed-forward per tick into locxsPID.Des, cm/s
    aff: np.ndarray | None = None    # accel feed-forward per tick added to locxsPID.U, cm/s^2
    trim_ff: float = 0.0             # deg added to the angle setpoint (attitude trim)
    mrac_tau: float = 0.0            # s; 0 = PID only
    mrac_t0: float = 0.0             # s, u_ad starts integrating (output_injection_on rise)
    bias_step: tuple[float, float] | None = None   # (t s, bias after) torque step


def _per_tick(arrs: list[np.ndarray | None], ticks: int) -> np.ndarray:
    out = np.zeros((ticks, len(arrs)))
    for j, a in enumerate(arrs):
        if a is not None:
            a = np.asarray(a, dtype=float)
            out[:, j] = a[:ticks] if len(a) >= ticks else np.r_[a, np.full(ticks - len(a), a[-1])]
    return out


SIM_OUT = ("p", "pfb", "sp", "th", "w", "ang_des", "ang_u", "rate_u", "u_ad", "vel_u", "vfb", "pos_ui", "ang_ui",
           "rate_ui")


def simulate(runs: list[Run], duration: float, seed: int = 0, log_every: int = LOG_EVERY,
             keep: tuple[str, ...] = SIM_OUT) -> dict[str, np.ndarray]:
    """Run all `runs` for `duration` s; returns arrays (samples, runs) of `log_every`-tick means, plus 't'.

    Means, not point samples: the rate loop limit-cycles near the tick rate and point samples alias it."""
    n = len(runs)
    ticks = int(round(duration / TICK))
    cols = np.arange(n)
    pos, vel, ang, rate = (Pid([r.rows[k] for r in runs]) for k in ("pos", "vel", "ang", "rate"))
    pp = {f: np.array([getattr(r.plant, f) for r in runs], dtype=float) for f in Plant.__dataclass_fields__}
    h = TICK / SUB
    a_m = 1.0 - np.exp(-h / pp["tau_m"])
    d_sub = np.round(pp["delay"] / h).astype(int)
    ubuf = np.zeros((d_sub.max() + 1, n))
    d_of = np.round(pp["of_delay"] / (TICK * LOC_DIV)).astype(int)
    vbuf = np.zeros((d_of.max() + 1, n))
    rng = np.random.default_rng(seed)
    streams = [2 * r.rep + r.axis for r in runs]
    n_st = max(streams) + 1
    noise = rng.standard_normal((ticks // LOC_DIV + 1, n_st))[:, streams] * pp["of_noise"]
    # slow lateral push: Ornstein-Uhlenbeck accel, rms `dist`, time constant `dist_tau`, updated at 100 Hz
    dt_loc = TICK * LOC_DIV
    d_a = np.exp(-dt_loc / pp["dist_tau"])
    d_kick = rng.standard_normal((ticks // LOC_DIV + 1, n_st))[:, streams] * pp["dist"] * np.sqrt(1.0 - d_a ** 2)
    push = d_kick[0] / np.sqrt(1.0 - d_a ** 2)
    lc_amp = pp["gyro_lc"] * np.sqrt(2.0)
    lc_phase = rng.uniform(0, 2 * np.pi, n_st)[streams]
    lc_walk = rng.standard_normal((ticks, n_st))[:, streams] * 0.3
    sp = _per_tick([r.sp for r in runs], ticks)
    vff = _per_tick([r.vff for r in runs], ticks)
    aff = _per_tick([r.aff for r in runs], ticks)
    trim = np.array([r.trim_ff for r in runs])
    mtau = np.array([r.mrac_tau for r in runs])
    m_gain = np.where(mtau > 0, TICK / np.where(mtau > 0, mtau, 1.0), 0.0)
    m_t0 = np.array([r.mrac_t0 for r in runs])
    t_step = np.array([r.bias_step[0] if r.bias_step else np.inf for r in runs])
    b_after = np.array([r.bias_step[1] if r.bias_step else r.plant.bias for r in runs])

    th = np.zeros(n)
    w = np.zeros(n)
    tm = pp["bias"].copy()            # start in torque balance: integrators empty, motors carrying the bias
    v = np.zeros(n)
    p = np.zeros(n)
    pfb = np.zeros(n)
    uad = np.zeros(n)
    vfb = np.zeros(n)
    vel_u = np.zeros(n)
    ubuf[:] = pp["bias"]
    th[:] = pp["lean"]
    n_out = ticks // log_every
    out = {k: np.zeros((n_out, n)) for k in keep}
    acc = {k: np.zeros(n) for k in keep}
    for i in range(ticks):
        if i % LOC_DIV == 0:
            j = i // LOC_DIV
            push = push * d_a + d_kick[j]
            vbuf[j % len(vbuf)] = v
            vfb = vbuf[(j - d_of) % len(vbuf), cols] + noise[j]
            pfb = pfb + vfb * dt_loc
            pos.step(sp[i], pfb)
            vel.step(np.clip(pos.U, -VEL_SP_MAX, VEL_SP_MAX) + vff[i], vfb)
            vel_u = vel.U + aff[i]
        des = np.clip(fast_atan(vel_u / G) * RAD2DEG, -LEAN_MAX, LEAN_MAX) + trim
        ang.step(des, th)
        lc_phase = lc_phase + 2 * np.pi * 25.0 * TICK + lc_walk[i]
        rate.step(ang.U, w + lc_amp * np.sin(lc_phase))
        u_cmd = rate.U + uad
        uad = uad + np.where(i * TICK >= m_t0, m_gain, 0.0) * rate.U
        bias = np.where(i * TICK >= t_step, b_after, pp["bias"])
        for s in range(SUB):
            q = i * SUB + s
            ubuf[q % len(ubuf)] = u_cmd
            tm += (ubuf[(q - d_sub) % len(ubuf), cols] - tm) * a_m
            w += h * pp["k"] * (tm - bias)
            th += h * w
            v += h * (G * np.tan((th - pp["lean"]) / RAD2DEG) + push)
            p += h * v
        vals = {"p": p, "pfb": pfb, "sp": sp[i], "th": th, "w": w, "ang_des": des, "ang_u": ang.U, "rate_u": rate.U,
                "u_ad": uad, "vel_u": vel_u, "vfb": vfb, "pos_ui": pos.Ui, "ang_ui": ang.Ui, "rate_ui": rate.Ui}
        for k in keep:
            acc[k] += vals[k]
        if i % log_every == log_every - 1:
            o = i // log_every
            for k in keep:
                out[k][o] = acc[k] / log_every
                acc[k][:] = 0.0
    out["t"] = (np.arange(n_out) * log_every + (log_every - 1) / 2.0) * TICK
    return out


# --------------------------------------------------------------------------- scenarios

def waypoint_track(points_cm: np.ndarray, speed: float, hold_s: float) -> tuple[np.ndarray, np.ndarray]:
    """Waypoints at constant speed, linearly interpolated in time per tick as API/wfb_traj.c does.

    Returns (pos, vel) per tick, shape (ticks, 2), cm and cm/s, then `hold_s` at the last point."""
    seg = np.linalg.norm(np.diff(points_cm, axis=0), axis=1)
    t_pts = np.r_[0.0, np.cumsum(seg / (speed * 100.0))]
    t = np.arange(int(round((t_pts[-1] + hold_s) / TICK))) * TICK
    pos = np.stack([np.interp(t, t_pts, points_cm[:, a]) for a in range(2)], 1)
    k = np.clip(np.searchsorted(t_pts, t, side="right") - 1, 0, len(seg) - 1)
    vel = np.diff(points_cm, axis=0)[k] / (seg[k] / (speed * 100.0))[:, None]
    vel[t >= t_pts[-1]] = 0.0
    return pos, vel


def square_points(side_m: float = 1.0, step_m: float = 0.1) -> np.ndarray:
    m = int(round(side_m / step_m))
    s = np.linspace(0.0, side_m, m + 1)[1:] * 100.0
    e, z = np.full(m, side_m * 100.0), np.zeros(m)
    legs = [np.stack(a, 1) for a in ((s, z), (e, s), (e - s, e), (z, e - s))]
    return np.vstack([np.zeros((1, 2))] + legs)


def circle_points(radius_m: float = 0.5, step_m: float = 0.1) -> np.ndarray:
    m = int(np.ceil(2 * np.pi * radius_m / step_m))
    a = np.linspace(0.0, 2 * np.pi, m + 1)
    r = radius_m * 100.0
    return np.stack([r * np.sin(a), r - r * np.cos(a)], 1)


def accel_ff(vel: np.ndarray, tau: float = 0.2) -> np.ndarray:
    """Accel feed-forward: derivative of the first-order-filtered velocity feed-forward."""
    a = 1.0 - np.exp(-TICK / tau)
    vf = signal.lfilter([a], [1.0, a - 1.0], vel, axis=0, zi=np.zeros((1, vel.shape[1])) if vel.ndim > 1 else [0.0])[0]
    return np.gradient(vf, TICK, axis=0)


# --------------------------------------------------------------------------- metrics

def osc(x: np.ndarray, fs: float, fmin: float = 0.2, fmax: float = 3.0) -> tuple[float, float]:
    """Dominant oscillation in [fmin, fmax] Hz: (frequency, amplitude = sqrt(2 * band power near the peak))."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 64:
        return np.nan, np.nan
    f, pxx = signal.welch(x - x.mean(), fs, nperseg=min(len(x), int(20 * fs)), detrend="linear")
    band = (f >= fmin) & (f <= fmax)
    if not band.any():
        return np.nan, np.nan
    fpk = f[band][np.argmax(pxx[band])]
    near = band & (np.abs(f - fpk) <= max(0.15, f[1] - f[0]))
    return float(fpk), float(np.sqrt(2.0 * np.sum(pxx[near]) * (f[1] - f[0])))


def att_lag_ms(des: np.ndarray, fb: np.ndarray, fs: float, fmin: float = 0.3, fmax: float = 1.5) -> float:
    """Attitude lag Des -> FB: phase of the cross spectrum over [fmin, fmax] Hz as a time delay, ms."""
    if not (np.all(np.isfinite(des)) and np.all(np.isfinite(fb))):
        return np.nan
    nper = min(len(des), int(20 * fs))
    f, pxy = signal.csd(des - des.mean(), fb - fb.mean(), fs, nperseg=nper, detrend="linear")
    band = (f >= fmin) & (f <= fmax)
    wgt = np.abs(pxy[band])
    if not wgt.sum() > 0:
        return np.nan
    lag = -np.angle(pxy[band]) / (2 * np.pi * f[band])
    return float(1000.0 * np.average(lag, weights=wgt))


def settle_time(err: np.ndarray, t: np.ndarray, t0: float, tol: float) -> float:
    """Time after t0 until |err| stays below tol; nan if it never does."""
    after = t >= t0
    bad = np.flatnonzero(after & (np.abs(err) >= tol))
    if len(bad) == 0:
        return 0.0
    last = bad[-1]
    return float(t[last] - t0) if last + 1 < len(t) else np.nan


def sim_stats(out: dict[str, np.ndarray], t_from: float = 10.0) -> dict[str, np.ndarray]:
    """The flight_stats targets of every sim column (controller frame, position from the FB like the logs)."""
    m = out["t"] >= t_from
    fs = 1.0 / (TICK * LOG_EVERY)
    e = (out["sp"] - out["pfb"])[m]
    res = {"e": (out["ang_des"] - out["th"])[m].mean(0), "fb": out["th"][m].mean(0), "u": out["ang_u"][m].mean(0),
           "rate_u": out["rate_u"][m].mean(0), "u_ad": out["u_ad"][m].mean(0), "pos_e": e.mean(0),
           "pos_rms": e.std(0), "vel_u": out["vel_u"][m].mean(0)}
    res["rate_ui"] = res["rate_u"] - F0_ROWS["rate"].Kp * res["u"]
    sw = np.array([osc(out["th"][m, j], fs) for j in range(e.shape[1])])
    res["sway_hz"], res["sway_amp"] = sw[:, 0], sw[:, 1]
    res["lag_ms"] = np.array([att_lag_ms(out["ang_des"][m, j], out["th"][m, j], fs) for j in range(e.shape[1])])
    return res


# --------------------------------------------------------------------------- logs

def load_flight(logs_dir, name: str) -> dict[int, pd.DataFrame]:
    """Slot CSVs of one flight, keyed by slot, with t = t_src_ms / 1000; header-only CSVs skipped."""
    slots = {}
    for s in range(4):
        path = pathlib.Path(logs_dir) / f"{name}.slot{s}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        if len(df) < 2 or "t_src_ms" not in df:
            continue
        df.columns = [c.replace("Ctrler.", "") for c in df.columns]
        df = df.drop_duplicates("t_src_ms").sort_values("t_src_ms").reset_index(drop=True)
        df["t"] = df["t_src_ms"] / 1000.0
        slots[s] = df
    return slots


def _at(df: pd.DataFrame, col: str, t: np.ndarray) -> np.ndarray:
    """Nearest-earlier sample of df[col] at times t (zero-order hold)."""
    k = np.clip(np.searchsorted(df["t"].to_numpy(), t, side="right") - 1, 0, len(df) - 1)
    return df[col].to_numpy()[k]


def hover_window(slots, skip_s: float = 5.0, tail_s: float = 2.0) -> tuple[float, float]:
    """Longest stretch with flight_phase FLYING and OF hold on, minus the takeoff and landing ends."""
    s1 = slots[1]
    t = s1["t"].to_numpy()
    ok = s1["flight_phase"].to_numpy() == 1
    if 3 in slots and "g_of_hold_active" in slots[3]:
        ok &= _at(slots[3], "g_of_hold_active", t) == 1
    best, start = (0.0, 0.0), None
    for i in range(len(t) + 1):
        on = i < len(t) and ok[i] and (start is None or t[i] - t[i - 1] < 0.5)
        if on and start is None:
            start = i
        elif not on and start is not None:
            if t[i - 1] - t[start] > best[1] - best[0]:
                best = (t[start], t[i - 1])
            start = i if i < len(t) and ok[i] else None
    return best[0] + skip_s, best[1] - tail_s


def mixer_torque(s2: pd.DataFrame, axis: str) -> pd.Series:
    """Controller-frame torque per motor from the slot2 motor outputs (mixer, StabilizerTask.c ~1125-1143):
    roll = u_gyrox = (-m1 + m2 + m3 - m4)/4; pitch = gyroyPID.U + u_ad = (m1 - m2 + m3 - m4)/4."""
    m1, m2, m3, m4 = (s2[f"mymotor.motor{i}"].astype(float) for i in range(1, 5))
    return (-m1 + m2 + m3 - m4) / 4.0 if axis == "roll" else (m1 - m2 + m3 - m4) / 4.0


def flight_stats(slots) -> dict[str, float]:
    """Hover means and spectra in the controller frame (pitch = pitchPID frame, y' = -y)."""
    t0, t1 = hover_window(slots)
    win = {s: df[(df["t"] >= t0) & (df["t"] <= t1)] for s, df in slots.items()}
    s1, s2, s3 = win[1], win[2], win[3]
    out = {"t0": t0, "t1": t1, "dur": t1 - t0}
    for ax, a_pid, r_pid, sign in (("roll", "rollPID", "gyroxPID", 1.0), ("pitch", "pitchPID", "gyroyPID", -1.0)):
        out[f"{ax}_e"] = float((s2[f"{a_pid}.Des"] - s2[f"{a_pid}.FB"]).mean())
        out[f"{ax}_fb"] = float(s2[f"{a_pid}.FB"].mean())
        out[f"{ax}_u"] = float(s2[f"{a_pid}.U"].mean())
        out[f"{ax}_rate_u"] = float(s2[f"{r_pid}.U"].mean())
        out[f"{ax}_rate_ui"] = out[f"{ax}_rate_u"] - F0_ROWS["rate"].Kp * out[f"{ax}_u"]   # mean gyro FB ~ 0
        out[f"{ax}_need"] = float(mixer_torque(s2, ax).mean())
        out[f"{ax}_u_ad"] = out[f"{ax}_need"] - out[f"{ax}_rate_u"]
        out[f"{ax}_sway_hz"], out[f"{ax}_sway_amp"] = osc(s2[f"{a_pid}.FB"].to_numpy(), 50.0)
        out[f"{ax}_lag_ms"] = att_lag_ms(s2[f"{a_pid}.Des"].to_numpy(), s2[f"{a_pid}.FB"].to_numpy(), 50.0)
        lp = "locxPID" if ax == "roll" else "locyPID"
        e = sign * (s3[f"{lp}.Des"] - s3[f"{lp}.FB"]).to_numpy()
        out[f"{ax}_pos_e"] = float(e.mean())
        out[f"{ax}_pos_rms"] = float(np.sqrt(np.mean((e - e.mean()) ** 2)))
        vl = "locxsPID" if ax == "roll" else "locysPID"
        out[f"{ax}_vel_u"] = float(sign * s3[f"{vl}.U"].mean())
        vf = s3[f"{vl}.FB"].to_numpy()
        out[f"{ax}_vfb_hf"] = float(np.std(np.diff(vf)) / np.sqrt(2.0))
        out[f"{ax}_pos_hz"], out[f"{ax}_pos_amp"] = osc(e, 50.0)
    return out


def rate_hf(u: np.ndarray, w: np.ndarray, fs: float, fmin: float = 5.0) -> dict[str, float]:
    """Rate-loop limit cycle: peak frequency of the gyro rate above fmin Hz and the rms above fmin of rate and U."""
    out = {}
    for name, x in (("w", w), ("u", u)):
        f, p = signal.welch(x - x.mean(), fs, nperseg=min(len(x), 1024))
        hf = f > fmin
        out[f"{name}_hf"] = float(np.sqrt(np.sum(p[hf]) * (f[1] - f[0])))
        if name == "w":
            out["hf_hz"] = float(f[hf][np.argmax(p[hf])])
    return out


def log_rate_hf(slots, axis: str) -> dict[str, float]:
    t0, t1 = hover_window(slots)
    s1 = slots[1][(slots[1]["t"] >= t0) & (slots[1]["t"] <= t1)]
    r_pid = "gyroxPID" if axis == "roll" else "gyroyPID"
    return rate_hf(s1[f"{r_pid}.U"].to_numpy(dtype=float), s1[f"{r_pid}.FB"].to_numpy(dtype=float), 100.0)


def fit_rate_plant(slots, axis: str, delays=range(0, 9), taus=(0.01, 0.02, 0.03, 0.045, 0.06, 0.08, 0.1, 0.13)):
    """Least squares on the 100 Hz slot1: d(rate)/dt = k * (lag(delay(U)) - bias), grid over delay and tau.

    Returns dict(k, tau_m, delay, bias, r2). U = gyro?PID.U (+ u_ad when MRAC injects), rate = gyro?PID.FB."""
    t0, t1 = hover_window(slots)
    r_pid = "gyroxPID" if axis == "roll" else "gyroyPID"
    s1 = slots[1]
    s1 = s1[(s1["t"] >= t0) & (s1["t"] <= t1)]
    t = s1["t"].to_numpy()
    u = s1[f"{r_pid}.U"].to_numpy(dtype=float)
    w = s1[f"{r_pid}.FB"].to_numpy(dtype=float)
    dt = float(np.median(np.diff(t)))
    good = np.abs(np.diff(t) - dt) < 0.25 * dt
    best = None
    for n in delays:
        ud = np.r_[np.full(n, u[0]), u[: len(u) - n]]
        for tau in taus:
            a = 1.0 - np.exp(-dt / tau)
            uf = signal.lfilter([a], [1.0, a - 1.0], ud, zi=[ud[0] * (1.0 - a)])[0]
            x = np.c_[uf[:-1], np.ones(len(uf) - 1)][good] * dt
            y = np.diff(w)[good]
            coef, *_ = np.linalg.lstsq(x, y, rcond=None)
            r2 = 1.0 - np.sum((y - x @ coef) ** 2) / np.sum((y - y.mean()) ** 2)
            if best is None or r2 > best["r2"]:
                best = {"k": float(coef[0]), "bias": float(-coef[1] / coef[0]), "tau_m": tau, "delay": n * dt,
                        "r2": float(r2)}
    return best


def mrac_rise(slots, axis: str = "roll", fit_s: float = 10.0) -> tuple[np.ndarray, np.ndarray, float]:
    """Applied u_ad (mixer torque - gyro?PID.U, 50 Hz slot2, 0.2 s mean) for `fit_s` s after
    output_injection_on rises in flight. Returns (t from the rise, u_ad, final = mean from +5 s to hover end)."""
    t0, t1 = hover_window(slots, skip_s=0.0)
    s2 = slots[2]
    s2 = s2[(s2["t"] >= t0) & (s2["t"] <= t1)]
    t = s2["t"].to_numpy()
    on = np.flatnonzero(s2["mrac_flags.output_injection_on"].to_numpy() > 0)
    if len(on) == 0:
        return np.zeros(0), np.zeros(0), np.nan
    r_pid = "gyroxPID" if axis == "roll" else "gyroyPID"
    uad = np.convolve((mixer_torque(s2, axis) - s2[f"{r_pid}.U"]).to_numpy(), np.ones(10) / 10.0, mode="same")
    k0 = on[0]
    m = (t >= t[k0]) & (t < t[k0] + fit_s)
    return t[m] - t[k0], uad[m], float(np.mean(uad[t >= t[k0] + 5.0]))
