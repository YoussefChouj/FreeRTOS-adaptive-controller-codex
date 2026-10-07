"""Flight review (WP-42 P5): one self-contained HTML page per recorder session.

    python -m ground_station.analysis.flight_review <session_dir> [--out FILE] [--no-sat]

Sections:
  header           manifest (label, reason, duration) and the session_schema build/contracts when the recording has one
                   (WP-42 P3), the HOVER hold window, and the prim_state / safety_trip timeline
  setpoint/actual  x / y / z position (campaign_capture.POSITION_AXES) and every logged ``<loop>.Des`` / ``<loop>.FB``
                   pair, with error RMS and max over the hold (the whole flight span without one)
  injection        per change of mrac_flags.output_injection_on while armed: 4 s bins before / after (z, x/y and
                   attitude spread, u_ad / u_nom vs rate correlation, motor share at the high limit) and a +-10 s plot
  spectra          attitude and rate feedback (Hann rFFT on a uniform resample) with the largest line of each
  saturation       motor commands against the bench limits; share of flight-span samples at the high / low limit
  sample interval  histogram of the frame interval per telemetry slot, with median / p99 / max and the gap count

Plots are PNGs inside the page (data URIs), so the page is one file that opens anywhere; the numbers are also in the
page as JSON (``<script id="review-data">``). The loader, hold window and timeline are campaign_outputs' and
flight_debrief's; the controller replays of the same session are the WP-34 scripts (mrac_log_replay, refmodel_replay).
"""
from __future__ import annotations

import argparse
import base64
import csv
import html
import io
import json
import math
import statistics
from pathlib import Path
from typing import Any, Mapping

from ground_station.analysis import flight_debrief as fd
from ground_station.livewatch.campaign_capture import ATTITUDE, MOTORS, POSITION_AXES
from ground_station.service.campaign_outputs import _SLOT_PREFIX, PRIM_STATE, Series, hold_window

GAP_FACTOR = 3.0        # an interval above GAP_FACTOR x the slot median counts as a gap (as flight_debrief)
SPECTRUM_SYMS = (ATTITUDE[0], ATTITUDE[1], fd.RATE_FB["roll"], fd.RATE_FB["pitch"])


def read_stream(slot0: Path) -> tuple[Series, dict[int, list[float]]]:
    """A stream_log --frames capture (``<stem>.slot0.csv`` plus its sibling slots) as ``read_session`` returns it,
    loaded by log_corpus (board clock, t = 0 at the first slot-0 frame)."""
    from ground_station.analysis import log_corpus as lc
    raw = lc._load_stream(slot0)
    series: Series = {k: (t.tolist(), v.tolist()) for k, (t, v) in raw.items()}
    frames: dict[int, list[float]] = {}
    for i, f in enumerate(lc._stream_slots(slot0)):
        with f.open(newline="", encoding="utf-8") as fh:
            cols = [c for c in next(csv.reader(fh), []) if c not in ("t_src_ms", "t_host_s", "seq")]
        hit = next((series[c][0] for c in cols if c in series), None)
        if hit:
            frames[i] = hit
    return series, frames


def read_session(session_dir: str | Path) -> tuple[Series, dict[int, list[float]]]:
    """``campaign_outputs.read_telemetry``'s series plus the frame times (s) per slot, in one pass over the CSV.
    A ``<stem>.slot0.csv`` path reads a stream_log capture instead (``read_stream``)."""
    if str(session_dir).endswith(".slot0.csv"):
        return read_stream(Path(session_dir))
    series: Series = {}
    frames: dict[int, list[float]] = {}
    t0 = None
    with (Path(session_dir) / "telemetry.csv").open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                ns, value = int(row["received_ns"]), float(row["value"])
            except (KeyError, TypeError, ValueError):
                continue
            t0 = ns if t0 is None else t0
            t = (ns - t0) / 1e9
            ts, vs = series.setdefault(_SLOT_PREFIX.sub("", row.get("key") or ""), ([], []))
            ts.append(t)
            vs.append(value)
            try:
                slot = int(row.get("slot") or 0)
            except ValueError:
                slot = -1
            ft = frames.setdefault(slot, [])
            if not ft or ft[-1] != t:
                ft.append(t)
    return series, frames


def flight_span(series: Series) -> tuple[float, float] | None:
    """First and last sample with prim_state not IDLE; the whole recording without a prim_state stream."""
    ts, vs = series.get(PRIM_STATE, ([], []))
    if ts:
        busy = [t for t, v in zip(ts, vs) if int(round(v)) != 0]
        return (busy[0], busy[-1]) if busy else None
    all_t = [t for s in series.values() for t in (s[0][0], s[0][-1]) if s[0]]
    return (min(all_t), max(all_t)) if all_t else None


def _in(ts: list[float], vs: list[float], w: tuple[float, float] | None) -> tuple[list[float], list[float]]:
    if w is None:
        return ts, vs
    pts = [(t, v) for t, v in zip(ts, vs) if w[0] <= t <= w[1]]
    return [t for t, _ in pts], [v for _, v in pts]


def tracking_pairs(series: Series) -> list[dict[str, Any]]:
    """Position axes first (in m), then every other ``<loop>.Des`` / ``<loop>.FB`` pair in the log (raw units)."""
    out, used = [], set()
    for name, ax in zip(("x", "y", "z"), POSITION_AXES):
        if ax.feedback in series and ax.reference in series:
            out.append({"label": f"{name} position", "fb": ax.feedback, "des": ax.reference, "scale": ax.to_m,
                        "unit": "m"})
            used |= {ax.feedback, ax.reference}
    for sym in sorted(series):
        if sym.endswith(".FB") and sym not in used and (des := sym[:-3] + ".Des") in series:
            out.append({"label": sym[:-3], "fb": sym, "des": des, "scale": 1.0, "unit": ""})
    return out


def _err(series: Series, pair: Mapping[str, Any], w: tuple[float, float] | None) -> list[float]:
    import bisect
    ts, vs = _in(*series[pair["fb"]], w)
    rt, rv = series[pair["des"]]
    return [(v - rv[max(0, bisect.bisect_right(rt, t) - 1)]) * pair["scale"] for t, v in zip(ts, vs)]


def tracking_stats(series: Series, pairs: list[dict[str, Any]], w: tuple[float, float] | None) -> list[dict[str, Any]]:
    rows = []
    for p in pairs:
        e = _err(series, p, w)
        rows.append({"label": p["label"], "unit": p["unit"], "n": len(e),
                     "err_rms": round(math.sqrt(sum(x * x for x in e) / len(e)), 4) if e else None,
                     "err_max": round(max(abs(x) for x in e), 4) if e else None})
    return rows


def spectrum(ts: list[float], vs: list[float]) -> tuple[Any, Any] | None:
    """(freqs Hz, amplitude) of a Hann rFFT on a uniform resample at the median interval; None if too short."""
    import numpy as np
    if len(ts) < 16 or ts[-1] - ts[0] < fd.MIN_SPECTRUM_S:
        return None
    dt = statistics.median(b - a for a, b in zip(ts, ts[1:]))
    if dt <= 0:
        return None
    y = np.interp(np.arange(ts[0], ts[-1], dt), ts, vs)
    win = np.hanning(len(y))
    return np.fft.rfftfreq(len(y), dt), 2.0 * np.abs(np.fft.rfft((y - y.mean()) * win)) / win.sum()


def saturation(series: Series, sat: tuple[float, float] | None, w: tuple[float, float] | None) -> dict[str, Any]:
    """Per motor over the window: mean, min, max and (with limits) the share of samples at or past each limit."""
    out: dict[str, Any] = {}
    for sym in MOTORS:
        _, vs = _in(*series.get(sym, ([], [])), w)
        if not vs:
            continue
        m = {"n": len(vs), "mean": round(sum(vs) / len(vs), 1), "min": min(vs), "max": max(vs)}
        if sat is not None:
            hi, lo = sat
            m["frac_hi"] = round(sum(v >= hi for v in vs) / len(vs), 4)
            m["frac_lo"] = round(sum(v <= lo for v in vs) / len(vs), 4)
        out[sym.split(".")[-1]] = m
    return out


def interval_stats(frames: Mapping[int, list[float]]) -> dict[int, dict[str, Any]]:
    out = {}
    for slot, ts in sorted(frames.items()):
        dts = sorted(b - a for a, b in zip(ts, ts[1:]))
        if not dts:
            continue
        med = statistics.median(dts)
        out[slot] = {"frames": len(ts), "median_ms": round(1e3 * med, 2),
                     "p99_ms": round(1e3 * dts[int(0.99 * (len(dts) - 1))], 2), "max_ms": round(1e3 * dts[-1], 1),
                     "gaps": sum(d > GAP_FACTOR * med for d in dts),
                     "rate_hz": round((len(ts) - 1) / (ts[-1] - ts[0]), 1) if ts[-1] > ts[0] else None}
    return out


# ---- MRAC injection switch -------------------------------------------------------------------------------------

INJECTION = "mrac_flags.output_injection_on"
ARMED = "DroneStatus.ARM_Status"
U_AD = {"pitch": "mrac_state.pitch.u_ad", "roll": "mrac_state.roll.u_ad"}
U_NOM = {"pitch": "mrac_state.pitch.u_nom", "roll": "mrac_state.roll.u_nom"}
SWITCH_PAD_S = 10.0     # plot window either side of an injection switch
SWITCH_BIN_S = 4.0      # stats bin length
SWITCH_BINS = (2, 4)    # bins before / after a switch (cut at the next switch and at disarm)


def armed_span(series: Series) -> tuple[float, float] | None:
    ts, vs = series.get(ARMED, ([], []))
    on = [t for t, v in zip(ts, vs) if int(round(v)) != 0]
    return (on[0], on[-1]) if on else None


def switch_events(series: Series) -> list[dict[str, Any]]:
    """Every change of the MRAC output-injection flag while armed (the whole recording without an arm stream)."""
    ts, vs = series.get(INJECTION, ([], []))
    arm = armed_span(series)
    out, prev = [], None
    for t, v in zip(ts, vs):
        on = int(round(v)) != 0
        if prev is not None and on != prev and (arm is None or arm[0] <= t <= arm[1]):
            out.append({"t_s": round(t, 2), "to": "on" if on else "off"})
        prev = on
    return out


def _corr(series: Series, a: str, b: str, w: tuple[float, float]) -> float | None:
    import numpy as np
    ta, va = _in(*series.get(a, ([], [])), w)
    tb, vb = series.get(b, ([], []))
    if len(ta) < 8 or len(tb) < 2:
        return None
    x, y = np.asarray(va), np.interp(ta, tb, vb)
    if x.std() == 0 or y.std() == 0:
        return None
    return round(float(np.corrcoef(x, y)[0, 1]), 2)


def _sd(series: Series, sym: str, w: tuple[float, float], scale: float = 1.0) -> float | None:
    _, vs = _in(*series.get(sym, ([], [])), w)
    return round(scale * statistics.pstdev(vs), 3) if len(vs) > 2 else None


def _mean(series: Series, sym: str, w: tuple[float, float], scale: float = 1.0) -> float | None:
    _, vs = _in(*series.get(sym, ([], [])), w)
    return round(scale * sum(vs) / len(vs), 3) if vs else None


MOTOR_PWM_MAX = 4000.0  # BSP/pwm.h Motor_PWM_MAX: pwm.c clamps the CCR there, the logged mymotor.* can exceed it


def motor_top(series: Series, sat: tuple[float, float] | None) -> float | None:
    """The high limit: the bench one when given, else the firmware rail (logged commands at or past it are clipped)."""
    return sat[0] if sat is not None else MOTOR_PWM_MAX


def bin_stats(series: Series, w: tuple[float, float], top: float | None) -> dict[str, Any]:
    """One window: injection share, z vs setpoint, x/y spread, attitude spread and peak, the u_ad-rate correlation
    (positive = u_ad pushes with the rate, i.e. it removes damping) and the motor share at the high limit."""
    z, x, y = POSITION_AXES[2], POSITION_AXES[0], POSITION_AXES[1]
    row: dict[str, Any] = {"t0": round(w[0], 1), "t1": round(w[1], 1), "inj": _mean(series, INJECTION, w),
                           "z": _mean(series, z.feedback, w, z.to_m), "z_des": _mean(series, z.reference, w, z.to_m),
                           "x_sd": _sd(series, x.feedback, w, x.to_m), "y_sd": _sd(series, y.feedback, w, y.to_m),
                           "pit_sd": _sd(series, ATTITUDE[1], w), "rol_sd": _sd(series, ATTITUDE[0], w)}
    _, pv = _in(*series.get(ATTITUDE[1], ([], [])), w)
    row["pit_pk"] = round(max(abs(v - sum(pv) / len(pv)) for v in pv), 2) if pv else None
    for ax in ("pitch", "roll"):
        row[f"{ax[:3]}_ad_r"] = _corr(series, U_AD[ax], fd.RATE_FB[ax], w)
        row[f"{ax[:3]}_nom_r"] = _corr(series, U_NOM[ax], fd.RATE_FB[ax], w)
    if top is not None:
        fr = {s.split(".")[-1]: sum(v >= top for v in vs) / len(vs)
              for s in MOTORS if (vs := _in(*series.get(s, ([], [])), w)[1])}
        if fr:
            m = max(fr, key=fr.get)
            row["top_motor"], row["top_frac"] = m, round(fr[m], 2)
    return row


def switch_stats(series: Series, top: float | None) -> list[dict[str, Any]]:
    """Per injection switch: SWITCH_BINS bins of SWITCH_BIN_S before and after it, cut at the neighbouring switches
    and at the armed span (a bin shorter than half a bin is dropped)."""
    evs, arm = switch_events(series), armed_span(series)
    lo_all, hi_all = arm or (-math.inf, math.inf)
    out = []
    for i, ev in enumerate(evs):
        t = ev["t_s"]
        lo = max(lo_all, evs[i - 1]["t_s"] if i else -math.inf)
        hi = min(hi_all, evs[i + 1]["t_s"] if i + 1 < len(evs) else math.inf)
        wins = [(max(lo, t - k * SWITCH_BIN_S), t - (k - 1) * SWITCH_BIN_S) for k in range(SWITCH_BINS[0], 0, -1)]
        wins += [(t + (k - 1) * SWITCH_BIN_S, min(hi, t + k * SWITCH_BIN_S)) for k in range(1, SWITCH_BINS[1] + 1)]
        rows = [{"side": "before" if b <= t else "after", **bin_stats(series, (a, b), top)}
                for a, b in wins if b - a >= SWITCH_BIN_S / 2]
        out.append({**ev, "bins": rows})
    return out


# ---- plots -----------------------------------------------------------------------------------------------------

VARIANT, PRESET, OF1_ON = "vp_active", "kp_active", "g_ekf_of1_on"   # slot-3 labels (exp8_frames.md)
OF1_FLOW = ("ano_of.of1_dx", "ano_of.of1_dy")  # raw flow cm/s, EKF x/y frame (StabilizerTask.c EkfOf_UpdateRaw)
AIR_Z_M = 0.3           # airborne = z estimate above this inside an armed cycle
PAD_S = 1.0             # takeoff / landing position = mean over this long before liftoff / before disarm
MIN_RUN_S = 2.0         # shortest shadow / on run that counts


def armed_cycles(series: Series) -> list[tuple[float, float]]:
    """Every armed interval, rising to falling edge (a recording that ends armed closes at its last sample)."""
    ts, vs = series.get(ARMED, ([], []))
    out, t0 = [], None
    for t, v in zip(ts, vs):
        on = int(round(v)) != 0
        if on and t0 is None:
            t0 = t
        elif not on and t0 is not None:
            out.append((t0, t))
            t0 = None
    if t0 is not None:
        out.append((t0, ts[-1]))
    return out


def _mode(series: Series, sym: str, w: tuple[float, float]) -> int | None:
    r = [int(round(v)) for v in _in(*series.get(sym, ([], [])), w)[1]]
    return max(set(r), key=r.count) if r else None


def _np(series: Series, sym: str):
    import numpy as np
    ts, vs = series.get(sym, ([], []))
    return np.asarray(ts, float), np.asarray(vs, float)


def _sel(series: Series, sym: str, runs: list[tuple[float, float]]):
    import numpy as np
    ts, vs = _np(series, sym)
    m = np.zeros(len(ts), bool)
    for a, b in runs:
        m |= (ts >= a) & (ts <= b)
    return ts[m], vs[m]


def inj_runs(series: Series, w: tuple[float, float], state: bool) -> list[tuple[float, float]]:
    """Runs of at least MIN_RUN_S inside w with the injection flag == state (no flag stream: all of w is shadow)."""
    ts, vs = _in(*series.get(INJECTION, ([], [])), w)
    if not ts:
        return [] if state else [w]
    out, t0 = [], None
    for t, v in zip(ts, vs):
        hit = (int(round(v)) != 0) == state
        if hit and t0 is None:
            t0 = t
        elif not hit and t0 is not None:
            out.append((t0, t))
            t0 = None
    if t0 is not None:
        out.append((t0, w[1]))
    return [r for r in out if r[1] - r[0] >= MIN_RUN_S]


STILL_MPS = 0.05        # setpoint speed below this = the operator is not moving the stick (hover)


def still_runs(series: Series, w: tuple[float, float]) -> list[tuple[float, float]]:
    """Runs of at least MIN_RUN_S inside w where the x and y setpoints both move slower than STILL_MPS."""
    import numpy as np
    ts, sp = None, None
    for A in POSITION_AXES[:2]:
        t, v = _in(*series.get(A.reference, ([], [])), w)
        if len(t) < 3:
            return []
        t, v = np.asarray(t), np.asarray(v) * A.to_m
        k = max(1, int(round(0.5 / max(float(np.median(np.diff(t))), 1e-3))))  # 0.5 s difference
        d = np.abs(v[k:] - v[:-k]) / np.maximum(t[k:] - t[:-k], 1e-3)
        d = np.interp(t, t[k:], d)
        ts, sp = (t, d) if ts is None else (ts, np.maximum(sp, np.interp(ts, t, d)))
    out, t0 = [], None
    for t, v in zip(ts, sp):
        if v < STILL_MPS and t0 is None:
            t0 = t
        elif v >= STILL_MPS and t0 is not None:
            out.append((t0, t))
            t0 = None
    if t0 is not None:
        out.append((t0, w[1]))
    return [r for r in out if r[1] - r[0] >= MIN_RUN_S]


def _cross(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out = [(max(x0, y0), min(x1, y1)) for x0, x1 in a for y0, y1 in b if min(x1, y1) - max(x0, y0) >= MIN_RUN_S]
    return sorted(out)


def state_stats(series: Series, runs: list[tuple[float, float]], top: float | None) -> dict[str, Any]:
    """Pooled over the runs: attitude sd, x/y/z tracking rms (m), u_ad rms and its correlation with the body rate,
    and the motor share at the high limit."""
    import numpy as np
    row: dict[str, Any] = {"s": round(sum(b - a for a, b in runs), 1)}
    if not runs:
        return row
    for k, sym in (("pit", ATTITUDE[1]), ("rol", ATTITUDE[0])):
        v = _sel(series, sym, runs)[1]
        row[f"{k}_sd"] = round(float(v.std()), 2) if len(v) > 8 else None
    for i, k in enumerate("xyz"):
        a = POSITION_AXES[i]
        t, fb = _sel(series, a.feedback, runs)
        tr, ref = _np(series, a.reference)
        if len(t) > 8 and len(tr) > 1:
            row[f"{k}_rms"] = round(float(np.sqrt(np.mean(((fb - np.interp(t, tr, ref)) * a.to_m) ** 2))), 3)
    for ax in ("pitch", "roll"):
        t, u = _sel(series, U_AD[ax], runs)
        tr, r = _np(series, fd.RATE_FB[ax])
        if len(t) > 8 and len(tr) > 1:
            y = np.interp(t, tr, r)
            row[f"{ax[:3]}_uad"] = round(float(np.sqrt(np.mean(u ** 2))), 4)
            row[f"{ax[:3]}_r"] = round(float(np.corrcoef(u, y)[0, 1]), 2) if u.std() > 0 and y.std() > 0 else None
    if top is not None:
        fr = {s.split(".")[-1]: float(np.mean(v >= top)) for s in MOTORS if len(v := _sel(series, s, runs)[1])}
        if fr:
            m = max(fr, key=fr.get)
            row["top"] = f"{m} {fr[m]:.2f}"
    return row


def _norm_end(series: Series, ax: str, kind: str, t_end: float) -> float | None:
    """|kind| (Theta or Whatf, 6 weights) of one axis at the last sample at or before t_end."""
    import numpy as np
    vals = []
    for i in range(6):
        ts, vs = _np(series, f"mrac_state.{ax}.{kind}[{i}]")
        k = int(np.searchsorted(ts, t_end, side="right")) - 1
        if k >= 0:
            vals.append(vs[k])
    return round(float(np.linalg.norm(vals)), 3) if vals else None


def segment_stats(series: Series, w: tuple[float, float], top: float | None) -> dict[str, Any]:
    """One armed cycle: labels, airborne window, shadow vs injection-on stats, the takeoff-to-landing offset of the
    EKF estimate and of the raw of1 flow integrated on its own (the EKF holds its estimate on the setpoint, so a
    real drift the EKF cannot see shows up only in the of1 integral), and the weight norms at the end."""
    import numpy as np
    row: dict[str, Any] = {"vp": _mode(series, VARIANT, w), "kp": _mode(series, PRESET, w),
                           "of1": _mode(series, OF1_ON, w), "t0": round(w[0], 1), "t1": round(w[1], 1)}
    z = POSITION_AXES[2]
    tz, vz = _in(*series.get(z.feedback, ([], [])), w)
    air = [t for t, v in zip(tz, vz) if v * z.to_m >= AIR_Z_M]
    if len(air) < 2:
        return row
    a = (air[0], air[-1])
    row["air"] = [round(a[0], 1), round(a[1], 1)]
    row["air_s"] = round(a[1] - a[0], 1)
    inj = _in(*series.get(INJECTION, ([], [])), a)[1]
    row["inj_pct"] = round(100 * sum(int(round(v)) != 0 for v in inj) / len(inj)) if inj else None
    pos = {}
    for i, k in enumerate("xy"):
        ax = POSITION_AXES[i]
        t0w, t1w = (max(w[0], a[0] - PAD_S), a[0]), (max(a[1], w[1] - PAD_S), w[1])
        pos[k] = (_mean(series, ax.feedback, t0w, ax.to_m), _mean(series, ax.feedback, t1w, ax.to_m))
        ta, va = _in(*series.get(ax.feedback, ([], [])), a)
        pos[k + "air"] = (np.asarray(va) * ax.to_m - (pos[k][0] or 0.0)) if va else np.zeros(1)
        tf, vf = _in(*series.get(OF1_FLOW[i], ([], [])), a)
        pos[k + "of1"] = float(np.trapezoid(np.asarray(vf) * 0.01, tf)) if len(tf) > 1 else None
    if None not in (*pos["x"], *pos["y"]):
        dx, dy = pos["x"][1] - pos["x"][0], pos["y"][1] - pos["y"][0]
        row.update(land_dx=round(dx, 2), land_dy=round(dy, 2), land_off=round(math.hypot(dx, dy), 2))
        n = min(len(pos["xair"]), len(pos["yair"]))
        row["max_off"] = round(float(np.max(np.hypot(pos["xair"][:n], pos["yair"][:n]))), 2)
    if pos["xof1"] is not None and pos["yof1"] is not None:
        row.update(of1_dx=round(pos["xof1"], 2), of1_dy=round(pos["yof1"], 2),
                   of1_off=round(math.hypot(pos["xof1"], pos["yof1"]), 2))
    still = still_runs(series, a)
    for name, state in (("off", False), ("on", True)):
        runs = inj_runs(series, a, state)
        row[name] = state_stats(series, runs, top)
        row[name + "_still"] = state_stats(series, _cross(runs, still), top)
    # real drift in stick-free hover: the setpoint is still and the EKF holds it, so any motion the raw of1 flow
    # sees (cm/s, outside the EKF) is the drone walking away with the estimate
    runs = _cross(inj_runs(series, a, True) + inj_runs(series, a, False), still)
    if runs:
        row["still_s"] = round(sum(r[1] - r[0] for r in runs), 1)
        for i, k in enumerate("xy"):
            v = _sel(series, OF1_FLOW[i], runs)[1]
            row[f"of1_v{k}"] = round(float(np.mean(v)), 1) if len(v) else None
        if None not in (row["of1_vx"], row["of1_vy"]):
            row["of1_v"] = round(math.hypot(row["of1_vx"], row["of1_vy"]), 1)
    for ax in ("pitch", "roll"):
        row[f"th_{ax[0]}"] = _norm_end(series, ax, "Theta", a[1])
        row[f"wf_{ax[0]}"] = _norm_end(series, ax, "Whatf", a[1])
    return row


def variant_segments(series: Series, top: float | None) -> list[dict[str, Any]]:
    segs = [segment_stats(series, w, top) for w in armed_cycles(series)]
    return [s for s in segs if s.get("air")]


def _flat(seg: Mapping[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in seg.items() if not isinstance(v, (dict, list))}
    for name in ("off", "on", "off_still", "on_still"):
        out.update({f"{name}_{k}": v for k, v in (seg.get(name) or {}).items()})
    return out


def _png(fig) -> str:
    import matplotlib.pyplot as plt
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=80)
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _shade(axes, w: tuple[float, float] | None) -> None:
    if w:
        for ax in axes:
            ax.axvspan(*w, color="0.92", zorder=0)


def plot_tracking(series: Series, pairs: list[dict[str, Any]], hold: tuple[float, float] | None) -> str | None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    if not pairs:
        return None
    fig, axes = plt.subplots(len(pairs), 1, figsize=(10, 1.9 * len(pairs) + 0.6), sharex=True, squeeze=False)
    axes = axes[:, 0]
    for ax, p in zip(axes, pairs):
        for sym, style, lab in ((p["des"], "--", "setpoint"), (p["fb"], "-", "actual")):
            ts, vs = series[sym]
            ax.plot(ts, [v * p["scale"] for v in vs], style, linewidth=0.8, label=lab)
        ax.set_ylabel(f"{p['label']} {p['unit']}".strip(), fontsize=8)
        ax.legend(loc="upper right", fontsize=7)
    axes[-1].set_xlabel("t s (from recording start; grey: HOVER hold)")
    _shade(axes, hold)
    return _png(fig)


def plot_spectra(series: Series, w: tuple[float, float] | None) -> tuple[str | None, list[dict[str, Any]]]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    fig, ax = plt.subplots(figsize=(10, 3.6))
    peaks, drawn = [], False
    for sym in SPECTRUM_SYMS:
        ts, vs = _in(*series.get(sym, ([], [])), w)
        sp = spectrum(ts, vs)
        if sp is None:
            continue
        freqs, amp = sp
        ax.semilogy(freqs, amp + 1e-6, linewidth=0.8, label=sym)
        band = freqs >= 0.5
        if band.any():
            i = int(np.argmax(np.where(band, amp, -1.0)))
            peaks.append({"symbol": sym, "f_hz": round(float(freqs[i]), 2), "amp": round(float(amp[i]), 3),
                          "fs_hz": round(1.0 / statistics.median(b - a for a, b in zip(ts, ts[1:])), 1)})
        drawn = True
    if not drawn:
        plt.close(fig)
        return None, peaks
    ax.axvspan(1, 6, color="0.92", zorder=0)
    ax.axvline(8, color="0.6", linestyle=":")
    ax.set_xlabel("Hz (grey: angle-loop band 1-6 Hz; dotted: rate band from 8 Hz)")
    ax.set_ylabel("amplitude (deg, deg/s)")
    ax.legend(fontsize=7)
    return _png(fig), peaks


def plot_motors(series: Series, sat: tuple[float, float] | None, span: tuple[float, float] | None) -> str | None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    syms = [s for s in MOTORS if s in series]
    if not syms:
        return None
    fig, ax = plt.subplots(figsize=(10, 3.2))
    for sym in syms:
        ax.plot(*series[sym], linewidth=0.7, label=sym.split(".")[-1])
    for v in sat or ():
        ax.axhline(v, color="r", linestyle="--", linewidth=0.8)
    _shade([ax], span)
    ax.set_xlabel("t s (grey: flight span; red: bench saturation limits)")
    ax.set_ylabel("motor cmd")
    ax.legend(fontsize=7, ncol=4)
    return _png(fig)


def plot_switch(series: Series, t_sw: float, top: float | None) -> str | None:
    """SWITCH_PAD_S either side of one injection switch: z, x/y, attitude, u_ad against the rate (pitch, roll), motors."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    w = (t_sw - SWITCH_PAD_S, t_sw + SWITCH_PAD_S)
    fig, axes = plt.subplots(6, 1, figsize=(10, 11), sharex=True)
    for ax, i in ((axes[0], 2), (axes[1], 0), (axes[1], 1)):
        a = POSITION_AXES[i]
        name = "xyz"[i]
        for sym, style in ((a.reference, "--"), (a.feedback, "-")):
            ts, vs = _in(*series.get(sym, ([], [])), w)
            ax.plot(ts, [v * a.to_m for v in vs], style, linewidth=0.8, label=f"{name} {'set' if style == '--' else ''}")
    axes[0].set_ylabel("z m")
    axes[1].set_ylabel("x / y m")
    for sym, lab in ((ATTITUDE[1], "pitch"), (ATTITUDE[0], "roll")):
        axes[2].plot(*_in(*series.get(sym, ([], [])), w), linewidth=0.8, label=lab)
    axes[2].set_ylabel("attitude deg")
    for ax, k in ((axes[3], "pitch"), (axes[4], "roll")):
        ax.plot(*_in(*series.get(U_AD[k], ([], [])), w), linewidth=0.9, label=f"{k} u_ad")
        ax.plot(*_in(*series.get(U_NOM[k], ([], [])), w), linewidth=0.6, alpha=0.6, label=f"{k} u_nom")
        ax.set_ylabel(f"{k} cmd")
        tw = ax.twinx()
        tw.plot(*_in(*series.get(fd.RATE_FB[k], ([], [])), w), color="0.55", linewidth=0.6, label="rate")
        tw.set_ylabel("rate deg/s", fontsize=8)
    for sym in MOTORS:
        axes[5].plot(*_in(*series.get(sym, ([], [])), w), linewidth=0.7, label=sym.split(".")[-1])
    if top is not None:
        axes[5].axhline(top, color="r", linestyle="--", linewidth=0.8)
    axes[5].set_ylabel("motor cmd")
    axes[5].set_xlabel("t s (red line: injection switch; motors: red dashed = high limit)")
    for ax in axes:
        ax.axvline(t_sw, color="r", linewidth=1.0)
        ax.legend(loc="upper left", fontsize=7, ncol=4)
    return _png(fig)


def plot_variant_overview(segs: list[dict[str, Any]]) -> str | None:
    """Per variant: attitude sd shadow vs on, z / x / y tracking rms on, takeoff-to-landing offset EKF vs of1."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    if not segs:
        return None
    f = [_flat(s) for s in segs]
    lab = [f"vp{s['vp']}" for s in f]
    x = np.arange(len(f))
    g = lambda k: [s.get(k) if s.get(k) is not None else np.nan for s in f]  # noqa: E731
    fig, axes = plt.subplots(2, 2, figsize=(11, 7))
    for ax, keys, title in ((axes[0, 0], ("off_pit_sd", "on_pit_sd"), "pitch sd deg"),
                            (axes[0, 1], ("off_rol_sd", "on_rol_sd"), "roll sd deg"),
                            (axes[1, 0], ("on_z_rms", "on_x_rms", "on_y_rms"), "tracking rms m, injection on"),
                            (axes[1, 1], ("land_off", "of1_off"), "takeoff-to-landing offset m")):
        wdt = 0.8 / len(keys)
        for j, k in enumerate(keys):
            ax.bar(x + (j - (len(keys) - 1) / 2) * wdt, g(k), wdt, label=k)
        ax.set_xticks(x, lab)
        ax.set_title(title, fontsize=10)
        ax.legend(fontsize=7)
    axes[1, 1].text(0.01, 0.97, "land_off: EKF estimate; of1_off: raw of1 flow integrated (outside the EKF)",
                    transform=axes[1, 1].transAxes, fontsize=7, va="top")
    return _png(fig)


def plot_segment(series: Series, seg: Mapping[str, Any]) -> str | None:
    """One variant: x-y track (EKF solid, of1 integral dashed; takeoff triangle, landing square), x/y vs setpoint,
    attitude and u_ad against time with the injection-on time shaded."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    a = tuple(seg["air"])
    w = (seg["t0"], seg["t1"])
    fig = plt.figure(figsize=(11, 6.5))
    gs = fig.add_gridspec(3, 2, width_ratios=(1, 1.7))
    xy = fig.add_subplot(gs[:, 0])
    tl = [fig.add_subplot(gs[i, 1]) for i in range(3)]
    for ax in tl[1:]:
        ax.sharex(tl[0])
    p = {}
    for i, k in enumerate("xy"):
        A = POSITION_AXES[i]
        t, v = _in(*series.get(A.feedback, ([], [])), w)
        p[k] = (np.asarray(t), np.asarray(v) * A.to_m)
        tl[0].plot(t, p[k][1], linewidth=0.8, label=k)
        tl[0].plot(*_in(*series.get(A.reference, ([], [])), w)[0:1], [r * A.to_m for r in
                   _in(*series.get(A.reference, ([], [])), w)[1]], "--", linewidth=0.7, label=f"{k} set")
    tl[0].set_ylabel("x / y m")
    if len(p["x"][0]) and len(p["y"][0]):
        ty, yy = p["y"]
        tx, xx = p["x"]
        yi = np.interp(tx, ty, yy) if len(ty) > 1 else np.zeros_like(xx)
        m = (tx >= a[0]) & (tx <= a[1])
        xy.plot(xx[m], yi[m], linewidth=0.9, label="EKF")
        xy.plot(xx[m][:1], yi[m][:1], "^", color="g", markersize=9, label="takeoff")
        xy.plot(xx[m][-1:], yi[m][-1:], "s", color="r", markersize=8, label="landing")
        tf, fx = _in(*series.get(OF1_FLOW[0], ([], [])), a)
        tg, fy = _in(*series.get(OF1_FLOW[1], ([], [])), a)
        if len(tf) > 1 and len(tg) > 1 and m.any():
            cx = np.concatenate([[0], np.cumsum(np.diff(tf) * (np.asarray(fx[:-1]) + np.asarray(fx[1:])) / 200)])
            cy = np.interp(tf, tg, np.concatenate([[0], np.cumsum(np.diff(tg) * (np.asarray(fy[:-1]) +
                                                                                  np.asarray(fy[1:])) / 200)]))
            xy.plot(xx[m][0] + cx, yi[m][0] + cy, "--", linewidth=0.8, label="of1 integral")
    xy.set_aspect("equal", adjustable="datalim")
    xy.set_xlabel("x m")
    xy.set_ylabel("y m")
    xy.legend(fontsize=7)
    for sym, lab in ((ATTITUDE[1], "pitch"), (ATTITUDE[0], "roll")):
        tl[1].plot(*_in(*series.get(sym, ([], [])), w), linewidth=0.7, label=lab)
    tl[1].set_ylabel("deg")
    for k in ("pitch", "roll"):
        tl[2].plot(*_in(*series.get(U_AD[k], ([], [])), w), linewidth=0.7, label=f"{k} u_ad")
    tl[2].set_ylabel("u_ad")
    tl[2].set_xlabel("t s (shaded: injection on)")
    for r in inj_runs(series, a, True):
        for ax in tl:
            ax.axvspan(*r, color="#cfe3ff", zorder=0)
    for ax in tl:
        ax.legend(loc="upper left", fontsize=7, ncol=4)
    fig.suptitle(f"vp {seg['vp']}  kp {seg['kp']}  of1 {seg['of1']}  air {a[0]:.0f}-{a[1]:.0f} s", fontsize=10)
    return _png(fig)


def plot_intervals(frames: Mapping[int, list[float]]) -> str | None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    slots = [s for s, ts in sorted(frames.items()) if len(ts) > 1]
    if not slots:
        return None
    fig, axes = plt.subplots(1, len(slots), figsize=(10, 3.0), squeeze=False)
    for ax, s in zip(axes[0], slots):
        ts = frames[s]
        ax.hist([round(1e3 * (b - a), 3) for a, b in zip(ts, ts[1:])], bins=60, log=True)  # float-eps spread breaks bins
        ax.set_title(f"slot {s}", fontsize=8)
        ax.set_xlabel("frame interval ms", fontsize=8)
    axes[0][0].set_ylabel("count")
    return _png(fig)


# ---- page ------------------------------------------------------------------------------------------------------

def _table(rows: list[Mapping[str, Any]], cols: list[str]) -> str:
    if not rows:
        return "<p class=dim>none</p>"
    head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{html.escape('' if r.get(c) is None else str(r.get(c)))}</td>"
                                    for c in cols) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def _img(src: str | None, alt: str) -> str:
    return f'<img alt="{html.escape(alt)}" src="{src}">' if src else f"<p class=dim>no data for {html.escape(alt)}</p>"


CSS = """body{font:14px/1.45 system-ui,sans-serif;margin:16px auto;max-width:1060px;padding:0 16px;background:#fff;color:#111}
h1{font-size:20px}h2{font-size:16px;margin-top:28px;border-bottom:1px solid #ddd}img{max-width:100%}
table{border-collapse:collapse;margin:6px 0}td,th{border:1px solid #ddd;padding:2px 8px;text-align:left;font-size:13px}
.dim{color:#777}code{background:#f4f4f4;padding:0 3px}"""


def review(session_dir: str | Path, out: str | Path | None = None,
           sat: tuple[float, float] | None = None) -> Path:
    """Write the review page (default ``<session_dir>/flight_review.html``) and return its path."""
    d = Path(session_dir)
    series, frames = read_session(d)
    manifest: dict[str, Any] = {}
    if (d / "manifest.json").is_file():
        manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    schema = manifest.get("session_schema") or {}
    hold, span = hold_window(series), flight_span(series)
    w = hold or span
    pairs = tracking_pairs(series)
    spec_src, peaks = plot_spectra(series, w)
    top = motor_top(series, sat)
    switches = switch_stats(series, top) if INJECTION in series else []
    segs = variant_segments(series, top) if VARIANT in series else []
    data = {
        "session": d.name, "hold_window_s": hold, "flight_span_s": span, "sat_limits": sat,
        "timeline": fd.timeline(series),
        "tracking": tracking_stats(series, pairs, w),
        "spectral_peaks": peaks,
        "saturation": saturation(series, sat, span),
        "intervals": interval_stats(frames),
        "injection_switches": switches, "motor_top": top, "variant_segments": segs,
        "build": schema.get("build"), "contracts": schema.get("contracts"),
    }
    win_txt = (f"HOVER hold {hold[0]:.1f}-{hold[1]:.1f} s" if hold else
               f"no HOVER hold; flight span {span[0]:.1f}-{span[1]:.1f} s" if span else "no flight span")
    git = (schema.get("build") or {}).get("git") or {}
    meta = [{"field": "session", "value": d.name},
            {"field": "label / reason", "value": f"{manifest.get('label') or ''} {manifest.get('reason') or ''}".strip()},
            {"field": "window", "value": win_txt},
            {"field": "git", "value": f"{git.get('commit', '')[:12]}{' (dirty)' if git.get('dirty') else ''}" or None},
            {"field": "contracts", "value": json.dumps(schema.get("contracts")) if schema.get("contracts") else None},
            {"field": "variables / tunables", "value": f"{len(schema.get('variables') or [])} / "
                                                       f"{len(schema.get('tunables') or [])}" if schema else None}]
    sat_rows = [{"motor": k, **v} for k, v in data["saturation"].items()]
    iv_rows = [{"slot": k, **v} for k, v in data["intervals"].items()]
    sw_cols = ["side", "t0", "t1", "inj", "z", "z_des", "x_sd", "y_sd", "pit_sd", "pit_pk", "rol_sd",
               "pit_ad_r", "pit_nom_r", "rol_ad_r", "rol_nom_r", "top_motor", "top_frac"]
    sw_html = "".join(f"<h3>{html.escape(s['to'])} at {s['t_s']:.1f} s</h3>{_table(s['bins'], sw_cols)}"
                      f"{_img(plot_switch(series, s['t_s'], top), 'injection switch')}" for s in switches)
    if INJECTION in series:
        sw_html = f"""<h2>MRAC injection switches</h2>
<p class=dim>{SWITCH_BIN_S:g} s bins around each change of <code>{INJECTION}</code> while armed. z in m, sd and pk
(peak from the bin mean) in m / deg. *_ad_r / *_nom_r: correlation of u_ad / u_nom with the body rate; negative damps,
positive pushes with the motion. top_frac: share of samples at the motor high limit ({top}).</p>
{sw_html or "<p class=dim>no switch while armed</p>"}"""
    seg_html = ""
    if segs:
        seg_cols = ["vp", "kp", "of1", "air_s", "inj_pct", "off_still_s", "on_still_s", "off_still_pit_sd",
                    "on_still_pit_sd", "off_still_rol_sd", "on_still_rol_sd", "on_still_z_rms", "on_pit_sd",
                    "on_rol_sd", "on_pit_r", "on_rol_r", "on_pit_uad", "on_rol_uad", "on_top", "land_off", "max_off",
                    "of1_off", "of1_vx", "of1_vy", "of1_v", "th_p", "th_r"]
        seg_html = f"""<h2>Variant segments</h2>
<p class=dim>One row per armed cycle, labelled by <code>{VARIANT}</code> / <code>{PRESET}</code> / <code>{OF1_ON}</code>;
stats over the airborne part (z &gt; {AIR_Z_M:g} m), split into shadow (off_) and injection-on (on_) runs of at least
{MIN_RUN_S:g} s; *_still_*: only where both x/y setpoints move slower than {STILL_MPS:g} m/s (stick-free hover),
the rest is roam. sd in deg, rms in m. *_r: corr(u_ad, body rate), negative damps. land_off / max_off: EKF estimate,
landing and largest offset from the takeoff point. of1_off: raw of1 flow integrated over the airborne part, outside
the EKF (no bias correction). of1_vx / of1_vy / of1_v (cm/s): mean raw of1 flow over the stick-free hover, where the EKF holds the
setpoint: real drift the estimate does not see. th_p / th_r: |Theta| pitch / roll
at landing.</p>
{_table([_flat(s) for s in segs], seg_cols)}
{_img(plot_variant_overview(segs), "variant overview")}
{"".join(_img(plot_segment(series, s), f"vp {s['vp']}") for s in segs)}"""
    blob = json.dumps(data).replace("</", "<\\/")
    page = f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Flight review {html.escape(d.name)}</title>
<style>{CSS}</style></head><body>
<h1>Flight review: {html.escape(d.name)}</h1>
{_table(meta, ["field", "value"])}
<h2>Timeline</h2>{_table(data["timeline"], ["t_s", "what", "to"])}
<h2>Setpoint vs actual</h2>
<p class=dim>Error = actual - setpoint over the {"HOVER hold" if hold else "flight span"}.</p>
{_table(data["tracking"], ["label", "unit", "n", "err_rms", "err_max"])}
{_img(plot_tracking(series, pairs, hold), "setpoint vs actual")}
{seg_html}
{sw_html}
<h2>Spectra</h2>{_table(peaks, ["symbol", "f_hz", "amp", "fs_hz"])}{_img(spec_src, "spectra")}
<h2>Motor saturation</h2>
<p class=dim>Over the flight span; limits {html.escape(str(sat)) if sat else "not given (--no-sat)"}.</p>
{_table(sat_rows, ["motor", "n", "mean", "min", "max", "frac_hi", "frac_lo"])}
{_img(plot_motors(series, sat, span), "motor commands")}
<h2>Sample interval</h2>
<p class=dim>A gap is an interval above {GAP_FACTOR:g} x the slot median.</p>
{_table(iv_rows, ["slot", "frames", "rate_hz", "median_ms", "p99_ms", "max_ms", "gaps"])}
{_img(plot_intervals(frames), "sample interval histogram")}
<script type="application/json" id="review-data">{blob}</script>
</body></html>
"""
    path = Path(out) if out else (d.parent / (d.name[: -len(".slot0.csv")] + ".flight_review.html") if d.is_file()
                                  else d / "flight_review.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path


def page_data(path: str | Path) -> dict[str, Any]:
    """The numbers a review page carries (its ``review-data`` JSON)."""
    text = Path(path).read_text(encoding="utf-8")
    start = text.index('<script type="application/json" id="review-data">') + len(
        '<script type="application/json" id="review-data">')
    return json.loads(text[start:text.index("</script>", start)].replace("<\\/", "</"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("session", help="a recorder session dir (telemetry.csv) or a stream_log <stem>.slot0.csv")
    p.add_argument("--out", help="output HTML (default <session>/flight_review.html)")
    p.add_argument("--no-sat", action="store_true", help="skip the bench saturation limits")
    a = p.parse_args(argv)
    sat = None
    if not a.no_sat:
        try:
            from ground_station.service.campaign_live import _sat_hi_lo
            sat = _sat_hi_lo()
        except Exception:
            sat = None
    print(f"flight review: {review(a.session, a.out, sat)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
