"""Rate-plant frequency response from a logged excitation (IV estimate), and its integrator+lag+delay fit.

Log sources, in order of preference:
  1. 0x03 ID frame keys as decoded by serial_bridge.py:903-933: id.<axis>.r / .x (rad/s), id.dither (deg/s),
     id.sample_counter (200 Hz), id.sysid_state. The run window is sysid_state RUNNING.
  2. The campaign core streams: Ctrler.gyro?PID.Des / .FB (deg/s) with xTickCount (ms) as the time base. The dither
     is rebuilt from the excite parameters (excitation.dither) and aligned to Des by cross-correlation.

T = Phi_xd / Phi_rd is the closed rate loop r -> x with the dither d as the instrument, and the plant is
P = T / (C (1 - T)), C the firmware PID that flew (design.pid_frf). That equals Phi_xd / Phi_ud for u = C (r - x)
(WP-25 step 3) without logging gyro?PID.U or the u_nom scale. It assumes MRAC injection off and no clamping.
The fit is fit_rate_plant's model (research/sim/cascade.py:432) fitted on the complex FRF instead of in time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd
from scipy import optimize, signal

from ground_station.autotune import excitation as ex
from ground_station.autotune.design import Plant, PidRow, pid_frf

RATE_PID: Mapping[str, str] = {"roll": "gyroxPID", "pitch": "gyroyPID", "yaw": "gyrozPID"}
ANGLE_PID: Mapping[str, str] = {"roll": "rollPID", "pitch": "pitchPID", "yaw": "yawPID"}
PRIM_STATE, PRIM_HOVER = "g_wfb_status.prim_state", 2
RAD2DEG = 180.0 / math.pi

# Acceptance of the identification. PROPOSED (WP-25), none measured on this airframe.
COH_MIN = 0.6            # per-bin coherence of d with both r and x
BAND_COVER_MIN = 0.5     # share of N_BANDS log sub-bands of [f0, f1] with at least one kept bin
N_BANDS = 10
FIT_MAX_REL = 0.35       # weighted relative RMS error of the fit on the kept bins
ALIGN_MIN_CORR = 0.3     # normalised correlation of the rebuilt dither with Des needed to call it found

Series = dict[str, tuple[np.ndarray, np.ndarray]]


class IdError(ValueError):
    """The log cannot give an identification; the message is the reason."""


@dataclass(frozen=True)
class IdRun:
    """One excitation window, uniformly sampled: dither d, rate setpoint r, rate x (deg/s)."""

    fs: float
    d: np.ndarray
    r: np.ndarray
    x: np.ndarray
    t0_s: float     # excitation start (start command) on the log time base
    source: str


@dataclass(frozen=True)
class Frf:
    f_hz: np.ndarray
    plant: np.ndarray       # complex P(j 2 pi f)
    coh: np.ndarray
    keep: np.ndarray        # in band and coherent
    band: tuple[float, float]
    coverage: float
    n_avg: int


@dataclass(frozen=True)
class PlantFit:
    plant: Plant
    rel_residual: float
    n_bins: int


def load_series(src: str | Path) -> Series:
    """{symbol: (t_s, values)} from a session dir / its telemetry.csv (received_ns,slot,key,value rows, keys
    slot<N>.<symbol>) or a wide CSV with a time column t (s) and one column per symbol."""
    p = Path(src)
    p = p / "telemetry.csv" if p.is_dir() else p
    df = pd.read_csv(p)
    out: Series = {}
    if {"received_ns", "key", "value"} <= set(df.columns):
        df = df.assign(key=df["key"].astype(str).str.replace(r"^slot\d+\.", "", regex=True),
                       t=(df["received_ns"] - df["received_ns"].min()) / 1e9,
                       value=pd.to_numeric(df["value"], errors="coerce")).dropna(subset=["value"])
        for key, g in df.groupby("key", sort=False):
            out[str(key)] = (g["t"].to_numpy(float), g["value"].to_numpy(float))
        return out
    if "t" not in df.columns:
        raise IdError(f"{p}: neither a session telemetry.csv nor a wide CSV with a 't' column")
    for col in df.columns.drop("t"):
        m = df[col].notna()
        out[str(col)] = (df.loc[m, "t"].to_numpy(float), pd.to_numeric(df.loc[m, col]).to_numpy(float))
    return out


def timebase(series: Series) -> Callable[[np.ndarray], np.ndarray]:
    """Host time -> firmware time (xTickCount, 1 kHz) when logged, so link jitter does not smear the spectra."""
    if "xTickCount" not in series:
        return lambda t: np.asarray(t, float)
    th, tick = series["xTickCount"]
    tick_s = (tick - tick[0]) / 1000.0
    return lambda t: np.interp(t, th, tick_s)


def _from_id_frame(series: Series, axis: str) -> IdRun:
    t_host, cnt = series["id.sample_counter"]

    def on_frame(key: str) -> np.ndarray:  # every id.* key comes in the same frame: same host times
        return np.interp(t_host, *series[key])

    st = np.rint(on_frame("id.sysid_state")) if "id.sysid_state" in series else np.zeros(0)
    if not np.any(st == ex.STATE_RUNNING):
        raise IdError("0x03 ID frame present but no sysid_state RUNNING sample")
    run = np.flatnonzero(st == ex.STATE_RUNNING)
    run = run[: np.argmax(np.diff(np.r_[run, run[-1] + 2]) > 1) + 1]  # first contiguous RUNNING block
    first = np.flatnonzero(st != ex.STATE_IDLE)[0]
    t = cnt[run] * ex.DT_S
    grid = np.arange(t[0], t[-1], ex.DT_S)
    get = {k: np.interp(grid, t, on_frame(k)[run]) for k in (f"id.{axis}.r", f"id.{axis}.x", "id.dither")}
    return IdRun(1.0 / ex.DT_S, get["id.dither"], get[f"id.{axis}.r"] * RAD2DEG, get[f"id.{axis}.x"] * RAD2DEG,
                 float(timebase(series)(t_host[first : first + 1])[0]), "0x03 ID frame")


def find_start(series: Series, axis: str, excite: Mapping[str, Any]) -> tuple[float, float, np.ndarray,
                                                                                  np.ndarray, np.ndarray, float]:
    """Align the rebuilt dither with Ctrler.<rate>.Des. Returns (t0_s, corr, grid, r, x, fs) on the time base."""
    des, fb = (f"Ctrler.{RATE_PID[axis]}.{s}" for s in ("Des", "FB"))
    if des not in series or fb not in series:
        raise IdError(f"log has neither the 0x03 ID frame (id.{axis}.*) nor {des} / {fb}")
    tb = timebase(series)
    tr, tx = tb(series[des][0]), tb(series[fb][0])
    fs = float(round(1.0 / np.median(np.diff(tr))))
    grid = np.arange(tr[0], tr[-1], 1.0 / fs)
    r, x = np.interp(grid, tr, series[des][1]), np.interp(grid, tx, series[fb][1])
    tmpl = ex.dither(np.arange(0.0, ex.active_s(excite["duration_s"]), 1.0 / fs), excite["signal"], excite["f0"],
                     excite["f1"], excite["amp"], excite["duration_s"])
    n_ma = max(1, int(2.0 * fs))
    r_hp = r - np.convolve(r, np.ones(n_ma) / n_ma, mode="same")
    if len(r_hp) < len(tmpl) // 2:
        raise IdError(f"log is {len(r_hp) / fs:.1f} s long, shorter than half the excitation")
    c = signal.correlate(np.r_[r_hp, np.zeros(len(tmpl))], tmpl, mode="valid")[: len(r_hp) - len(tmpl) // 2]
    lag = int(np.argmax(c))
    seg = r_hp[lag : lag + len(tmpl)]
    corr = float(c[lag] / (np.linalg.norm(seg) * np.linalg.norm(tmpl[: len(seg)]) + 1e-12))
    return float(grid[lag]), corr, grid, r, x, fs


def extract(series: Series, axis: str, excite: Mapping[str, Any]) -> IdRun:
    """The excitation run for one axis from either log source (see module doc)."""
    if f"id.{axis}.x" in series and "id.dither" in series and "id.sample_counter" in series:
        return _from_id_frame(series, axis)
    t0, corr, grid, r, x, fs = find_start(series, axis, excite)
    if corr < ALIGN_MIN_CORR:
        raise IdError(f"no {excite['signal']} found in Ctrler.{RATE_PID[axis]}.Des (best correlation {corr:.2f} < "
                      f"{ALIGN_MIN_CORR}): did the excite step run, with these f0/f1/duration?")
    w = (grid >= t0 + ex.RAMP_T_S) & (grid < t0 + ex.RAMP_T_S + excite["duration_s"])
    d = ex.dither(grid[w] - t0, excite["signal"], excite["f0"], excite["f1"], excite["amp"], excite["duration_s"])
    return IdRun(fs, d, r[w], x[w], t0, "core streams, dither rebuilt")


def plant_frf(runs: list[IdRun], flown: PidRow, band: tuple[float, float], coh_min: float = COH_MIN) -> Frf:
    """IV plant FRF averaged over runs (cross-spectra summed by Welch segment count)."""
    fs = runs[0].fs
    if any(abs(r.fs - fs) > 1e-6 for r in runs):
        raise IdError(f"runs differ in sample rate {[r.fs for r in runs]}")
    n = min(len(r.d) for r in runs)
    if n < 64:
        raise IdError(f"excitation window has {n} samples, need >= 64")
    nper = int(max(64, 2 ** math.floor(math.log2(n / 4.5))))
    acc: dict[str, Any] = {}
    n_avg = 0
    for run in runs:
        seg = (len(run.d) - nper) // (nper // 2) + 1
        for name, (a, b) in {"dd": (run.d, run.d), "dx": (run.d, run.x), "dr": (run.d, run.r),
                             "xx": (run.x, run.x), "rr": (run.r, run.r)}.items():
            f, s = signal.csd(a, b, fs=fs, nperseg=nper)
            acc[name] = acc.get(name, 0) + seg * s
        n_avg += seg
    with np.errstate(divide="ignore", invalid="ignore"):
        t = acc["dx"] / acc["dr"]
        coh = np.minimum(np.abs(acc["dx"]) ** 2 / (acc["dd"].real * acc["xx"].real),
                         np.abs(acc["dr"]) ** 2 / (acc["dd"].real * acc["rr"].real))
        p = t / (pid_frf(flown, 2 * math.pi * f) * (1.0 - t))
    coh = np.nan_to_num(coh)
    keep = (f >= band[0]) & (f <= band[1]) & (f > 0) & (coh >= coh_min) & np.isfinite(p)
    edges = np.geomspace(band[0], band[1], N_BANDS + 1)
    cover = np.mean([np.any(keep & (f >= lo) & (f <= hi)) for lo, hi in zip(edges[:-1], edges[1:])])
    return Frf(f, p, coh, keep, band, float(cover), n_avg)


def fit_plant(frf: Frf) -> PlantFit:
    """Weighted relative least squares of k e^{-sT} / (s (tau s + 1)) on the kept bins: grid over (T, tau) with k
    closed-form, then a local refine."""
    f, g, coh = frf.f_hz[frf.keep], frf.plant[frf.keep], frf.coh[frf.keep]
    if len(f) < 4:
        raise IdError(f"only {len(f)} coherent bins in {frf.band[0]:g}-{frf.band[1]:g} Hz, need >= 4 to fit")
    s, wgt = 2j * math.pi * f, np.minimum(coh / np.maximum(1.0 - coh, 1e-3), 50.0)
    y = g / np.abs(g)

    def k_res(tau: float, delay: float) -> tuple[float, float]:
        b = np.exp(-s * delay) / (s * (tau * s + 1.0)) / np.abs(g)
        k = float(np.sum(wgt * np.real(np.conj(b) * y)) / np.sum(wgt * np.abs(b) ** 2))
        return k, float(np.sum(wgt * np.abs(y - k * b) ** 2) / np.sum(wgt))

    grid = [(tau, dl) for tau in np.geomspace(0.003, 0.3, 40) for dl in np.arange(0.0, 0.0605, 0.0025)]
    tau0, dl0 = min(grid, key=lambda p: k_res(*p)[1])

    def resid(q: np.ndarray) -> np.ndarray:
        b = np.exp(q[0]) * np.exp(-s * q[2]) / (s * (np.exp(q[1]) * s + 1.0))
        e = np.sqrt(wgt / np.sum(wgt)) * (g - b) / np.abs(g)
        return np.r_[e.real, e.imag]

    k0 = k_res(tau0, dl0)[0]
    if k0 <= 0:  # wrong sign: report it, design_rate refuses a non-positive k
        return PlantFit(Plant(k0, tau0, dl0), math.sqrt(k_res(tau0, dl0)[1]), len(f))
    q0 = np.array([math.log(k0), math.log(tau0), dl0])
    q = optimize.least_squares(resid, q0, bounds=([-30, math.log(1e-3), 0.0], [30, math.log(1.0), 0.1])).x
    return PlantFit(Plant(float(math.exp(q[0])), float(math.exp(q[1])), float(q[2])),
                    float(np.sqrt(np.sum(resid(q) ** 2))), len(f))


def quality_problem(frf: Frf, fit: PlantFit | None) -> str:
    """Why the identification must not be used for a design, or '' if it may."""
    if frf.coverage < BAND_COVER_MIN:
        n = round(frf.coverage * N_BANDS)
        return (f"coherence >= {COH_MIN} in only {n} of {N_BANDS} sub-bands of {frf.band[0]:g}-{frf.band[1]:g} Hz "
                f"(need >= {math.ceil(BAND_COVER_MIN * N_BANDS)}): excitation too weak or too noisy")
    if fit is None:
        return "no fit"
    if fit.rel_residual > FIT_MAX_REL:
        return f"fit residual {fit.rel_residual:.2f} > {FIT_MAX_REL}: plant is not integrator+lag+delay here"
    return ""


def hover_rate_rms(series: Series, axis: str, t_end_s: float | None, skip_s: float = 2.0) -> float | None:
    """RMS of Des - FB on the rate loop in prim HOVER, from skip_s after HOVER is reached to 0.5 s before t_end_s
    (the first excitation start). None when the log has no such window."""
    des, fb = (f"Ctrler.{RATE_PID[axis]}.{s}" for s in ("Des", "FB"))
    if PRIM_STATE not in series or des not in series or fb not in series:
        return None
    tb = timebase(series)
    tp, prim = tb(series[PRIM_STATE][0]), np.rint(series[PRIM_STATE][1])
    if not np.any(prim == PRIM_HOVER):
        return None
    t, e = tb(series[des][0]), series[des][1] - np.interp(tb(series[des][0]), tb(series[fb][0]), series[fb][1])
    in_hover = prim[np.clip(np.searchsorted(tp, t, side="right") - 1, 0, len(prim) - 1)] == PRIM_HOVER
    m = in_hover & (t >= tp[np.argmax(prim == PRIM_HOVER)] + skip_s)
    if t_end_s is not None:
        m &= t < t_end_s - 0.5
    return float(np.sqrt(np.mean(e[m] ** 2))) if m.sum() >= 10 else None
