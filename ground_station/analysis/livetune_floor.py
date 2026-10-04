"""Noise floor of the live-tune cost J (ground_station/livetune/cost.py) on logged hover windows with the flown gains.

No logged flight has a live-tune excitation, so every window here is hover only: J_track = RMS(FB - Des) / A with
A = ExciteConfig.amp_dps measures the disturbance part of the error that every live-tune window also carries.
Windows are LiveTuneConfig.excite_s long, inside the airborne spans (log_corpus rule, EDGE_S trimmed), on the
roll / pitch rate loops the tuner excites. Spread is reported
  * within a flight (same gains by construction): std and CV of J over its windows;
  * between flights flown with equal rate gains: CV of the per-flight median J. Gains come from API/pid.c at the
    log's commit (session manifest / flight ledger) or, failing that, at the last pid.c commit before the log
    started ("inferred"; the flashed image may lag the tree).
The tuner keeps a candidate only at J_rel <= 1 - min_gain, so one window resolves that step only when the window
noise is well below min_gain * J_baseline.

    python -m ground_station.analysis.livetune_floor [--root <checkout with logs/>] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np

from ground_station.analysis import log_corpus as lc
from ground_station.livetune.cost import CostWeights, window_cost
from ground_station.livetune.loop import ExciteConfig, LiveTuneConfig

EDGE_S = 2.0
SAT_HI, SAT_LO = 3995.0, 2005.0          # sim/bench/bench.py:19, as service/campaign_live.py:_sat_hi_lo
MOTORS = [f"mymotor.motor{i}" for i in range(1, 5)]
RATE = {"roll": "gyroxPID", "pitch": "gyroyPID"}


def window_costs(series: lc.Series, axis: str, win_s: float, amp: float, weights: CostWeights = CostWeights()) -> list:
    """WindowCost of every win_s window inside the airborne spans, on the log's own samples of FB - Des."""
    des, fb = (f"Ctrler.{RATE[axis]}.{s}" for s in ("Des", "FB"))
    if des not in series or fb not in series:
        return []
    t = series[fb][0]
    e = series[fb][1] - np.interp(t, *series[des])
    sat = (sum(((lc.hold(series, m, t, 3000.0) >= SAT_HI) | (lc.hold(series, m, t, 3000.0) <= SAT_LO)).astype(float)
               for m in MOTORS) / 4.0) if all(m in series for m in MOTORS) else np.zeros(len(t))
    out = []
    for a, b in lc.spans(lc.airborne(series, t), t, 2 * EDGE_S + win_s):
        for s0 in np.arange(a + EDGE_S, b - EDGE_S - win_s + 1e-9, win_s):
            w = (t >= s0) & (t < s0 + win_s)
            wc = window_cost(t[w], e[w], sat[w], amp, weights)
            if wc is not None:
                out.append(wc)
    return out


def gains_key(ref: lc.LogRef, root: str | Path) -> tuple[str, str]:
    """(rate-gain signature, source) from pid.c at the log's commit, else at the last pid.c commit before the log."""
    commit, src = lc.log_commit(ref, root), "commit"
    if not commit and ref.kind == "vofa":
        try:
            started = json.loads(ref.path.read_text(encoding="utf-8")).get("started")
        except (OSError, ValueError):
            started = None
        if started:
            res = subprocess.run(["git", "log", "-1", "--format=%h", f"--before=@{int(started)}", "--", "API/pid.c"],
                                 cwd=str(lc.REPO), capture_output=True, text=True)
            commit, src = res.stdout.strip() or None, "inferred"
    rows = lc.pid_rows_at(commit)
    if not rows:
        return "unknown", "none"
    g = [rows[RATE[a]] for a in ("roll", "pitch")]
    return ";".join(f"{r.kp:g},{r.ki:g},{r.kd:g}" for r in g), src


def summarize(per_log: dict, min_gain: float) -> dict:
    """Within-flight and between-flight (equal gains) spread of J per axis."""
    out = {}
    for axis in RATE:
        logs = {k: v["axes"][axis] for k, v in per_log.items() if len(v["axes"].get(axis, {}).get("J", [])) >= 3}
        within = [np.std(v["J"], ddof=1) / np.mean(v["J"]) for v in logs.values()]
        groups: dict[str, list[float]] = {}
        for k, v in logs.items():
            groups.setdefault(per_log[k]["gains"], []).append(float(np.median(v["J"])))
        between = {g: float(np.std(j, ddof=1) / np.mean(j)) for g, j in groups.items() if g != "unknown" and len(j) >= 2}
        cv = float(np.median(within)) if within else float("nan")
        out[axis] = dict(logs=len(logs), windows=int(sum(len(v["J"]) for v in logs.values())),
                         J_median=float(np.median([np.median(v["J"]) for v in logs.values()])) if logs else float("nan"),
                         within_cv_median=cv, within_cv_max=float(np.max(within)) if within else float("nan"),
                         between_cv={g: (len(groups[g]), c) for g, c in between.items()},
                         windows_for_min_gain=float(np.ceil((2.0 * cv / min_gain) ** 2)) if within else float("nan"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(lc.REPO))
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    cfg, amp = LiveTuneConfig(), ExciteConfig().amp_dps
    need = {f"Ctrler.{RATE[a]}.{s}" for a in RATE for s in ("Des", "FB")} | {"flight_phase"}
    per_log = {}
    for ref in lc.find_logs(args.root):
        if not need <= lc.keys(ref):
            continue
        series = lc.load(ref)
        axes = {}
        for axis in RATE:
            wcs = window_costs(series, axis, cfg.excite_s, amp, cfg.weights)
            axes[axis] = dict(J=[w.J for w in wcs], track=[w.track for w in wcs], sat=[w.sat for w in wcs],
                              osc=[w.osc for w in wcs], fs=float(np.median([w.fs_hz for w in wcs])) if wcs else None)
        if any(axes[a]["J"] for a in RATE):
            g, src = gains_key(ref, args.root)
            per_log[ref.name] = dict(gains=g, gains_source=src, axes=axes)
            print(f"{ref.name}: gains {g} ({src}), J roll n={len(axes['roll']['J'])} "
                  f"med {np.median(axes['roll']['J']) if axes['roll']['J'] else float('nan'):.3f}", flush=True)
    summary = summarize(per_log, cfg.min_gain)
    print(json.dumps(summary, indent=1, default=float))
    if args.json:
        Path(args.json).write_text(json.dumps(dict(per_log=per_log, summary=summary), indent=1, default=float),
                                   encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
