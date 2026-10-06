"""Workflow C per-flight debrief: what happened, plots, findings with recommendations, and the next flight.

One flight in, one folder out (``logs/workflow-c/<run>/<NN>_<flight_id>/``):

    debrief.md     the page the agent sends the operator (bottom line, timeline, numbers vs the previous flight,
                   findings, next flight)
    debrief.json   the same as data
    plots/tracking.png   x / y / z vs reference, attitude, motors (campaign_outputs.plot_flight)
    plots/analysis.png   attitude and rate spectra over the hold, sample-interval histogram, per-motor means
    next.yaml      a one-flight fly campaign for the proposed next flight (``campaign_launch`` takes it)

``<run>/history.jsonl`` gets one line per flight (key metrics, the gain changes flown, the finding ids), so the next
debrief compares against the previous flight and judges whether a gain change helped.

Every threshold and step size here is PROPOSED (none has been checked against flown data); they live in the
``RULE_ROW`` table so a tuning pass edits one place. Gain changes target only the 7 loops CMD 0x01 can write
(``controller_descriptor.PID_AXES``), on the ground, through a ``run_plan`` the operator approves.

    python -m ground_station.analysis.flight_debrief <session_dir | campaign outputs_dir> --run logs/workflow-c/<run>
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ground_station.analysis.controller_descriptor import PID_AXES, PID_GAIN_CMD, PID_GAINS
from ground_station.livewatch.campaign_capture import ATTITUDE, KF_HEALTH, MOTORS
from ground_station.service.campaign_outputs import (
    PRIM_STATE, SAFETY_TRIP, Series, flight_metrics, hold_window, plot_flight, read_telemetry,
)

PRIM_NAMES = ("IDLE", "CLIMB", "HOVER", "TRAJ", "RETURN", "SETTLE", "DESCEND")  # API/wfb_prim.h
TRIP_NAMES = ("NONE", "HEARTBEAT", "LOW_V", "AIRBORNE_CAP", "FENCE", "CEILING", "TILT")  # API/wfb_safety.h
RATE_FB = {"roll": "Ctrler.gyroxPID.FB", "pitch": "Ctrler.gyroyPID.FB"}
RATE_DES = {"roll": "Ctrler.gyroxPID.Des", "pitch": "Ctrler.gyroyPID.Des"}
# CMD 0x01 write bounds (API/pid.c PID_CMD_ROW): 200 for every loop and gain, Z_ratePID Kp 800.
CMD_BOUND = {("Z_ratePID", "Kp"): 800.0}
CMD_BOUND_DEFAULT = 200.0
# Hover ladder for clean flights, all inside the soft fence (z <= 1.4). After the last rung: a campaign.
LADDER = ({"z": 0.5, "hold_s": 20}, {"z": 0.7, "hold_s": 20}, {"z": 1.0, "hold_s": 30}, {"z": 1.3, "hold_s": 40})
AFTER_LADDER = ("pid_ref", "livetune_rate_rp")
DEFAULT_LOG_GROUPS = ("velocity_loops", "optical_flow")


@dataclass(frozen=True)
class Rule:
    """One finding rule: watch at ``watch``, act at ``act`` (same unit as the metric)."""

    watch: float
    act: float
    unit: str


#                     watch   act    unit          (all PROPOSED)
RULE_ROW: Mapping[str, Rule] = {
    "z_bias":        Rule(0.05,  0.10,  "m"),        # |z mean - target| over the hold
    "z_wobble":      Rule(0.04,  0.08,  "m"),        # z error std over the hold (RMS with the bias removed)
    "xy_err_rms":    Rule(0.10,  0.20,  "m"),        # worse of x / y RMS error over the hold
    "att_rms":       Rule(2.0,   4.0,   "deg"),      # worse of roll / pitch RMS over the hold
    "att_lowf_peak": Rule(0.5,   1.0,   "deg"),      # largest attitude line in 1-6 Hz (angle loop)
    "rate_hf_peak":  Rule(5.0,   10.0,  "deg/s"),    # largest rate-FB line at >= 8 Hz (rate loop)
    "sat_frac":      Rule(0.02,  0.05,  "1"),        # share of hold samples with a motor at a limit
    "motor_spread":  Rule(0.06,  0.10,  "1"),        # (max - min) / mean of the per-motor hold means
    "gap_max_s":     Rule(0.25,  1.0,   "s"),        # longest telemetry gap
}
GAIN_STEP = 0.15        # relative step per flight for a proposed gain change, PROPOSED
MIN_SPECTRUM_S = 4.0    # hold shorter than this: no spectra
VERDICT_MARGIN = 0.10   # a targeted metric must move by 10 % to call a change better or worse


# ---- measurements ---------------------------------------------------------------------------------------------

def _window_vals(series: Series, sym: str, window: tuple[float, float]) -> tuple[list[float], list[float]]:
    ts, vs = series.get(sym, ([], []))
    pts = [(t, v) for t, v in zip(ts, vs) if window[0] <= t <= window[1]]
    return [t for t, _ in pts], [v for _, v in pts]


def spectrum_peak(ts: list[float], vs: list[float], f_lo: float, f_hi: float | None = None) -> dict[str, float] | None:
    """Largest line (Hann rFFT, amplitude in signal units) between f_lo and f_hi on a uniform resample."""
    import numpy as np

    if len(ts) < 16 or ts[-1] - ts[0] < MIN_SPECTRUM_S:
        return None
    dt = statistics.median(b - a for a, b in zip(ts, ts[1:]))
    if dt <= 0:
        return None
    grid = np.arange(ts[0], ts[-1], dt)
    y = np.interp(grid, ts, vs)
    y = y - y.mean()
    win = np.hanning(len(y))
    amp = 2.0 * np.abs(np.fft.rfft(y * win)) / win.sum()
    freqs = np.fft.rfftfreq(len(y), dt)
    band = (freqs >= f_lo) & (freqs <= (f_hi if f_hi is not None else freqs[-1]))
    if not band.any():
        return None
    i = int(np.argmax(np.where(band, amp, -1.0)))
    return {"f_hz": round(float(freqs[i]), 2), "amp": round(float(amp[i]), 3), "fs_hz": round(1.0 / dt, 1)}


def sample_timing(series: Series) -> dict[str, Any]:
    """Telemetry interval stats on the status stream (prim_state), else the most-sampled symbol."""
    sym = PRIM_STATE if PRIM_STATE in series else max(series, key=lambda k: len(series[k][0]), default=None)
    ts = series[sym][0] if sym else []
    dts = [b - a for a, b in zip(ts, ts[1:])]
    if not dts:
        return {"symbol": sym, "n": len(ts)}
    med = statistics.median(dts)
    srt = sorted(dts)
    return {"symbol": sym, "n": len(ts), "median_s": round(med, 4), "p99_s": round(srt[int(0.99 * (len(srt) - 1))], 4),
            "max_s": round(srt[-1], 3), "gaps": sum(d > 3 * med for d in dts), "dts": dts}


def timeline(series: Series) -> list[dict[str, Any]]:
    """prim_state and safety_trip transitions, in time order."""
    events = []
    for sym, names, what in ((PRIM_STATE, PRIM_NAMES, "state"), (SAFETY_TRIP, TRIP_NAMES, "trip")):
        prev = None
        for t, v in zip(*series.get(sym, ([], []))):
            k = int(round(v))
            if k != prev:
                name = names[k] if 0 <= k < len(names) else str(k)
                if prev is not None or k != 0:
                    events.append({"t_s": round(t, 2), "what": what, "to": name})
                prev = k
    return sorted(events, key=lambda e: e["t_s"])


def measure(series: Series, target_z_m: float | None, sat: tuple[float, float] | None) -> dict[str, Any]:
    """campaign_outputs.flight_metrics plus spectra, rate tracking, per-motor means and sample timing."""
    m = flight_metrics(series, target_z_m, sat)
    window = hold_window(series)
    m["timeline"] = timeline(series)
    timing = sample_timing(series)
    m["timing"] = {k: v for k, v in timing.items() if k != "dts"}
    if window is None:      # flight_metrics stops early; trips and EKF health still matter for a failed takeoff
        for key, sym, fn in (("kf_health_min", KF_HEALTH, min), ("safety_trip_max", SAFETY_TRIP, max)):
            vs = series.get(sym, ([], []))[1]
            m[key] = fn(vs) if vs else None
        return m
    spec: dict[str, Any] = {}
    for axis, sym in zip(("roll", "pitch"), ATTITUDE[:2]):
        spec[f"{axis}_att_lowf"] = spectrum_peak(*_window_vals(series, sym, window), 1.0, 6.0)
        spec[f"{axis}_rate_hf"] = spectrum_peak(*_window_vals(series, RATE_FB[axis], window), 8.0)
        ts, fb = _window_vals(series, RATE_FB[axis], window)
        _, des = _window_vals(series, RATE_DES[axis], window)
        if fb and len(des) == len(fb):
            err = [a - b for a, b in zip(fb, des)]
            m.setdefault("rate", {})[axis] = {"err_rms": round(math.sqrt(sum(e * e for e in err) / len(err)), 2)}
    m["spectra"] = spec
    z = m.get("z") or {}
    if z.get("err_rms_m") is not None and z.get("mean_minus_target_m") is not None:
        z["err_std_m"] = round(math.sqrt(max(0.0, z["err_rms_m"] ** 2 - z["mean_minus_target_m"] ** 2)), 3)
    means = {}
    for sym in MOTORS:
        _, vs = _window_vals(series, sym, window)
        if vs:
            means[sym.split(".")[-1]] = round(sum(vs) / len(vs), 1)
    if means:
        avg = sum(means.values()) / len(means)
        m.setdefault("motors", {})["means"] = means
        m["motors"]["spread"] = round((max(means.values()) - min(means.values())) / avg, 3) if avg else None
    return m


# ---- findings and recommendations -----------------------------------------------------------------------------

def _level(rule: str, x: float | None) -> str | None:
    if x is None:
        return None
    r = RULE_ROW[rule]
    return "act" if x >= r.act else "watch" if x >= r.watch else None


def _change(loop: str, gain: str, current: Mapping[str, Mapping[str, float]], factor: float) -> dict[str, Any] | None:
    old = (current.get(loop) or {}).get(gain)
    if old is None or old == 0:
        return None
    bound = CMD_BOUND.get((loop, gain), CMD_BOUND_DEFAULT)
    new = round(min(bound, max(0.0, old * factor)), 4)
    idx = PID_AXES.index(loop) * len(PID_GAINS) + PID_GAINS.index(gain)
    return {"loop": loop, "gain": gain, "from": old, "to": new,
            "step": {"action": "command", "args": {"command_id": PID_GAIN_CMD, "index": idx, "value": new}}}


def _worst(m: Mapping[str, Any], keys: tuple[str, ...], field: str) -> tuple[str | None, float | None]:
    best = (None, None)
    for k in keys:
        v = (m.get(k) or {}).get(field)
        if v is not None and (best[1] is None or abs(v) > abs(best[1])):
            best = (k, v)
    return best


def findings(m: Mapping[str, Any], gains: Mapping[str, Mapping[str, float]]) -> list[dict[str, Any]]:
    """Rule hits, worst first. Each: id, level (act / watch), evidence, why, recommend, change (or None),
    metric (the number a later flight is judged on), blocks_ladder."""
    out: list[dict[str, Any]] = []

    def add(fid, level, evidence, why, recommend, change=None, metric=None, blocks=False):
        if level:
            out.append({"id": fid, "level": level, "evidence": evidence, "why": why, "recommend": recommend,
                        "change": change, "metric": metric, "blocks_ladder": blocks or level == "act"})

    if not m.get("hold_s"):
        add("no_hover", "act", "prim_state never reached HOVER",
            "the takeoff did not finish; nothing about hold quality can be judged",
            "read the timeline and docs/workflow-b/failure-modes.md; fix the cause, then repeat this flight")
    trip = m.get("safety_trip_max")
    if trip:
        name = TRIP_NAMES[int(trip)] if 0 <= int(trip) < len(TRIP_NAMES) else str(trip)
        add("safety_trip", "act", f"wfb_safety tripped: {name}", "the firmware safety layer ended or limited the flight",
            "find the trip in failure-modes.md; LOW_V = swap the pack, FENCE/CEILING = check the position estimate "
            "before flying the same points again, TILT = check attitude oscillation below", blocks=True)
    if m.get("kf_health_min") == 0:
        add("kf_diverged", "act", "g_ekf_of_health hit 0", "the optical-flow EKF diverged; position hold is blind",
            "check the floor texture and lighting, then repeat at the same height", blocks=True)
    z = m.get("z") or {}
    bias = z.get("mean_minus_target_m")
    add("z_bias", _level("z_bias", abs(bias) if bias is not None else None),
        f"z mean {z.get('mean_m')} m vs target {z.get('target_m')} m ({bias:+.3f} m)" if bias is not None else "",
        "a steady altitude offset is a loop that does not integrate it out (Z_posPID Ki is not CMD 0x01 writable)",
        "raise Z_ratePID Ki one step; if the bias stays, it needs a Z_posPID table edit (firmware, flash)",
        _change("Z_ratePID", "Ki", gains, 1 + GAIN_STEP), "z.mean_minus_target_m")
    add("z_wobble", _level("z_wobble", z.get("err_std_m")), f"z error std {z.get('err_std_m')} m (bias removed)",
        "altitude oscillates around its own mean: the climb-rate loop overshoots",
        "lower Z_ratePID Kp one step", _change("Z_ratePID", "Kp", gains, 1 - GAIN_STEP), "z.err_std_m")
    axis, xy = _worst(m, ("x", "y"), "err_rms_m")
    add("xy_drift", _level("xy_err_rms", xy), f"{axis} RMS error {xy} m",
        "the position loops (locx/locyPID, not CMD 0x01 writable) or the optical-flow velocity let it drift",
        "run python -m ground_station.analysis.drift_rootcause on this session; keep velocity_loops + optical_flow "
        "in the log plan", None, f"{axis}.err_rms_m" if axis else None)
    spec = m.get("spectra") or {}
    for ax, angle_loop, rate_loop in (("roll", "rollPID", "gyroxPID"), ("pitch", "pitchPID", "gyroyPID")):
        hf = spec.get(f"{ax}_rate_hf") or {}
        add(f"{ax}_rate_osc", _level("rate_hf_peak", hf.get("amp")),
            f"{ax} rate line {hf.get('amp')} deg/s at {hf.get('f_hz')} Hz",
            "a fast oscillation is the inner rate loop ringing (too much D or Kp, or the 25 Hz limit cycle)",
            f"lower {rate_loop} Kd one step", _change(rate_loop, "Kd", gains, 1 - GAIN_STEP),
            f"spectra.{ax}_rate_hf.amp")
        lf = spec.get(f"{ax}_att_lowf") or {}
        add(f"{ax}_att_osc", _level("att_lowf_peak", lf.get("amp")),
            f"{ax} attitude line {lf.get('amp')} deg at {lf.get('f_hz')} Hz",
            "a slow wobble is the outer angle loop overshooting", f"lower {angle_loop} Kp one step",
            _change(angle_loop, "Kp", gains, 1 - GAIN_STEP), f"spectra.{ax}_att_lowf.amp")
    ax, att = _worst(m, ("roll", "pitch"), "rms_deg")
    add("attitude", _level("att_rms", att), f"{ax} RMS {att} deg over the hold",
        "large lean while holding: trim, CG offset, or the position loop working hard",
        "compare with the per-motor means; a steady lean with one hot motor is CG / prop, not gains",
        None, f"{ax}.rms_deg" if ax else None)
    mot = m.get("motors") or {}
    add("saturation", _level("sat_frac", mot.get("sat_frac")), f"motors at a limit {mot.get('sat_frac')} of the hold",
        "no headroom left: the controller cannot correct when a motor is pinned",
        "swap to a full pack and check the thrust margin before raising any gain", None, "motors.sat_frac", True)
    spread = mot.get("spread")
    hot = max(mot.get("means") or {"-": 0}, key=lambda k: (mot.get("means") or {}).get(k, 0))
    add("motor_spread", _level("motor_spread", spread), f"per-motor hold means spread {spread} (highest {hot})",
        "one motor works harder: CG offset, a bent prop or a weak motor", "check the prop and the CG on the bench",
        None, "motors.spread")
    timing = m.get("timing") or {}
    add("telemetry_gaps", _level("gap_max_s", timing.get("max_s")),
        f"longest telemetry gap {timing.get('max_s')} s, {timing.get('gaps')} gaps > 3x median",
        "the radio link dropped samples; spectra and RMS are less trustworthy", "lower the log rate or drop a group")
    order = {"act": 0, "watch": 1}
    return sorted(out, key=lambda f: order[f["level"]])


def _get(m: Mapping[str, Any], path: str | None) -> float | None:
    cur: Any = m
    for part in (path or "").split("."):
        cur = cur.get(part) if isinstance(cur, Mapping) else None
    return abs(cur) if isinstance(cur, (int, float)) else None


def flown_changes(prev: Mapping[str, Any] | None, applied: Sequence[str] | None,
                  gains: Mapping[str, Mapping[str, float]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(flown, not_applied). A proposed change counts as flown only when `applied` names it ("loop.gain", or
    "loop.gain=value" when a different value went in): a proposal is not a write (F6 credited Kd 10->8.5 unflown)."""
    proposed = list((prev or {}).get("next_changes", []))
    flown = []
    for spec in applied or []:
        key, _, val = spec.partition("=")
        loop, _, gain = key.partition(".")
        ch = next((c for c in proposed if c["loop"] == loop and c["gain"] == gain), None)
        if ch is None:
            ch = {"loop": loop, "gain": gain, "from": gains.get(loop, {}).get(gain), "to": None, "metric": None}
        else:
            proposed.remove(ch)
        flown.append({**ch, "to": float(val) if val else ch["to"]})
    return flown, proposed


def judge_changes(flown: list[dict[str, Any]], m: Mapping[str, Any]) -> list[dict[str, Any]]:
    """For each gain change flown since the previous flight: better / worse / no clear effect on its metric."""
    out = []
    for ch in flown:
        before, after = ch.get("before"), _get(m, ch.get("metric"))
        if before is None or after is None:
            verdict = "not measured"
        elif after <= before * (1 - VERDICT_MARGIN):
            verdict = "better"
        elif after >= before * (1 + VERDICT_MARGIN):
            verdict = "worse"
        else:
            verdict = "no clear effect"
        out.append({**{k: ch[k] for k in ("loop", "gain", "from", "to", "metric")}, "before": before, "after": after,
                    "verdict": verdict})
    return out


def propose_next(scenario_args: Mapping[str, Any], found: list[dict[str, Any]],
                 verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic next flight: revert a change that made things worse; else try the top gain change on the
    same flight (A/B); else repeat after a blocking fix; else the next ladder rung."""
    args = dict(scenario_args)
    worse = [v for v in verdicts if v["verdict"] == "worse"]
    if worse:
        changes = [_change(v["loop"], v["gain"], {v["loop"]: {v["gain"]: v["to"]}}, v["from"] / v["to"]) for v in worse]
        return {"scenario": "hover", "args": args, "changes": [c for c in changes if c],
                "why": "the last change made " + ", ".join(v["metric"] for v in worse) + " worse: revert it, fly again"}
    tunable = [f for f in found if f["change"]]
    if tunable:
        f = tunable[0]
        return {"scenario": "hover", "args": args, "changes": [{**f["change"], "metric": f["metric"]}],
                "why": f"{f['id']}: {f['recommend']}; same flight again so the change is the only difference"}
    if any(f["blocks_ladder"] for f in found):
        f = next(f for f in found if f["blocks_ladder"])
        return {"scenario": "hover", "args": args, "changes": [],
                "why": f"{f['id']} must be fixed first ({f['recommend']}); repeat this flight"}
    rung = next((r for r in LADDER if (r["z"], r["hold_s"]) > (args.get("z", 0), args.get("hold_s", 0))), None)
    if rung is None:
        return {"scenario": None, "args": {}, "changes": [], "campaigns": list(AFTER_LADDER),
                "why": "the hover ladder is clean: move to a reference campaign (pid_ref) or live rate tuning"}
    return {"scenario": "hover", "args": dict(rung), "changes": [], "why": "clean hold: next ladder rung"}


# ---- outputs --------------------------------------------------------------------------------------------------

def plot_analysis(series: Series, m: Mapping[str, Any], path: Path, sat: tuple[float, float] | None) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    window = hold_window(series)
    fig, axes = plt.subplots(3, 1, figsize=(10, 10))
    if window:
        for ax_name, sym in (("roll att", ATTITUDE[0]), ("pitch att", ATTITUDE[1]),
                             ("roll rate", RATE_FB["roll"]), ("pitch rate", RATE_FB["pitch"])):
            ts, vs = _window_vals(series, sym, window)
            if len(ts) >= 16 and ts[-1] - ts[0] >= MIN_SPECTRUM_S:
                dt = statistics.median(b - a for a, b in zip(ts, ts[1:]))
                y = np.interp(np.arange(ts[0], ts[-1], dt), ts, vs)
                win = np.hanning(len(y))
                axes[0].semilogy(np.fft.rfftfreq(len(y), dt), 2 * np.abs(np.fft.rfft((y - y.mean()) * win)) / win.sum()
                                 + 1e-6, label=ax_name, linewidth=0.8)
        axes[0].axvspan(1, 6, color="0.92", zorder=0)
        axes[0].axvline(8, color="0.6", linestyle=":")
    axes[0].set_xlabel("Hz (grey: angle-loop band 1-6 Hz; dotted: rate band from 8 Hz)")
    axes[0].set_ylabel("amplitude (deg, deg/s)")
    axes[0].legend(fontsize=8)
    axes[0].set_title("spectra over the HOVER hold")
    dts = sample_timing(series).get("dts") or []
    if dts:
        axes[1].hist([d * 1000 for d in dts], bins=60, log=True)
    axes[1].set_xlabel("telemetry sample interval ms (status stream)")
    axes[1].set_ylabel("count")
    means = (m.get("motors") or {}).get("means") or {}
    axes[2].bar(list(means), list(means.values()))
    if sat:
        for v in sat:
            axes[2].axhline(v, color="r", linestyle="--", linewidth=0.8)
    axes[2].set_ylabel("motor cmd, hold mean (red: limits)")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=90)
    plt.close(fig)
    return path


def _fmt(x: Any) -> str:
    return "-" if x is None else f"{x:.3f}" if isinstance(x, float) else str(x)


KEY_METRICS = (("hold s", "hold_s"), ("z mean - target m", "z.mean_minus_target_m"), ("z RMS m", "z.err_rms_m"), ("z std m", "z.err_std_m"),
               ("x RMS m", "x.err_rms_m"), ("y RMS m", "y.err_rms_m"), ("roll RMS deg", "roll.rms_deg"),
               ("pitch RMS deg", "pitch.rms_deg"), ("roll rate line deg/s", "spectra.roll_rate_hf.amp"),
               ("pitch rate line deg/s", "spectra.pitch_rate_hf.amp"), ("motor sat frac", "motors.sat_frac"),
               ("motor spread", "motors.spread"), ("telemetry max gap s", "timing.max_s"))


def _raw(m: Mapping[str, Any], path: str) -> Any:
    cur: Any = m
    for part in path.split("."):
        cur = cur.get(part) if isinstance(cur, Mapping) else None
    return cur


def render(d: Mapping[str, Any]) -> str:
    m, prev = d["metrics"], (d.get("previous") or {}).get("metrics") or {}
    found, nxt = d["findings"], d["next"]
    top = found[0] if found else None
    bottom = (f"{top['level'].upper()}: {top['evidence']}. {top['recommend']}." if top
              else "Clean hold: no rule fired.")
    lines = [f"# Flight {d['n']} debrief: {d['flight_id']} ({d['scenario']} {json.dumps(d['scenario_args'])})", "",
             f"**Bottom line:** {bottom}", "", "## What happened", "", "| t s | event |", "|---|---|"]
    lines += [f"| {e['t_s']} | {e['what']} -> {e['to']} |" for e in m.get("timeline", [])] or ["| - | no status stream |"]
    lines += ["", "## Numbers (hold window)", "", "| metric | this flight | previous |", "|---|---|---|"]
    lines += [f"| {label} | {_fmt(_raw(m, p))} | {_fmt(_raw(prev, p))} |" for label, p in KEY_METRICS]
    if d.get("verdicts"):
        lines += ["", "## Did the last change help?", "", "| change | metric | before | after | verdict |",
                  "|---|---|---|---|---|"]
        lines += [f"| {v['loop']}.{v['gain']} {v['from']} -> {v['to']} | {v['metric']} | {_fmt(v['before'])} "
                  f"| {_fmt(v['after'])} | **{v['verdict']}** |" for v in d["verdicts"]]
    if d.get("not_applied"):
        lines += ["", "Proposed but NOT applied before this flight (no verdict): " + ", ".join(
            f"{c['loop']}.{c['gain']} {c['from']} -> {c['to']}" for c in d["not_applied"])]
    lines += ["", "## Findings (thresholds PROPOSED)", "", "| level | finding | evidence | recommendation |",
              "|---|---|---|---|"]
    lines += [f"| {f['level']} | {f['id']} | {f['evidence']} | {f['recommend']} |" for f in found] or \
             ["| ok | - | every rule under its watch level | - |"]
    lines += ["", "## Next flight (PROPOSED)", "", f"- why: {nxt['why']}"]
    if nxt.get("scenario"):
        lines.append(f"- fly: `{nxt['scenario']}` {json.dumps(nxt['args'])}, launch copy from `next.yaml`")
    for c in nxt.get("changes", []):
        lines.append(f"- on the ground, before Go: {c['loop']}.{c['gain']} {c['from']} -> {c['to']} "
                     f"(run_plan step `{json.dumps(c['step']['args'])}`, operator approves)")
    for name in nxt.get("campaigns", []):
        lines.append(f"- campaign: `{name}`")
    lines += ["", "Plots: [tracking](plots/tracking.png), [spectra / timing / motors](plots/analysis.png)"]
    return "\n".join(lines) + "\n"


def next_campaign_yaml(run_name: str, n: int, nxt: Mapping[str, Any], pack: str,
                       groups: tuple[str, ...] = DEFAULT_LOG_GROUPS, rate_hz: float = 50) -> str | None:
    if not nxt.get("scenario"):
        return None
    name = f"wfc-{run_name}-{n:02d}"[:40].lower()
    args = ", ".join(f"{k}: {v}" for k, v in nxt["args"].items())
    return (f"# Workflow C flight {n}, proposed by flight_debrief: {nxt['why']}\n"
            f"campaign: {name}\nobjective: \"workflow C flight {n}\"\ncontroller: pid\nmode: fly\n"
            f"packs:\n  - {pack}\nmax_flights: 1\nexperiments:\n"
            f"  - name: f{n:02d}\n    scenario: {nxt['scenario']}\n    scenario_args: {{{args}}}\n"
            f"    capture: campaign\n    repeats: 1\n"
            f"    log_plan: {{rate_hz: {rate_hz:g}, groups: [{', '.join(groups)}]}}\nabort: {{}}\n")


def flight_number(flight_id: str, history: list[dict[str, Any]]) -> int:
    """The flight's own number from its id (wfc-<run>-NN-<seq>); counting debriefs breaks when a flight was never
    debriefed (10-06: f01/f02 skipped, f03 became folder 01 and "flight 2")."""
    m = re.match(r"wfc-.+-(\d{2})-\d{3}$", flight_id)
    return int(m.group(1)) if m else len(history) + 1


def read_history(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "history.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def current_gains(history: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """API/pid.c defaults with every change flown in this run applied on top (a reboot resets them: say so)."""
    from ground_station.autotune.design import read_pid_rows

    rows = read_pid_rows()
    gains = {loop: dict(rows[loop].gains()) for loop in PID_AXES if loop in rows}
    for h in history:
        for c in h.get("flown_changes", []):
            if c.get("to") is not None:
                gains.setdefault(c["loop"], {})[c["gain"]] = c["to"]
    return gains


def debrief(session_dir: str | Path, run_dir: str | Path, *, flight_id: str | None = None, scenario: str = "hover",
            scenario_args: Mapping[str, Any] | None = None, pack: str = "P4000-1",
            sat: tuple[float, float] | None = None, gains: Mapping[str, Mapping[str, float]] | None = None,
            plots: bool = True, log_plan: Mapping[str, Any] | None = None,
            applied: Sequence[str] | None = None) -> Path:
    """Write one flight's debrief folder and its history line; returns the folder.

    log_plan: the plan this flight flew; next.yaml keeps it (operator picks rate and groups per flight)."""
    run_dir = Path(run_dir)
    history = read_history(run_dir)
    prev = history[-1] if history else None
    flight_id = flight_id or Path(session_dir).name
    n = flight_number(flight_id, history)
    args = dict(scenario_args or {})
    flown, not_applied = flown_changes(prev, applied, current_gains(history) if gains is None else gains)
    gains = gains if gains is not None else current_gains(history + [{"flown_changes": flown}])
    series = read_telemetry(session_dir)
    m = measure(series, args.get("z"), sat)
    found = findings(m, gains)
    verdicts = judge_changes(flown, m)
    nxt = propose_next(args, found, verdicts)
    for c in nxt["changes"]:
        c.setdefault("metric", next((f["metric"] for f in found if f["change"] and f["change"]["loop"] == c["loop"]
                                     and f["change"]["gain"] == c["gain"]), None))
    out = run_dir / f"{n:02d}_{flight_id}"
    out.mkdir(parents=True, exist_ok=True)
    d = {"n": n, "flight_id": flight_id, "session": str(session_dir), "scenario": scenario, "scenario_args": args,
         "metrics": m, "findings": found, "verdicts": verdicts, "not_applied": not_applied, "next": nxt, "gains": gains,
         "previous": {"flight_id": prev["flight_id"], "metrics": prev["metrics"]} if prev else None}
    if plots:
        plot_flight(series, out / "plots" / "tracking.png", f"{flight_id} {scenario}", hold_window(series))
        plot_analysis(series, m, out / "plots" / "analysis.png", sat)
    plan = dict(log_plan or {})
    yaml_text = next_campaign_yaml(run_dir.name, n + 1, nxt, pack, tuple(plan.get("groups") or DEFAULT_LOG_GROUPS),
                                   plan.get("rate_hz") or 50)
    if yaml_text:
        (out / "next.yaml").write_text(yaml_text, encoding="utf-8")
    (out / "debrief.json").write_text(json.dumps(d, indent=2, default=str), encoding="utf-8")
    (out / "debrief.md").write_text(render(d), encoding="utf-8")
    line = {"n": n, "flight_id": flight_id, "session": str(session_dir), "scenario_args": args,
            "metrics": {k: v for k, v in m.items() if k not in ("timeline",)},
            "findings": [f["id"] for f in found],
            "flown_changes": [{k: c[k] for k in ("loop", "gain", "from", "to")} for c in flown],
            "next_changes": [{**{k: c[k] for k in ("loop", "gain", "from", "to")}, "metric": c.get("metric"),
                              "before": _get(m, c.get("metric"))} for c in nxt["changes"]]}
    with (run_dir / "history.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, default=str) + "\n")
    return out


def _from_outputs(outputs_dir: Path) -> tuple[str, str, str, dict[str, Any], dict[str, Any]]:
    """(session, flight_id, scenario, args, log_plan) of the last flight in a workflow B campaign outputs folder."""
    from ground_station.service.campaign_schema import load_campaign

    metrics = json.loads((outputs_dir / "metrics.json").read_text(encoding="utf-8"))
    flight = metrics["flights"][-1]
    camp = load_campaign(outputs_dir / "campaign.yaml")
    exp = next(e for e in camp.experiments if e.name == flight["experiment"])
    scen = exp.scenario.name if exp.scenario else "hover"
    return flight["recording"], flight["flight_id"], scen, dict(exp.scenario_args or {}), dict(exp.log_plan or {})


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("source", help="a recorder session dir (telemetry.csv) or a campaign outputs dir (metrics.json)")
    p.add_argument("--run", required=True, help="workflow C run folder, e.g. logs/workflow-c/20261005-2000")
    p.add_argument("--scenario", default="hover")
    p.add_argument("--args", default="{}", help='scenario args as JSON, e.g. \'{"z": 0.5, "hold_s": 20}\'')
    p.add_argument("--pack", default="P4000-1")
    p.add_argument("--no-sat", action="store_true", help="skip the bench saturation limits")
    p.add_argument("--applied", action="append", default=[], metavar="LOOP.GAIN[=VALUE]",
                   help="a gain change actually written (run_plan finished) before this flight; repeatable. "
                        "Without it no proposed change is credited")
    a = p.parse_args(argv)
    src = Path(a.source)
    flight_id, scen, args, plan = None, a.scenario, json.loads(a.args), None
    if (src / "metrics.json").is_file():
        session, flight_id, scen, from_camp, plan = _from_outputs(src)
        args = args or from_camp
        src = Path(session)
    sat = None
    if not a.no_sat:
        try:
            from ground_station.service.campaign_live import _sat_hi_lo
            sat = _sat_hi_lo()
        except Exception:
            sat = None
    out = debrief(src, a.run, flight_id=flight_id, scenario=scen, scenario_args=args, pack=a.pack, sat=sat,
                  log_plan=plan, applied=a.applied)
    print(f"debrief: {out / 'debrief.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
