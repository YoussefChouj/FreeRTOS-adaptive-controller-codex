"""Autotune over the log corpus: the CLI verdict on every log, and the same FRF pipeline on natural hover excitation.

1. ``python -m ground_station.autotune.cli`` (propose, rate loop) on every log with the rate Des/FB streams, per
   axis. Each log is exported once to the CLI's wide-CSV input (t = log_corpus time base) so torn session rows
   cannot crash it. Its verdict: exit 0 proposed / 2 refused, with the printed reason.
2. Without a SysID run there is no dither, so (2) reuses autotune.frf with the rate setpoint r as its own instrument
   on the airborne spans: T = Phi_rx / Phi_rr, coherence = gamma^2(r, x), P = T / (C (1 - T)) with C the PID that
   flew (API/pid.c at the log's commit when known, else today's). r is NOT exogenous in hover (the angle loop closes
   through x), so even a coherent fit is biased toward the inverse controller; the run reports whether coherence
   alone would pass the WP-25 gates (frf.COH_MIN, BAND_COVER_MIN, FIT_MAX_REL), then fit, margins and the design.

    python -m ground_station.analysis.autotune_sweep [--root <checkout with logs/>] [--json out.json]
"""
from __future__ import annotations

import argparse
import contextlib
import collections
import io
import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from ground_station.analysis import log_corpus as lc
from ground_station.autotune import cli
from ground_station.autotune import excitation as ex
from ground_station.autotune import frf as fr
from ground_station.autotune.design import design_rate, margins, rate_loop, read_pid_rows

EDGE_S = 2.0          # cut takeoff / landing transients from each airborne span
AXES = ("roll", "pitch", "yaw")


def export_csv(series: lc.Series, path: Path) -> bool:
    """Write the rate Des/FB streams (and prim_state) as the CLI's wide CSV; False when they are missing."""
    keys = [f"Ctrler.{fr.RATE_PID[a]}.{s}" for a in AXES for s in ("Des", "FB")]
    if not all(k in series for k in keys):
        return False
    keys += [fr.PRIM_STATE] if fr.PRIM_STATE in series else []
    cols = [pd.Series(series[k][1], index=series[k][0], name=k) for k in keys]
    df = pd.concat([c[~c.index.duplicated()] for c in cols], axis=1).sort_index()
    df.index.name = "t"
    df.to_csv(path)
    return True


def cli_verdict(csv: Path, axis: str, work: Path) -> dict:
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            code = cli.main([str(csv), "--axis", axis, "--out", str(work / "autotune.json")])
    except Exception as exc:  # noqa: BLE001 - a CLI crash is a verdict to report, not a reason to stop the sweep
        return dict(code="crash", reason=f"{type(exc).__name__}: {exc}")
    out = buf.getvalue().splitlines()
    reason = next((ln[len("REFUSED: "):] for ln in out if ln.startswith("REFUSED: ")), "")
    return dict(code=code, reason=reason)


def hover_runs(series: lc.Series, axis: str) -> list[fr.IdRun]:
    """One IdRun per airborne span (EDGE_S trimmed) with d = r: Des/FB on the log's own sample grid."""
    des, fb = (f"Ctrler.{fr.RATE_PID[axis]}.{s}" for s in ("Des", "FB"))
    td = series[des][0]
    dt = np.median(np.diff(np.unique(td))) if len(td) > 3 else 0.0     # host-time sessions repeat stamps
    if not dt > 0:
        return []
    fs = float(round(1.0 / dt))
    t = np.arange(td[0], td[-1], 1.0 / fs)
    runs = []
    for a, b in lc.spans(lc.airborne(series, t), t, 2 * EDGE_S + 64 / fs):
        g = np.arange(a + EDGE_S, b - EDGE_S, 1.0 / fs)
        ok_d, ok_f = np.isfinite(series[des][1]), np.isfinite(series[fb][1])
        r = np.interp(g, series[des][0][ok_d], series[des][1][ok_d])
        x = np.interp(g, series[fb][0][ok_f], series[fb][1][ok_f])
        runs.append(fr.IdRun(fs, r - r.mean(), r - r.mean(), x - x.mean(), float(a), "hover, r as instrument"))
    return runs


def natural_id(series: lc.Series, axis: str, flown) -> dict:
    runs = hover_runs(series, axis)
    if not runs:
        return dict(status="no airborne span")
    fs = runs[0].fs
    band = (ex.ID_EXCITE["f0"], min(ex.ID_EXCITE["f1"], 0.45 * fs))
    try:
        frf = fr.plant_frf(runs, flown, band)
    except fr.IdError as exc:
        return dict(status="refused", reason=str(exc))
    res = dict(fs=fs, seconds=float(sum(len(r.x) for r in runs) / fs), band=list(band), coverage=frf.coverage,
               bins=int(frf.keep.sum()), coh_med=float(np.median(frf.coh[(frf.f_hz >= band[0]) & (frf.f_hz <= band[1])])))
    fit = None
    try:
        fit = fr.fit_plant(frf)
    except fr.IdError as exc:
        res.update(status="refused", reason=str(exc))
        return res
    res["fit"] = dict(k=fit.plant.k, tau_ms=fit.plant.tau_s * 1e3, delay_ms=fit.plant.delay_s * 1e3,
                      rel_residual=fit.rel_residual)
    problem = fr.quality_problem(frf, fit)
    if fit.plant.k > 0:
        m = margins(*rate_loop(fit.plant, flown))
        res["flown_margins"] = dict(pm_deg=m.pm_deg, gm_db=m.gm_db, wc_rps=m.wc_rps)
    if problem:
        res.update(status="refused", reason=problem)
        return res
    d = design_rate(fit.plant, flown)
    res.update(status=d.status, reason=d.reason, flown=flown.gains(),
               proposed=d.proposed.gains() if d.proposed else None)
    return res


def analyze(series: lc.Series, rows: dict, work: Path) -> dict:
    out = {}
    csv = work / "log.csv"
    have = export_csv(series, csv)
    for axis in AXES:
        r = dict(cli=cli_verdict(csv, axis, work) if have else None)
        if have:
            r["natural"] = natural_id(series, axis, rows[fr.RATE_PID[axis]])
        out[axis] = r
    return out


def to_markdown(results: dict) -> str:
    lines = ["| log | axis | gains | CLI | natural: s / coh med / coverage / bins | fit k, tau ms, delay ms, resid | "
             "flown PM / GM / wc | verdict |", "|---|---|---|---|---|---|---|---|"]
    for log, r in results.items():
        for axis in AXES:
            a = r["axes"].get(axis, {})
            c, n = a.get("cli"), a.get("natural") or {}
            if not c or "seconds" not in n and n.get("status") == "no airborne span":
                continue
            fit, fm = n.get("fit"), n.get("flown_margins")
            lines.append(
                f"| {log} | {axis} | {r['gains']} | {c['code']} | "
                + (f"{n['seconds']:.0f} / {n['coh_med']:.2f} / {n['coverage']:.1f} / {n['bins']} | " if "coverage" in n
                   else "- | ")
                + (f"{fit['k']:.0f}, {fit['tau_ms']:.0f}, {fit['delay_ms']:.0f}, {fit['rel_residual']:.2f} | " if fit else "- | ")
                + (f"{fm['pm_deg']:.0f} / {fm['gm_db']:.1f} / {fm['wc_rps']:.0f} | " if fm else "- | ")
                + f"{n.get('status', '-')}: {n.get('reason', '')[:70]} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(lc.REPO))
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    need = {f"Ctrler.{fr.RATE_PID[a]}.{s}" for a in AXES for s in ("Des", "FB")}
    today = read_pid_rows()
    results, sysid = {}, 0
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        for ref in lc.find_logs(args.root):
            k = lc.keys(ref)
            sysid += bool({"id.sample_counter", "id.sysid_state"} & k)
            if not need <= k:
                continue
            series = lc.load(ref)
            rows = lc.pid_rows_at(lc.log_commit(ref, args.root))
            results[ref.name] = dict(gains="commit" if rows else "today", axes=analyze(series, rows or today, work))
    print(f"logs with a 0x03 ID frame / sysid_state: {sysid}")
    verdicts = collections.Counter((a["cli"]["code"], a["cli"]["reason"].split(":")[0][:60])
                                   for r in results.values() for a in r["axes"].values() if a.get("cli"))
    print(f"CLI runs on {len(results)} logs x {len(AXES)} axes:", dict(verdicts.most_common(6)))
    print(to_markdown(results))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1, default=float), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
