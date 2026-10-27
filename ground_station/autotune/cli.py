"""Auto-tune and multi-loop SysID: identify rate/angle/vel/pos plants, propose PID gains, or verify them.

    python -m ground_station.autotune.cli <session-or-csv> [more logs] --axis roll|pitch|yaw|x|y|z [--loop rate|angle|vel|pos]
    python -m ground_station.autotune.cli <verify-session> --axis roll --verify <proposal.json>

Propose: prints the FRF fit, the margins of the current / ideal / proposed gains and the exact knobs (CMD 0x01
PID_GAIN symbol, idx, value), writes autotune_<axis>_<loop>.json and model_<loop>_<axis>.json, and saves
diagnostic figures when --out is a directory or --plot-dir is given. Exit 0 proposed, 2 refused.
Verify: re-measures the plant under the proposed gains and its pre-excite hover error; exit 0 keep, 3 revert.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from ground_station.analysis.controller_descriptor import PID_AXES, PID_GAIN_CMD, PID_GAINS
from ground_station.autotune import excitation as ex
from ground_station.autotune import frf as fr
from ground_station.autotune.design import (
    Design, Margins, PidRow, Spec, angle_loop, design_angle, design_outer, design_rate, margins, rate_loop,
    read_pid_rows,
)
from ground_station.autotune.plots import save_diagnostics

HOVER_RMS_MAX_RATIO = 1.1  # PROPOSED (WP-25 F3): verify hover rate-error RMS <= 1.1 x the ID flight's


def knobs(member: str, new: Mapping[str, float], prev: Mapping[str, float]) -> list[dict[str, Any]]:
    """CMD 0x01 writes (idx = axis * 3 + gain, controller_descriptor.PID_AXES) for every gain that changes."""
    if member not in PID_AXES:
        return []
    axis = PID_AXES.index(member)
    return [{"symbol": f"{member}.{g}", "value": new[g], "prev": prev[g], "cmd_id": PID_GAIN_CMD,
             "idx": axis * 3 + PID_GAINS.index(g)} for g in PID_GAINS if new[g] != prev[g]]


def _gains(text: str | None, base: PidRow) -> PidRow:
    if not text:
        return base
    kp, ki, kd = (float(v) for v in text.split(","))
    return replace(base, kp=kp, ki=ki, kd=kd)


def _m(m: Margins | None) -> str:
    if m is None:
        return "-"
    gm = f"{m.gm_db:.1f} dB" if math.isfinite(m.gm_db) else "inf"
    return f"PM {m.pm_deg:5.1f} deg  GM {gm:>8}  wc {m.wc_rps:6.1f} rad/s  max|S| {m.s_max:.2f}"


def _design_band(fit: fr.PlantFit, flown: PidRow, dt: float) -> tuple[float, float] | None:
    """Hz band the margins of the flown loop are read in: 0.25 x its crossover up to the larger of 4 x crossover and
    1.25 x its phase crossover. None (judge the full band) when the fit has no positive gain or no crossover."""
    if not fit.plant.k > 0:
        return None
    w = fr.np.geomspace(0.05, 0.95 * math.pi / dt, 2000)
    mag, ph = rate_loop(fit.plant, flown, w=w, dt=dt)
    wc = margins(mag, ph, w).wc_rps
    if not (math.isfinite(wc) and wc > 0):
        return None
    hi = 4.0 * wc
    below = fr.np.nonzero(ph <= -180.0)[0]
    if len(below):
        hi = max(hi, 1.25 * float(w[below[0]]))
    return 0.25 * wc / (2 * math.pi), hi / (2 * math.pi)


def _identify(args: argparse.Namespace, flown: PidRow, excite: Mapping[str, Any],
              id_loop: str = "rate") -> dict[str, Any]:
    """Load logs, IV FRF, fit. Returns {'series', 'runs', 'frf', 'fit', 'problem', 'baseline', 'psd', 'zoh'}."""
    all_series = [fr.load_series(src) for src in args.logs]
    dt = fr.loop_dt(id_loop, args.axis)
    excitations = getattr(args, "excitations", None) or [excite]
    # The ID-frame reader selects one contiguous run. Align each saved band independently
    # against core streams when a flight contains multiple different excitations.
    extract_series = [{k: v for k, v in s.items() if not k.startswith("id.")} for s in all_series] if len(excitations) > 1 else all_series
    runs = [fr.extract(s, args.axis, cfg, loop=id_loop) for s in extract_series for cfg in excitations]
    use_zoh = bool(getattr(args, "correct_zoh", False)) or (id_loop != "rate") or (excite["f1"] > 20.0)
    frf = fr.plant_frf(runs, flown, (excite["f0"], excite["f1"]), dt=dt, correct_zoh=use_zoh)
    fit = None
    try:
        fit = fr.fit_plant(frf, allow_drag=bool(getattr(args, "fit_drag", False)))
    except fr.IdError as exc:
        problem = str(exc)
    else:
        band = _design_band(fit, flown, dt)
        problem = fr.quality_problem(frf, fit, design_band_hz=band)
        if band is not None:
            res, n = fr.band_residual(frf, fit, band)
            print(f"design band {band[0]:.2g}-{band[1]:.2g} Hz (flown loop crossover / phase crossover): "
                  f"residual {res:.3f} on {n} bins")
    starts = []
    for axis in (fr.RATE_PID if id_loop == "rate" else [args.axis]):
        try:
            t0, corr, *_ = fr.find_start(all_series[0], axis, excitations[0], loop=id_loop)
        except fr.IdError:
            continue
        if corr >= fr.ALIGN_MIN_CORR:
            starts.append(t0)
    rms = fr.hover_rate_rms(all_series[0], args.axis, min(starts + [runs[0].t0_s]), loop=id_loop)
    psd_tuple = fr.psd_check(all_series[0], args.axis, loop=id_loop, band=(excite["f0"], excite["f1"]))
    zoh = fr.zoh_info(runs[0].fs, fit.plant.delay_s if fit else 0.0)
    print(f"{args.axis} {args.loop}: {len(runs)} run(s), {runs[0].source}, fs {runs[0].fs:g} Hz, "
          f"start {', '.join(f'{r.t0_s:.2f}' for r in runs)} s, excite {dict(excite)}")
    print(f"FRF: {int(frf.keep.sum())} bins with coherence >= {fr.COH_MIN} in {frf.band[0]:g}-{frf.band[1]:g} Hz, "
          f"band coverage {frf.coverage:.0%}, {frf.n_avg} Welch averages")
    if fit is not None:
        p = fit.plant
        drag_s = f", drag {p.drag:.3f} 1/s" if p.drag > 0 else ""
        print(f"fit: k {p.k:.4g} (deg/s^2 per mixer unit), tau {p.tau_s * 1000:.1f} ms, delay {p.delay_s * 1000:.1f} "
              f"ms{drag_s}, relative residual {fit.rel_residual:.3f} on {fit.n_bins} bins")
    print(f"pre-excite hover rate-error RMS: {'-' if rms is None else f'{rms:.2f} deg/s'}")
    return {"series": all_series[0], "runs": runs, "frf": frf, "fit": fit, "problem": problem,
            "baseline": rms, "psd": psd_tuple, "zoh": zoh}


def _id_dict(args: argparse.Namespace, ident: Mapping[str, Any], excite: Mapping[str, Any]) -> dict[str, Any]:
    fit = ident["fit"]
    fit_d = None
    if fit is not None:
        fit_d = {"k": fit.plant.k, "tau_s": fit.plant.tau_s, "delay_s": fit.plant.delay_s,
                 "drag": fit.plant.drag, "rel_residual": fit.rel_residual, "bins": fit.n_bins}
    return {"axis": args.axis, "loop": args.loop, "logs": [str(p) for p in args.logs], "excite": dict(excite),
            "source": ident["runs"][0].source, "starts_s": [r.t0_s for r in ident["runs"]],
            "frf": {"bins": int(ident["frf"].keep.sum()), "coverage": ident["frf"].coverage,
                    "n_avg": ident["frf"].n_avg,
                    "median_fit_coherence": float(fr.np.median(ident["frf"].coh[ident["frf"].keep]))
                    if ident["frf"].keep.any() else 0.0},
            "excitations": list(getattr(args, "excitations", None) or [dict(excite)]),
            "fit": fit_d,
            "zoh": ident.get("zoh"),
            "psd_check": ident["psd"][2] if ident.get("psd") else None,
            "baseline": {"hover_rate_rms_dps": ident["baseline"]}}


def _out_path(args: argparse.Namespace, suffix: str) -> Path:
    name = f"autotune_{args.axis}_{args.loop}{suffix}.json"
    if args.out:
        p = Path(args.out)
        if p.is_dir() or p.suffix.lower() != ".json":
            p.mkdir(parents=True, exist_ok=True)
            return p / name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    first = Path(args.logs[0])
    return first / name if first.is_dir() else first.with_name(f"{first.stem}_{name}")


def _model_path(args: argparse.Namespace, out_json: Path) -> Path:
    return out_json.parent / f"model_{args.loop}_{args.axis}.json"


def _write(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=float), encoding="utf-8")
    print(f"wrote {path}")


def _load_inner_wc(paths: list[str] | None) -> tuple[list[str], float | None]:
    if not paths:
        return [], None
    wcs = []
    for p in paths:
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8"))
            m = d.get("margins", {})
            wc = (m.get("proposed") or m.get("current") or m.get("rate") or {}).get("wc_rps")
            if wc and wc > 0:
                wcs.append(float(wc))
        except Exception:
            continue
    return [str(p) for p in paths], (min(wcs) if wcs else None)


def propose(args: argparse.Namespace, rows: Mapping[str, PidRow], excite: Mapping[str, Any]) -> int:
    member = fr.pid_member(args.loop, args.axis)
    dt = fr.loop_dt(args.loop, args.axis)
    cur = _gains(args.angle_gains if args.loop == "angle" else args.rate_gains, rows[member])
    rate_member = fr.RATE_PID.get(args.axis, member)
    flown_rate = _gains(args.rate_gains, rows[rate_member])
    inner_files, inner_wc = _load_inner_wc(getattr(args, "inner_model", None))

    id_loop = args.loop
    flown = cur
    if args.loop == "angle":
        try:
            s0 = fr.load_series(args.logs[0])
            _, corr_ang, *_ = fr.find_start(s0, args.axis, excite, loop="angle")
        except Exception:
            corr_ang = 0.0
        if corr_ang < fr.ALIGN_MIN_CORR and args.axis in fr.RATE_PID:
            id_loop = "rate"
            flown = flown_rate

    raw_series = None
    psd_tuple: tuple[Any, Any, dict[str, Any]] = (fr.np.zeros(0), fr.np.zeros(0), {"available": False})
    try:
        raw_series = fr.load_series(args.logs[0])
        psd_tuple = fr.psd_check(raw_series, args.axis, loop=id_loop, band=(excite["f0"], excite["f1"]))
    except Exception:
        pass

    try:
        ident = _identify(args, flown, excite, id_loop=id_loop)
    except fr.IdError as exc:
        ident, d = None, Design("refused", str(exc), current=cur, dt_s=dt)
    else:
        if ident["problem"]:
            m_cur = (margins(*rate_loop(ident["fit"].plant, cur, dt=dt))
                     if (ident["fit"] is not None and ident["fit"].plant.k > 0) else None)
            d = Design("refused", ident["problem"], current=cur,
                       margins={"current": m_cur} if m_cur else None, dt_s=dt)
        elif args.loop == "rate":
            d = design_rate(ident["fit"].plant, cur)
        elif args.loop == "angle" and id_loop == "rate":
            d = design_angle(ident["fit"].plant, flown_rate, cur)
        else:
            d = design_outer(args.loop, ident["fit"].plant, cur, dt=dt, inner_wc_rps=inner_wc)

    for name in ("current", "ideal", "proposed"):
        row = getattr(d, name)
        if row is not None:
            g = ", ".join(f"{k} {v:.4g}" for k, v in row.gains().items())
            c = ", ".join(f"{k} {v:.4g}" for k, v in row.continuous(dt).items())
            print(f"{name:9s} fw [{g}]  cont [{c}]  {_m((d.margins or {}).get(name))}")

    out = {**(_id_dict(args, ident, excite) if ident else {"axis": args.axis, "loop": args.loop,
                                                           "psd_check": psd_tuple[2]}),
           "member": member, "rate_gains": flown_rate.gains(), "gains_in_force": flown.gains(),
           "inner_models": inner_files, **d.to_dict(), "knobs": []}
    out_json = _out_path(args, "")
    model_json = _model_path(args, out_json)

    plot_dirs: list[Path] = []
    if args.out and (Path(args.out).is_dir() or Path(args.out).suffix.lower() != ".json"):
        plot_dirs.append(Path(args.out))
    if getattr(args, "plot_dir", None):
        for pd_str in args.plot_dir:
            plot_dirs.append(Path(pd_str))
    stem = getattr(args, "stem", None) or (Path(args.out).stem if args.out and Path(args.out).suffix.lower() != ".json"
                                           else f"{args.loop}_{args.axis}")
    for pdir in plot_dirs:
        save_diagnostics(pdir, stem, args.axis, args.loop, ident, d,
                         ident["psd"] if ident else psd_tuple, series=raw_series, dt_s=dt)

    if d.status != "refused":
        out["knobs"] = knobs(member, d.proposed.gains(), cur.gains())
    _write(out_json, out)
    _write(model_json, out)

    if d.status != "proposed":
        print(f"REFUSED: {d.reason}")
        return 2
    if d.note:
        print(f"note: {d.note}")
    print("knobs to write (CMD 0x01 PID_GAIN):" if out["knobs"] else "no change: current gains are the proposal")
    for k in out["knobs"]:
        print(f"  {k['symbol']:12s} = {k['value']:<8.4g} (was {k['prev']:.4g}; cmd 0x{k['cmd_id']:02X} idx {k['idx']})")
    if getattr(args, "print_gain_write", False) and d.proposed is not None:
        pg = d.proposed.gains()
        print(f"PROPOSED gain-write command (not sent): --{args.loop}-gains {pg['Kp']:g},{pg['Ki']:g},{pg['Kd']:g}")
    return 0


def verify(args: argparse.Namespace, rows: Mapping[str, PidRow], excite: Mapping[str, Any]) -> int:
    prev = json.loads(Path(args.verify).read_text(encoding="utf-8"))
    if prev.get("status") != "proposed" or prev.get("axis") != args.axis:
        print(f"ERROR: {args.verify} is not a proposal for axis {args.axis}")
        return 1
    args.loop = prev["loop"]
    member = prev["member"]
    spec = Spec()
    new = _gains(",".join(str(prev["proposed"][g]) for g in PID_GAINS), rows[member])
    rate_member = fr.RATE_PID.get(args.axis, member)
    flown = new if args.loop == "rate" else _gains(",".join(str(prev["rate_gains"][g]) for g in PID_GAINS),
                                                   rows[rate_member])
    fails = []
    try:
        ident = _identify(args, flown, excite, id_loop="rate" if args.loop in ("rate", "angle") else args.loop)
    except fr.IdError as exc:
        ident = None
        fails.append(f"cannot re-measure: {exc}")
    if ident is not None and ident["problem"]:
        fails.append(f"cannot re-measure: {ident['problem']}")
    elif ident is not None:
        plant = ident["fit"].plant
        m = margins(*(rate_loop(plant, new) if args.loop == "rate" else angle_loop(plant, flown, new)))
        print(f"re-measured {_m(m)}")
        pm_min = spec.pm_min_deg if args.loop == "rate" else spec.angle_pm_min_deg
        if not m.meets(pm_min, spec.gm_min_db, spec.s_max):
            fails.append(f"margins below spec (PM >= {pm_min}, GM >= {spec.gm_min_db} dB, max|S| <= {spec.s_max})")
        base, rms = prev.get("baseline", {}).get("hover_rate_rms_dps"), ident["baseline"]
        if base and rms and rms > HOVER_RMS_MAX_RATIO * base:
            fails.append(f"hover rate-error RMS {rms:.2f} > {HOVER_RMS_MAX_RATIO} x baseline {base:.2f} deg/s")
    out = {"proposal": str(args.verify), "logs": [str(p) for p in args.logs], "fails": fails,
           "decision": "revert" if fails else "keep"}
    if fails:
        out["knobs"] = [{**k, "value": k["prev"], "prev": k["value"]} for k in prev.get("knobs", [])]
        print("VERIFY FAIL -> REVERT to G_prev:\n  " + "\n  ".join(fails))
        for k in out["knobs"]:
            print(f"  {k['symbol']:12s} = {k['value']:<8.4g} (cmd 0x{k['cmd_id']:02X} idx {k['idx']})")
    else:
        print(f"VERIFY PASS -> keep {', '.join(f'{k}={v:.4g}' for k, v in prev['proposed'].items())}")
    _write(_out_path(args, "_verify"), out)
    return 3 if fails else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("logs", nargs="+", type=Path, help="session dir(s) or CSV(s) of excite flights")
    ap.add_argument("--axis", required=True, choices=fr.ALL_AXES)
    ap.add_argument("--loop", choices=("rate", "angle", "vel", "pos"), default="rate")
    ap.add_argument("--rate-gains", help="Kp,Ki_fw,Kd_fw of the PID that flew (default API/pid.c)")
    ap.add_argument("--angle-gains", help="Kp,Ki_fw,Kd_fw of the current angle PID (default API/pid.c)")
    ap.add_argument("--flight-file", type=Path, help="saved .fly file: use its matching excitation band(s)")
    ap.add_argument("--inner-model", nargs="*", help="inner-loop model_<loop>_<axis>.json file(s) measured on")
    ap.add_argument("--verify", metavar="PROPOSAL_JSON", help="verify a proposal on this (verify-flight) log")
    ap.add_argument("--signal", choices=sorted(ex.SIGNALS))
    for k in ("f0", "f1", "amp", "duration"):
        ap.add_argument(f"--{k}", type=float, help="excite parameter, default the campaign's")
    ap.add_argument("--out", help="JSON path or output directory (default next to the first log)")
    ap.add_argument("--plot-dir", nargs="*", help="additional directory(s) to write diagnostic PNG/CSV/caption files")
    ap.add_argument("--stem", help="filename stem for diagnostic plots (default <loop>_<axis>)")
    ap.add_argument("--correct-zoh", action="store_true", help="de-embed sample-and-hold ZOH from FRF before fitting")
    ap.add_argument("--print-gain-write", action="store_true", help="print PROPOSED gain-write command (never sent)")
    args = ap.parse_args(argv)
    default_ex = ex.VERIFY_EXCITE if args.verify else ex.LOOP_EXCITE.get(args.loop, ex.ID_EXCITE)
    excite = dict(default_ex)
    excite.update({k: v for k, v in (("signal", args.signal), ("f0", args.f0), ("f1", args.f1), ("amp", args.amp),
                                     ("duration_s", args.duration)) if v is not None})
    if args.flight_file:
        from ground_station.service.fly_file import compile_fly
        campaign, _ = compile_fly(args.flight_file.read_text(encoding="utf-8"))
        axis = {"x": "pitch", "y": "roll"}.get(args.axis, args.axis)
        args.excitations = []
        for experiment in campaign["experiments"]:
            for step in experiment["scenario"]["steps"]:
                cfg = step.get("excite") if isinstance(step, dict) else None
                if cfg and cfg.get("axis") == axis and cfg.get("target", "rate") == args.loop:
                    params = {k: cfg[k] for k in ("signal", "f0", "f1", "amp", "duration_s")}
                    if params not in args.excitations:
                        args.excitations.append(params)
        if not args.excitations:
            ap.error(f"no {args.loop}/{args.axis} excitation in {args.flight_file}")
        excite = dict(args.excitations[0])
        excite.update(f0=min(c["f0"] for c in args.excitations), f1=max(c["f1"] for c in args.excitations))
    rows = read_pid_rows()
    return verify(args, rows, excite) if args.verify else propose(args, rows, excite)


if __name__ == "__main__":
    sys.exit(main())
