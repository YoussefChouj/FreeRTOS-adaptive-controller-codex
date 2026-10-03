"""Workflow B campaign outputs: one folder per campaign run, written when the runner returns.

    logs/campaigns/<campaign>_<YYYYmmdd-HHMMSS>/
        summary.md             flight table and campaign status (the file the agent sends the operator)
        metrics.json           per-flight hover metrics; the hold window is g_wfb_status.prim_state == HOVER
        campaign.yaml          the campaign file that was flown
        log_plan.json          per-experiment plan_capture() plans and their subscribe steps
        plots/<flight_id>.png  x / y / z against their references, attitude, motors
        flights/<flight_id>/   flightlab report of the recording (it also upserts docs/flights/ledger.csv)

Each flight's raw log stays in its recorder session (FlightRecord.recording, ``logs/sessions/<stamp>-<label>``,
telemetry.csv rows ``received_ns,slot,key,value`` with keys ``slot<N>.<symbol>``).
"""

from __future__ import annotations

import bisect
import csv
import json
import math
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from ground_station.livewatch.campaign_capture import ATTITUDE, KF_HEALTH, MOTORS, POSITION_AXES
from ground_station.service.campaign_logplan import campaign_log_plans
from ground_station.service.campaign_runner import PRIM_HOVER, CampaignReport
from ground_station.service.campaign_schema import Campaign, load_campaign

PRIM_STATE = "g_wfb_status.prim_state"
SAFETY_TRIP = "g_wfb_status.safety_trip"
AXIS_NAMES = ("x", "y", "z")
_SLOT_PREFIX = re.compile(r"^slot\d+\.")

Series = dict[str, tuple[list[float], list[float]]]


def read_telemetry(session_dir: str | Path) -> Series:
    """``{symbol: (t_s, values)}`` from a session's telemetry.csv; t_s counts from the first row."""
    series: Series = {}
    t0 = None
    with (Path(session_dir) / "telemetry.csv").open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                ns, value = int(row["received_ns"]), float(row["value"])
            except (KeyError, TypeError, ValueError):
                continue
            t0 = ns if t0 is None else t0
            ts, vs = series.setdefault(_SLOT_PREFIX.sub("", row.get("key") or ""), ([], []))
            ts.append((ns - t0) / 1e9)
            vs.append(value)
    return series


def _at(series: tuple[list[float], list[float]], t: float) -> float:
    """Newest value at or before ``t`` (the first value before the series starts)."""
    ts, vs = series
    return vs[max(0, bisect.bisect_right(ts, t) - 1)]


def hold_window(series: Series) -> tuple[float, float] | None:
    """First and last sample time with prim_state HOVER, or None if the drone never reached it."""
    ts, vs = series.get(PRIM_STATE, ([], []))
    hover = [t for t, v in zip(ts, vs) if int(round(v)) == PRIM_HOVER]
    return (hover[0], hover[-1]) if hover else None


def _rms(xs: list[float]) -> float | None:
    return math.sqrt(sum(x * x for x in xs) / len(xs)) if xs else None


def _r(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(x, nd)


def flight_metrics(series: Series, target_z_m: float | None = None,
                   sat: tuple[float, float] | None = None) -> dict[str, Any]:
    """Hover-hold quality over the prim_state HOVER window: position error, attitude, motor headroom."""
    out: dict[str, Any] = {"hold_s": 0.0}
    window = hold_window(series)
    if window is None:
        out["note"] = "never reached HOVER (no prim_state 2 sample)"
        return out
    t0, t1 = window
    out["hold_s"] = round(t1 - t0, 2)
    for name, axis in zip(AXIS_NAMES, POSITION_AXES):
        fb, ref = series.get(axis.feedback), series.get(axis.reference)
        if not fb or not ref:
            continue
        pts = [(t, v) for t, v in zip(*fb) if t0 <= t <= t1]
        err = [(v - _at(ref, t)) * axis.to_m for t, v in pts]
        pos = [v * axis.to_m for _, v in pts]
        out[name] = {"err_rms_m": _r(_rms(err)), "err_max_m": _r(max((abs(e) for e in err), default=None)),
                     "mean_m": _r(sum(pos) / len(pos)) if pos else None}
    if target_z_m is not None and out.get("z", {}).get("mean_m") is not None:
        out["z"]["target_m"] = target_z_m
        out["z"]["mean_minus_target_m"] = _r(out["z"]["mean_m"] - target_z_m)
    for sym, label in zip(ATTITUDE[:2], ("roll", "pitch")):
        vals = [v for t, v in zip(*series.get(sym, ([], []))) if t0 <= t <= t1]
        if vals:
            out[label] = {"rms_deg": _r(_rms(vals), 2), "max_abs_deg": _r(max(abs(v) for v in vals), 2)}
    motors = [series[m] for m in MOTORS if m in series]
    if motors:
        ts = [t for t in motors[0][0] if t0 <= t <= t1]
        rows = [[_at(m, t) for m in motors] for t in ts]
        if rows:
            out["motors"] = {"max": max(max(r) for r in rows), "mean": _r(sum(map(sum, rows)) / (4 * len(rows)), 1)}
            if sat is not None:
                hi, lo = sat
                n_sat = sum(any(v >= hi or v <= lo for v in r) for r in rows)
                out["motors"]["sat_frac"] = _r(n_sat / len(rows))
    kf = series.get(KF_HEALTH, ([], []))[1]
    out["kf_health_min"] = min(kf) if kf else None
    trip = series.get(SAFETY_TRIP, ([], []))[1]
    out["safety_trip_max"] = max(trip) if trip else None
    ps = series.get(PRIM_STATE, ([], []))[0]
    out["status_rate_hz"] = _r((len(ps) - 1) / (ps[-1] - ps[0]), 1) if len(ps) > 1 and ps[-1] > ps[0] else None
    return out


def plot_flight(series: Series, path: Path, title: str, window: tuple[float, float] | None) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(5, 1, figsize=(10, 12), sharex=True)
    for ax, name, axis in zip(axes, AXIS_NAMES, POSITION_AXES):
        for sym, style, lab in ((axis.feedback, "-", "feedback"), (axis.reference, "--", "reference")):
            if sym in series:
                ts, vs = series[sym]
                ax.plot(ts, [v * axis.to_m for v in vs], style, label=lab)
        ax.set_ylabel(f"{name} m")
        ax.legend(loc="upper right", fontsize=8)
    for sym in ATTITUDE[:2]:
        if sym in series:
            axes[3].plot(*series[sym], label=sym)
    axes[3].set_ylabel("deg")
    axes[3].legend(loc="upper right", fontsize=8)
    for sym in MOTORS:
        if sym in series:
            axes[4].plot(*series[sym], label=sym, linewidth=0.8)
    axes[4].set_ylabel("motor cmd")
    axes[4].legend(loc="upper right", fontsize=8)
    axes[4].set_xlabel("t s (from recording start)")
    if window:
        for ax in axes:
            ax.axvspan(*window, color="0.9", zorder=0)
    fig.suptitle(f"{title} (grey: prim_state HOVER)")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=90)
    plt.close(fig)
    return path


def _fmt(x: Any, nd: int = 3) -> str:
    return "-" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def render_summary(campaign: Campaign, report: CampaignReport, rows: list[dict[str, Any]], stamp: str) -> str:
    lines = [f"# {campaign.campaign}: {stamp}", "", f"Objective: {campaign.objective}", "",
             f"Status: **{report.status}**{f' ({report.reason})' if report.reason else ''}, "
             f"{len(report.flights)} of {min(campaign.max_flights, sum(e.repeats for e in campaign.experiments))}"
             " flights", "",
             "| flight | experiment | outcome | hold s | z target / mean m | x / y / z RMS err m "
             "| roll / pitch RMS deg | motor max | sat | KF min |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for row in rows:
        rec, m = row["record"], row["metrics"]
        outcome = f"abort L{rec['abort_level']}: {rec['abort_reason']}" if rec["abort_level"] else "landed"
        z = m.get("z", {})
        rms = " / ".join(_fmt(m.get(a, {}).get("err_rms_m")) for a in AXIS_NAMES)
        att = " / ".join(_fmt(m.get(a, {}).get("rms_deg"), 2) for a in ("roll", "pitch"))
        mot = m.get("motors", {})
        lines.append(f"| {rec['flight_id']} | {rec['experiment']} | {outcome} | {_fmt(m.get('hold_s'), 1)} "
                     f"| {_fmt(z.get('target_m'), 2)} / {_fmt(z.get('mean_m'), 3)} | {rms} | {att} "
                     f"| {_fmt(mot.get('max'), 0)} | {_fmt(mot.get('sat_frac'))} | {_fmt(m.get('kf_health_min'), 0)} |")
    lines += ["", "## Files", ""]
    for row in rows:
        rec = row["record"]
        parts = [f"recording `{rec['recording'] or 'none'}`"]
        if row.get("plot"):
            parts.append(f"[plot](plots/{Path(row['plot']).name})")
        if row.get("report_dir"):
            parts.append(f"[flightlab report](flights/{rec['flight_id']}/)")
        if row.get("notes"):
            parts.append("; ".join(row["notes"]))
        lines.append(f"- {rec['flight_id']}: " + ", ".join(parts))
    lines += ["- campaign.yaml, log_plan.json, metrics.json in this folder"]
    return "\n".join(lines) + "\n"


def _default_analyze(session_dir: str, out_dir: Path) -> Path:
    from ground_station.analysis.flightlab.pipeline import analyze
    return analyze(session_dir, out_dir=out_dir, html=False, ledger=True).out_dir


def write_campaign_outputs(
    campaign_path: str | Path,
    report: CampaignReport,
    *,
    out_root: str | Path = "logs/campaigns",
    max_rate_hz: float | None = None,
    sat: tuple[float, float] | None = None,
    analyze_flight: Callable[[str, Path], Any] | None = _default_analyze,
    plots: bool = True,
    now: datetime | None = None,
) -> Path:
    """Write the campaign folder for one runner report; returns it. A flight without a readable recording
    still gets its summary row (outcome only); a failing plot or flightlab run is noted, not raised."""
    campaign = report.campaign if isinstance(report.campaign, Campaign) else load_campaign(campaign_path)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    out = Path(out_root) / f"{campaign.campaign}_{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(campaign_path, out / "campaign.yaml")
    (out / "log_plan.json").write_text(json.dumps(
        {"campaign": campaign.campaign, "max_rate_hz": max_rate_hz,
         "experiments": campaign_log_plans(campaign, max_rate_hz)}, indent=2), encoding="utf-8")
    if sat is None:
        try:
            from ground_station.service.campaign_live import _sat_hi_lo
            sat = _sat_hi_lo()
        except Exception:
            sat = None
    targets = {e.name: (e.scenario.hover_z_m if e.scenario else None) for e in campaign.experiments}

    rows = []
    for rec in report.flights:
        rec_d = _record_dict(rec)
        row: dict[str, Any] = {"record": rec_d, "metrics": {}, "notes": []}
        session = rec_d["recording"]
        if session and (Path(session) / "telemetry.csv").is_file():
            series = read_telemetry(session)
            row["metrics"] = flight_metrics(series, targets.get(rec_d["experiment"]), sat)
            if plots:
                try:
                    row["plot"] = str(plot_flight(series, out / "plots" / f"{rec_d['flight_id']}.png",
                                                  f"{rec_d['flight_id']} {rec_d['experiment']}", hold_window(series)))
                except Exception as exc:
                    row["notes"].append(f"plot failed: {type(exc).__name__}: {exc}")
            if analyze_flight is not None:
                try:
                    row["report_dir"] = str(analyze_flight(session, out / "flights" / rec_d["flight_id"]))
                except Exception as exc:
                    row["notes"].append(f"flightlab failed: {type(exc).__name__}: {exc}")
        else:
            row["notes"].append("no recording")
        rows.append(row)

    (out / "metrics.json").write_text(json.dumps(
        {"campaign": campaign.campaign, "stamp": stamp, "status": report.status, "reason": report.reason,
         "flights": [{**r["record"], "metrics": r["metrics"], "notes": r["notes"]} for r in rows]},
        indent=2), encoding="utf-8")
    (out / "summary.md").write_text(render_summary(campaign, report, rows, stamp), encoding="utf-8")
    return out


def _record_dict(rec: Any) -> dict[str, Any]:
    import dataclasses
    d = dataclasses.asdict(rec) if dataclasses.is_dataclass(rec) else dict(rec)
    d.setdefault("recording", "")
    return d


def summary_lines(metrics: Mapping[str, Any]) -> str:
    """One chat line per flight from metrics.json (for the runner's end-of-campaign message)."""
    out = []
    for f in metrics.get("flights", []):
        m = f.get("metrics", {})
        z = m.get("z", {})
        out.append(f"{f['flight_id']} {f['experiment']}: hold {_fmt(m.get('hold_s'), 1)} s, z mean "
                   f"{_fmt(z.get('mean_m'))} m (target {_fmt(z.get('target_m'), 2)}), xy RMS "
                   f"{_fmt(m.get('x', {}).get('err_rms_m'))}/{_fmt(m.get('y', {}).get('err_rms_m'))} m")
    return "\n".join(out)
