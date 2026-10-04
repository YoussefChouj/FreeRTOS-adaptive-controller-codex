"""SIL limit tests of the WP-33 MRAC variants: PR, ST, LFHG, with V1 and V2 as reference.

  python -m sim.sil.limits [--workers 4] [--out docs/analysis/sil-limits-2026-10-04.md]

Setup (PROPOSED): roadmap D doublet (it excites pitch and roll), MRAC injected on pitch and roll only
(g_ctrl_axis_mask 0x03; yaw and z trip abort U by design, WP-31 matrix), on the V1 drive (mrac_v1.yaml preset
v1_refmodel_g1). The variant knobs go on pitch and roll. Hardware in the plant: motor clamp 2000-4000, MOTOR_TAU 50.5 ms,
15 ms base delay (sim/bench/plant.py:22,37). Conditions: command delay +0/+5/+10 ms, and +0 ms with sensor noise x2.
G = mrac_g_gamma on pitch/roll, written by the SIL-only CMD 0x7E (sil_server.c) because 0x1D caps gamma_scale at 2.
LFHG runs lf_gain 1, so G is its whole gain (in flight: lf_gain = G and gamma_scale 1, the same product).
A run fails on a flight abort (metrics.ABORTS) that pid does not also trip in the same condition and seed (as the
WP-31 verdict); pid tilts past 12 deg on the doublet from +5 ms on, so there tilt counts only above pid's + TILT_TOL.
A point (variant, G, condition) fails when most of SEEDS fail. Gain edge = the first G failing at +0 ms; flight G
<= edge / MARGIN; its delay margin = the largest added delay up to which every delay passes. Failures at the edges
are re-run with hard_freeze_on 0 (CMD 0x0F idx 3) to tell the firmware's freeze guard from the law. Stage 2 sweeps
each variant's own knobs at its flight gain (0.25 if it has none), at +0 and +10 ms.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

from sim.sil import engine
from sim.sil.build import REPO
from sim.sil.controllers import ControllerSpec, preset_cmds, variant_cmds

DOC = REPO / "docs" / "analysis" / "sil-limits-2026-10-04.md"
SEEDS = (0, 1, 2)
CONDS = (("+0", "doublet"), ("+5", "doublet+delay5"), ("+10", "doublet+delay10"), ("+0 n2", "doublet+noise2"))
DELAYS = (("+0", 0), ("+5", 5), ("+10", 10))
CONDS2 = (CONDS[0], CONDS[2])
GAINS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0)
GAIN_CMD, MASK_PR, NO_FREEZE = 0x7E, ((0x1F, 1, 3.0),), ((0x0F, 3, 0.0),)
PR_AXES = (0, 1)
MARGIN = 4.0                                  # flight gain <= edge / MARGIN
TILT_TOL = 2.0                                # deg over pid's own tilt where pid already trips T (PROPOSED)
# gain the firmware accepts in flight: gamma_scale <= 2 (0x1D field 11); LFHG: lf_gain <= 10 (field 16)
G_FW_MAX = {"v1": 2.0, "v2": 2.0, "pr": 2.0, "st": 2.0, "st_e2": 2.0, "st_p2": 2.0, "st_bar": 2.0, "lfhg": 10.0}
# the variant knobs at their sweep centre, all PROPOSED: WP-31's PR values; ST eps 1.0 rad/s just inside e_freeze 1.2
# (p/r, MRAC_Init) and the sim's phi_max 10; LFHG = the MRAC_Init sigma_lf / gam_f rows. st_e2 / st_p2 / st_bar are
# the ST settings stage 2 found better at G 0.25 (wider bound, lower cap, barrier 0.2), swept in gain like the rest.
BASE = {"v1": {}, "v2": {}, "pr": dict(kappa_pr=0.5, crm_ell=10.0), "st": dict(st_eps=1.0, st_phi_max=10.0),
        "st_e2": dict(st_eps=2.0, st_phi_max=10.0), "st_p2": dict(st_eps=1.0, st_phi_max=2.0),
        "st_bar": dict(st_eps=1.0, st_phi_max=10.0, st_bar=0.2), "lfhg": dict(lf_gain=1.0, sigma_lf=0.8, gam_f=16.0)}
KNOBS = {
    "v1": (("ref_model_bw", (5.0, 10.0, 20.0, 44.0, 80.0)),),
    "pr": (("ref_model_bw", (5.0, 10.0, 20.0, 44.0, 80.0)), ("kappa_pr", (0.25, 0.5, 1.0, 1.5, 2.0)),
           ("crm_ell", (2.0, 5.0, 10.0, 20.0, 35.0, 50.0))),
    "st": (("ref_model_bw", (5.0, 10.0, 20.0, 44.0, 80.0)), ("st_eps", (0.2, 0.3, 0.5, 0.8, 1.0, 1.2, 2.0)),
           ("st_phi_max", (2.0, 5.0, 10.0, 50.0)), ("st_bar", (0.05, 0.2, 0.5, 1.0))),
    "lfhg": (("ref_model_bw", (5.0, 10.0, 20.0, 44.0, 80.0)), ("gam_f", (0.5, 2.0, 8.0, 16.0, 32.0, 64.0, 100.0)),
             ("sigma_lf", (0.1, 0.8, 2.0, 5.0))),
}
BASE_KNOB = {"ref_model_bw": 44.0, "st_bar": 0.0}     # MRAC_Init rows for pitch/roll
# flight setting per WP-33 variant (descriptor preset): the candidate with a flight G, then the widest delay margin,
# then noise x2 passed, then the highest flight G (PROPOSED rule)
FLIGHT = {"pr": ("mrac_pr", ("pr",)), "st": ("mrac_st", ("st", "st_e2", "st_p2", "st_bar")),
          "lfhg": ("mrac_lfhg", ("lfhg",))}


def choose(out: dict, cands: tuple[str, ...]) -> str:
    dm = out["dmargin"]
    return max(cands, key=lambda v: (out["gf"][v] > 0, -1 if dm[v] is None else dm[v], out["noise_ok"][v],
                                     out["gf"][v]))
WHY = {"crash": "X crash", "uad": "U |u_ad| > 0.5 |u_nom|", "tilt12": "T tilt", "clamp4000": "C motor at 4000 > 0.5 s",
       "simplex": "S simplex trip", "pos05": "P position error > 0.5 m"}
FLAG = {"crash": "X", "uad": "U", "tilt12": "T", "clamp4000": "C", "simplex": "S", "pos05": "P"}
PID = ControllerSpec("pid", "firmware PID rows, MRAC in shadow")


def spec(var: str, gain: float, extra: tuple = (), **knobs: float) -> ControllerSpec:
    """V1 (or V2) preset + the variant's knobs on pitch/roll + G on pitch/roll + injection on p/r only."""
    base = preset_cmds("mrac_v2", "v2_sataware") if var == "v2" else preset_cmds("mrac_v1", "v1_refmodel_g1")
    tag = ",".join(f"{n}={v:g}" for n, v in knobs.items()) + (",nofreeze" if extra else "")
    return ControllerSpec(f"{var}@G{gain:g}" + (f"[{tag}]" if tag else ""), f"limit test {var}",
                          cmds=base + variant_cmds(PR_AXES, **{**BASE[var], **knobs})
                          + tuple((GAIN_CMD, a, gain) for a in PR_AXES) + MASK_PR + extra, inject=True)


def failing(m: dict, ref: dict) -> list[str]:
    """The aborts that count against the variant, given pid's run `ref` in the same condition and seed."""
    lim = max(12.0, ref["max_tilt_deg"] + TILT_TOL) if "tilt12" in ref["aborts"] else 12.0
    return [a for a in m["aborts"] if (m["max_tilt_deg"] > lim if a == "tilt12" else a not in ref["aborts"])]


def point(ms: list[dict], refs: list[dict]) -> tuple[bool, list[str], int]:
    """(fails by majority, flags of the failing seeds, number of failing seeds) for one point over SEEDS."""
    f = [failing(m, r) for m, r in zip(ms, refs)]
    n = sum(bool(x) for x in f)
    return 2 * n > len(f), sorted({a for x in f for a in x}, key=list(FLAG).index), n


class Results:
    def __init__(self, res: dict):
        self.res = res

    def ms(self, *key) -> list[dict]:
        return [self.res[key + (s,)] for s in SEEDS]

    def pid(self, cond: str) -> list[dict]:
        return self.ms("pid", cond)

    def pt(self, cond: str, *key) -> tuple[bool, list[str], int]:
        return point(self.ms(*key, cond), self.pid(cond))


def _run(jobs_keys: list[tuple], workers: int) -> dict:
    jobs = [j for j, _ in jobs_keys]
    return {k: m for (_, k), m in zip(jobs_keys, engine.run_metrics(jobs, workers))} if jobs else {}


def analyse(R: Results) -> dict:
    out = {"edge": {}, "edge_any": {}, "gf": {}, "dmargin": {}, "noise_ok": {}}
    for var in BASE:
        nom = [R.pt("+0", "gain", var, g)[0] for g in GAINS]
        anyc = [any(R.pt(c, "gain", var, g)[0] for c, _ in CONDS) for g in GAINS]
        e = GAINS[nom.index(True)] if True in nom else None
        out["edge"][var] = e
        out["edge_any"][var] = GAINS[anyc.index(True)] if True in anyc else None
        lim = min(G_FW_MAX[var], e / MARGIN if e is not None else float("inf"))
        ok = [g for g in GAINS if g <= lim]
        gf = ok[-1] if ok else 0.0
        out["gf"][var] = gf
        g = gf if gf > 0 else GAINS[0]
        dm = None
        for c, d in DELAYS:
            if R.pt(c, "gain", var, g)[0]:
                break
            dm = d
        out["dmargin"][var] = dm
        out["noise_ok"][var] = not R.pt("+0 n2", "gain", var, g)[0]
    return out


def run(workers: int) -> tuple[Results, dict]:
    jk = [((PID, s, seed), ("pid", c, seed)) for c, s in CONDS for seed in SEEDS]
    jk += [((spec(var, g), s, seed), ("gain", var, g, c, seed))
           for var in BASE for g in GAINS for c, s in CONDS for seed in SEEDS]
    R = Results(_run(jk, workers))
    out = analyse(R)
    # stage 2 and the hard-freeze re-runs of the failing edge points
    jk = []
    for var, knobs in KNOBS.items():
        g = out["gf"][var] or GAINS[0]
        jk += [((spec(var, g, **{knob: v}), s, seed), ("knob", var, knob, v, c, seed))
               for knob, vals in knobs for v in vals for c, s in CONDS2 for seed in SEEDS]
    out["freeze"] = []
    for var in BASE:
        pts = []
        if out["edge"][var] is not None:
            pts.append((out["edge"][var], "+0"))
        g = out["gf"][var] or GAINS[0]
        bad = [c for c, _ in CONDS if R.pt(c, "gain", var, g)[0]]
        if bad:
            pts.append((g, bad[0]))
        for g, c in pts:
            out["freeze"].append((var, g, c))
            jk += [((spec(var, g, NO_FREEZE), dict(CONDS)[c], seed), ("nofreeze", var, g, c, seed)) for seed in SEEDS]
    R.res.update(_run(jk, workers))
    return R, out


def _cell(R: Results, var: str, g: float) -> str:
    rm = np.mean([m["rmse_cm"] for m in R.ms("gain", var, g, "+0")])
    bad = [(c, R.pt(c, "gain", var, g)[1]) for c, _ in CONDS if R.pt(c, "gain", var, g)[0]]
    return f"{rm:.1f}" + ("" if not bad else " " + " ".join(f"{''.join(FLAG[a] for a in fl)}({c})" for c, fl in bad))


def _diag(ms: list[dict], refs: list[dict]) -> str:
    f = [m for m, r in zip(ms, refs) if failing(m, r)]
    if not f:
        return "-"
    return (f"u_ad/u_nom {max(m['uad_ratio'] for m in f):.2f}, tilt {max(m['max_tilt_deg'] for m in f):.1f} vs pid "
            f"{max(r['max_tilt_deg'] for r in refs):.1f}, max err {max(m['max_err_cm'] for m in f):.0f} cm, "
            f"hf {np.mean([m['uad_hf'] for m in f]):.1f}")


def knob_summary(R: Results, var: str, knob: str, vals) -> tuple[str, str, str]:
    """(pass range, failures, chosen value) for one stage-2 sweep."""
    ok = [v for v in vals if not any(R.pt(c, "knob", var, knob, v)[0] for c, _ in CONDS2)]
    bad = [f"{v:g}: " + " ".join(f"{''.join(FLAG[a] for a in R.pt(c, 'knob', var, knob, v)[1])}({c})"
                                 for c, _ in CONDS2 if R.pt(c, "knob", var, knob, v)[0]) for v in vals if v not in ok]
    base = BASE[var].get(knob, BASE_KNOB.get(knob))
    inner = [v for v in vals if ok and min(ok) <= v <= max(ok)]
    rng = (f"{min(ok):g} .. {max(ok):g}" + ("" if len(ok) == len(inner) else " (gaps)")) if ok else "none"
    pick = base if base in ok or (base == 0.0 and knob == "st_bar") else (
        min(ok, key=lambda v: abs(np.log(v / base))) if ok and base else None)
    return rng, "; ".join(bad) or "-", "-" if pick is None else f"{pick:g}"


def write_doc(path: Path, R: Results, out: dict, wall: float) -> None:
    n = len(R.res)
    L = ["# SIL limit tests of the MRAC variants, 2026-10-04 (WP-33)", "",
         f"`python -m sim.sil.limits`: {n} runs (seeds {', '.join(map(str, SEEDS))}), wall {wall:.0f} s. Generated; do "
         "not edit. SIL only, unvalidated against flight (the WP-31 matrix caveat applies). Setup and rules: the "
         "`sim/sil/limits.py` docstring. Every threshold and preset here is PROPOSED.", "",
         "A point fails when most seeds trip an abort that pid does not trip in the same condition and seed (tilt: "
         f"> 12 deg, or > pid + {TILT_TOL:g} deg where pid trips T). Cells: doublet position RMSE cm at +0 ms (seed mean), "
         "then the failing conditions with their flags. X crash, U |u_ad| > 0.5 |u_nom|, T tilt, C motor at 4000 > "
         "0.5 s, S simplex, P error > 0.5 m. Conditions: +0/+5/+10 ms extra command delay, +0 n2 = sensor noise x2.", "",
         "pid on the same doublet (reference, seed mean): " + "; ".join(
             f"{c} ms: rmse {np.mean([m['rmse_cm'] for m in R.pid(c)]):.1f} cm, tilt "
             f"{np.mean([m['max_tilt_deg'] for m in R.pid(c)]):.1f} deg, T in "
             f"{sum('tilt12' in m['aborts'] for m in R.pid(c))}/{len(SEEDS)} seeds" for c, _ in CONDS) + ".", "",
         "## Stage 1: gain sweep (G = gamma multiplier on pitch/roll)", "",
         "| variant | " + " | ".join(f"G {g:g}" for g in GAINS) + " |", "|---|" + "---|" * len(GAINS)]
    for var in BASE:
        L.append(f"| {var} | " + " | ".join(_cell(R, var, g) for g in GAINS) + " |")
    L += ["", "High-frequency content of the injected correction at +0 ms (RMS tick-to-tick change of corr p/r, mixer "
          "units, seed mean; pid injects 0):", "", "| variant | " + " | ".join(f"G {g:g}" for g in GAINS) + " |",
          "|---|" + "---|" * len(GAINS)]
    for var in BASE:
        L.append(f"| {var} | " + " | ".join(f"{np.mean([m['uad_hf'] for m in R.ms('gain', var, g, '+0')]):.1f}"
                                            for g in GAINS) + " |")
    L += ["", "## Envelope, flight gain and margins", "",
          f"Gain edge = the first G that fails at +0 ms. Flight G = the largest grid G <= edge / {MARGIN:g} and <= the "
          "firmware bound (gamma_scale 2; LFHG lf_gain 10); 0 = none, then the margins are those of G 0.25. Delay "
          "margin = the largest extra delay up to which every delay passes at the flight G.", "",
          "| variant | stable G at +0 ms | gain edge | first G failing any condition | flight G | gain margin | "
          "delay margin | noise x2 | first failure at the gain edge (+0 ms) |", "|---|---|---|---|---|---|---|---|---|"]
    for var in BASE:
        e, ea, gf, dm = out["edge"][var], out["edge_any"][var], out["gf"][var], out["dmargin"][var]
        ok = [g for g in GAINS if e is None or g < e]
        env = f"{ok[0]:g} .. {ok[-1]:g}" if ok else "none"
        top = f"> {GAINS[-1]:g}"
        gm = (f"x{e / gf:g}" if e is not None else f"> x{GAINS[-1] / gf:g}") if gf > 0 else "-"
        fl = R.pt("+0", "gain", var, e) if e is not None else None
        what = (", ".join(WHY[a] for a in fl[1]) + f" in {fl[2]}/{len(SEEDS)} seeds ("
                + _diag(R.ms("gain", var, e, "+0"), R.pid("+0")) + ")") if fl else "none up to the top of the grid"
        L.append(f"| {var} | {env} | {e if e is not None else top} | {ea if ea is not None else top} | {gf:g} | {gm} | "
                 f"{'none' if dm is None else f'+{dm} ms'} | {'pass' if out['noise_ok'][var] else 'fail'} | {what} |")
    L += ["", "## Flight presets (the campaign presets of the descriptors)", "",
          "Rule: among a variant's settings, the one with a flight G, then the widest delay margin, then noise x2 passed, "
          "then the highest flight G. The rest of the V1 drive is mrac_v1.yaml v1_refmodel_g1 on pitch/roll.", "",
          "| variant | descriptor | setting | knobs (pitch/roll) | flight G | gain margin | delay margin | noise x2 | "
          "rmse +0 cm |", "|---|---|---|---|---|---|---|---|---|"]
    for var, (desc, cands) in FLIGHT.items():
        v = choose(out, cands)
        gf, e, dm = out["gf"][v], out["edge"][v], out["dmargin"][v]
        g = gf or GAINS[0]
        knobs = ", ".join(f"{k} {x:g}" for k, x in BASE[v].items() if not (v == "lfhg" and k == "lf_gain"))
        knobs += f", lf_gain {g:g} (gamma_scale 1)" if v == "lfhg" else f", gamma_scale {g:g}"
        L.append(f"| {var} | {desc}.yaml | {v} | {knobs} | {gf:g} | "
                 f"{(f'x{e / gf:g}' if e is not None else f'> x{GAINS[-1] / gf:g}') if gf > 0 else '-'} | "
                 f"{'none' if dm is None else f'+{dm} ms'} | {'pass' if out['noise_ok'][v] else 'fail'} | "
                 f"{np.mean([m['rmse_cm'] for m in R.ms('gain', v, g, '+0')]):.1f} |")
    L += ["", "### Hard-freeze attribution", "",
          "The failing edge points re-run with hard_freeze_on 0 (CMD 0x0F idx 3). The freeze zeroes u_ad in one tick "
          "when |e| > e_freeze (1.2 rad/s p/r) and the u_ad low-pass ramps it back; under delay this repeats at a few Hz.",
          "", "| variant | G | condition | firmware: failing seeds, flags | freeze off: failing seeds, flags | hf firmware -> "
          "freeze off |", "|---|---|---|---|---|---|"]
    for var, g, c in out["freeze"]:
        a, b = R.pt(c, "gain", var, g), R.pt(c, "nofreeze", var, g)
        hf = (np.mean([m["uad_hf"] for m in R.ms("gain", var, g, c)]),
              np.mean([m["uad_hf"] for m in R.ms("nofreeze", var, g, c)]))
        L.append(f"| {var} | {g:g} | {c} ms | {a[2]}/{len(SEEDS)} {''.join(FLAG[x] for x in a[1]) or '-'} | "
                 f"{b[2]}/{len(SEEDS)} {''.join(FLAG[x] for x in b[1]) or '-'} | {hf[0]:.1f} -> {hf[1]:.1f} |")
    L += ["", "## Stage 2: the variants' own knobs at the flight gain (+0 and +10 ms)", "",
          "Each knob is swept alone around the variant's sweep centre (ST: st_eps 1.0, st_phi_max 10).", "",
          "| variant | G | knob | values | pass range | failures | centre, or nearest pass |", "|---|---|---|---|---|---|---|"]
    for var, knobs in KNOBS.items():
        for knob, vals in knobs:
            rng, bad, pick = knob_summary(R, var, knob, vals)
            L.append(f"| {var} | {out['gf'][var] or GAINS[0]:g} | {knob} | {', '.join(f'{v:g}' for v in vals)} | {rng} | "
                     f"{bad} | {pick} |")
    L += ["", "Stage 2 position RMSE cm at +0 ms, seed mean (the knob's effect inside the envelope):", "",
          "| variant | knob | value: rmse |", "|---|---|---|"]
    for var, knobs in KNOBS.items():
        for knob, vals in knobs:
            L.append(f"| {var} | {knob} | " + ", ".join(
                f"{v:g}: {np.mean([m['rmse_cm'] for m in R.ms('knob', var, knob, v, '+0')]):.1f}" for v in vals) + " |")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", type=Path, default=DOC)
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    R, out = run(a.workers)
    wall = time.perf_counter() - t0
    write_doc(a.out, R, out, wall)
    print(f"wrote {a.out} ({len(R.res)} runs), wall {wall:.1f} s")
    for var in BASE:
        print(f"{var:5s} edge {out['edge'][var]} any {out['edge_any'][var]} flight G {out['gf'][var]} "
              f"delay margin {out['dmargin'][var]} noise ok {out['noise_ok'][var]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
