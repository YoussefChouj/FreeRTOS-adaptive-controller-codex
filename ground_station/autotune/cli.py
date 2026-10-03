"""Auto-tune between flights: identify the rate plant from an excite log, propose PID gains, or verify them.

    python -m ground_station.autotune.cli <session-or-csv> [more logs] --axis roll|pitch|yaw [--loop rate|angle]
    python -m ground_station.autotune.cli <verify-session> --axis roll --verify <proposal.json>

Propose: prints the FRF fit, the margins of the current / ideal / proposed gains and the exact knobs (CMD 0x01
PID_GAIN symbol, idx, value), and writes autotune_<axis>_<loop>.json next to the first log. Exit 0 proposed,
2 refused (the reason is printed). Verify: re-measures the plant under the proposed gains (the verify flight's short
excite) and its pre-excite hover rate error; exit 0 keep, 3 revert (prints the G_prev knobs to write back).
The gains that flew during a log are pid.c defaults unless --rate-gains / --angle-gains say otherwise.
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
    Design, Margins, PidRow, Spec, angle_loop, design_angle, design_rate, margins, rate_loop, read_pid_rows,
)

HOVER_RMS_MAX_RATIO = 1.1  # PROPOSED (WP-25 F3): verify hover rate-error RMS <= 1.1 x the ID flight's


def knobs(member: str, new: Mapping[str, float], prev: Mapping[str, float]) -> list[dict[str, Any]]:
    """CMD 0x01 writes (idx = axis * 3 + gain, controller_descriptor.PID_AXES) for every gain that changes."""
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


def _identify(args: argparse.Namespace, flown: PidRow, excite: Mapping[str, Any]) -> dict[str, Any]:
    """Load logs, IV FRF, fit. Returns {'series', 'runs', 'frf', 'fit', 'problem', 'baseline'}."""
    all_series = [fr.load_series(src) for src in args.logs]
    runs = [fr.extract(s, args.axis, excite) for s in all_series]
    frf = fr.plant_frf(runs, flown, (excite["f0"], excite["f1"]))
    fit = None
    try:
        fit = fr.fit_plant(frf)
    except fr.IdError as exc:
        problem = str(exc)
    else:
        problem = fr.quality_problem(frf, fit)
    starts = []
    for axis in fr.RATE_PID:
        try:
            t0, corr, *_ = fr.find_start(all_series[0], axis, excite)
        except fr.IdError:
            continue
        if corr >= fr.ALIGN_MIN_CORR:
            starts.append(t0)
    rms = fr.hover_rate_rms(all_series[0], args.axis, min(starts + [runs[0].t0_s]))
    print(f"{args.axis} {args.loop}: {len(runs)} run(s), {runs[0].source}, fs {runs[0].fs:g} Hz, "
          f"start {', '.join(f'{r.t0_s:.2f}' for r in runs)} s, excite {dict(excite)}")
    print(f"FRF: {int(frf.keep.sum())} bins with coherence >= {fr.COH_MIN} in {frf.band[0]:g}-{frf.band[1]:g} Hz, "
          f"band coverage {frf.coverage:.0%}, {frf.n_avg} Welch averages")
    if fit is not None:
        p = fit.plant
        print(f"fit: k {p.k:.4g} (deg/s^2 per mixer unit), tau {p.tau_s * 1000:.1f} ms, delay {p.delay_s * 1000:.1f} "
              f"ms, relative residual {fit.rel_residual:.3f} on {fit.n_bins} bins")
    print(f"pre-excite hover rate-error RMS: {'-' if rms is None else f'{rms:.2f} deg/s'}")
    return {"runs": runs, "frf": frf, "fit": fit, "problem": problem, "baseline": rms}


def _id_dict(args: argparse.Namespace, ident: Mapping[str, Any], excite: Mapping[str, Any]) -> dict[str, Any]:
    fit = ident["fit"]
    return {"axis": args.axis, "loop": args.loop, "logs": [str(p) for p in args.logs], "excite": dict(excite),
            "source": ident["runs"][0].source, "starts_s": [r.t0_s for r in ident["runs"]],
            "frf": {"bins": int(ident["frf"].keep.sum()), "coverage": ident["frf"].coverage},
            "fit": None if fit is None else {"k": fit.plant.k, "tau_s": fit.plant.tau_s, "delay_s": fit.plant.delay_s,
                                             "rel_residual": fit.rel_residual, "bins": fit.n_bins},
            "baseline": {"hover_rate_rms_dps": ident["baseline"]}}


def _out_path(args: argparse.Namespace, suffix: str) -> Path:
    if args.out:
        return Path(args.out)
    first = Path(args.logs[0])
    name = f"autotune_{args.axis}_{args.loop}{suffix}.json"
    return first / name if first.is_dir() else first.with_name(f"{first.stem}_{name}")


def _write(path: Path, data: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, default=float), encoding="utf-8")
    print(f"wrote {path}")


def propose(args: argparse.Namespace, rows: Mapping[str, PidRow], excite: Mapping[str, Any]) -> int:
    rate_member, angle_member = fr.RATE_PID[args.axis], fr.ANGLE_PID[args.axis]
    flown = _gains(args.rate_gains, rows[rate_member])
    member = rate_member if args.loop == "rate" else angle_member
    cur = flown if args.loop == "rate" else _gains(args.angle_gains, rows[angle_member])
    try:
        ident = _identify(args, flown, excite)
    except fr.IdError as exc:
        ident, d = None, Design("refused", str(exc), current=cur)
    else:
        if ident["problem"]:
            d = Design("refused", ident["problem"], current=cur)
        elif args.loop == "rate":
            d = design_rate(ident["fit"].plant, cur)
        else:
            d = design_angle(ident["fit"].plant, flown, cur)
    for name in ("current", "ideal", "proposed"):
        row = getattr(d, name)
        if row is not None:
            g = ", ".join(f"{k} {v:.4g}" for k, v in row.gains().items())
            c = ", ".join(f"{k} {v:.4g}" for k, v in row.continuous().items())
            print(f"{name:9s} fw [{g}]  cont [{c}]  {_m((d.margins or {}).get(name))}")
    out = {**(_id_dict(args, ident, excite) if ident else {"axis": args.axis, "loop": args.loop}),
           "member": member, "rate_gains": flown.gains(), **d.to_dict(), "knobs": []}
    if d.status != "proposed":
        print(f"REFUSED: {d.reason}")
        _write(_out_path(args, ""), out)
        return 2
    out["knobs"] = knobs(member, d.proposed.gains(), cur.gains())
    if d.note:
        print(f"note: {d.note}")
    print("knobs to write (CMD 0x01 PID_GAIN):" if out["knobs"] else "no change: current gains are the proposal")
    for k in out["knobs"]:
        print(f"  {k['symbol']:12s} = {k['value']:<8.4g} (was {k['prev']:.4g}; cmd 0x{k['cmd_id']:02X} idx {k['idx']})")
    _write(_out_path(args, ""), out)
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
    flown = new if args.loop == "rate" else _gains(",".join(str(prev["rate_gains"][g]) for g in PID_GAINS),
                                                   rows[fr.RATE_PID[args.axis]])
    fails = []
    try:
        ident = _identify(args, flown, excite)
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
    ap.add_argument("--axis", required=True, choices=sorted(fr.RATE_PID))
    ap.add_argument("--loop", choices=("rate", "angle"), default="rate")
    ap.add_argument("--rate-gains", help="Kp,Ki_fw,Kd_fw of the rate PID that flew (default API/pid.c)")
    ap.add_argument("--angle-gains", help="Kp,Ki_fw,Kd_fw of the current angle PID (default API/pid.c)")
    ap.add_argument("--verify", metavar="PROPOSAL_JSON", help="verify a proposal on this (verify-flight) log")
    ap.add_argument("--signal", choices=sorted(ex.SIGNALS))
    for k in ("f0", "f1", "amp", "duration"):
        ap.add_argument(f"--{k}", type=float, help="excite parameter, default the campaign's")
    ap.add_argument("--out", help="JSON path (default next to the first log)")
    args = ap.parse_args(argv)
    excite = dict(ex.VERIFY_EXCITE if args.verify else ex.ID_EXCITE)
    excite.update({k: v for k, v in (("signal", args.signal), ("f0", args.f0), ("f1", args.f1), ("amp", args.amp),
                                     ("duration_s", args.duration)) if v is not None})
    rows = read_pid_rows()
    return verify(args, rows, excite) if args.verify else propose(args, rows, excite)


if __name__ == "__main__":
    sys.exit(main())
