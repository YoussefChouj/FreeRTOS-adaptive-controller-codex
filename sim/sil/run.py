"""SIL CLI (WP-31).

  python -m sim.sil.run --controller v1_g025 --scenario figure8+wind_gust+cog [--seed 0]
  python -m sim.sil.run --matrix [--seed 0] [--controllers pid,mrac] [--out docs/analysis/sil-matrix-2026-10-04.md]

Controllers: sim/sil/controllers.py (pid_autotune runs ground_station/autotune on the SIL first, cached in build/;
a `_noz` suffix = the same controller with Z not injected, `_pr` = only pitch/roll injected). Scenarios:
sim/sil/scenarios.py (a trajectory and any
disturbances joined by '+'). Deterministic for a seed; --workers does not change results.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

from sim.sil import autotune, controllers, engine, metrics, scenarios, validate
from sim.sil.build import REPO

DOC = REPO / "docs" / "analysis" / "sil-matrix-2026-10-04.md"
FLAG = {"crash": "X", "uad": "U", "tilt12": "T", "clamp4000": "C", "simplex": "S", "pos05": "P"}
SHORT = {"pid": "pid", "pid_autotune": "pid_at", "mrac": "mrac", "v1_g025": "v1 x.25", "v1_g1": "v1 x1", "v2": "v2",
         "pr": "pr", "l3": "3l", "v3": "v3", "st": "st", "lfhg": "lfhg", "pr_st_lf": "pr+st+lf"}
SUFFIX = {"_noz": " noZ", "_pr": " p/r"}
THRUST = ("hover", "hover+mass_p15", "hover+mass_m15", "hover+battery", "hover+motor80")


def registry(seed: int, names=None) -> tuple[dict, list]:
    need_at = names is None or "pid_autotune" in names
    cmds, rounds = autotune.autotune(seed) if need_at else (None, [])
    reg = controllers.registry(cmds)
    if names is not None:
        reg = {k: v for k, v in reg.items() if k in names or (k.endswith("_noz") and k[:-4] in names)}
    return reg, rounds


def short(n: str) -> str:
    for suf, txt in SUFFIX.items():
        if n.endswith(suf) and n not in SHORT:
            return SHORT.get(n[:-len(suf)], n[:-len(suf)]) + txt
    return SHORT.get(n, n)


def flags(m: dict) -> str:
    return "".join(FLAG[a] + (f"({m['uad_axes']})" if a == "uad" else "") for a in m["aborts"])


def cell(m: dict) -> str:
    return f"{m['rmse_cm']:.1f}" + (" " + flags(m) if m["aborts"] else "")


def verdict(name: str, res: dict, groups: dict) -> tuple[str, str]:
    """PROPOSED rules. pid is judged on its own aborts; every other controller on the aborts pid does not also have
    in the same scenario ("new"):
      do not fly       a preset command refused; a new abort in hover or doublet; a new crash in any nominal or
                       single-disturbance run; figure-8 RMSE > 1.5 x pid
      fly with limits  a new abort in the figure-8, a single, a pair or the worst stack (listed: avoid them);
                       figure-8 RMSE > 1.1 x pid (roadmap pass bound); Theta rising > 5 s in hover or doublet
      fly              none of the above"""
    sc = {g: [s.name for s in groups[g]] for g in groups}
    mine = {s: res[(name, s)] for g in sc.values() for s in g}
    ref = {s: res[("pid", s)] for s in mine} if name != "pid" else {s: {"aborts": []} for s in mine}
    new = {s: [a for a in m["aborts"] if a not in ref[s]["aborts"]] for s, m in mine.items()}

    def listed(ss) -> str:
        return ", ".join(s.replace("figure8+", "f8+") + " (" + ",".join(
            a + (f":{mine[s]['uad_axes']}" if a == "uad" else "") for a in new[s]) + ")" for s in ss)

    f8 = mine["figure8"]["rmse_cm"] / max(res[("pid", "figure8")]["rmse_cm"], 1e-6)
    if not all(m["applied"] for m in mine.values()):
        return "do not fly", "a preset command was refused by the firmware parser"
    hd = [s for s in ("hover", "doublet") if new[s]]
    crash = [s for s in sc["nominal"] + sc["single"] if "crash" in new[s]]
    if hd or crash or f8 > 1.5:
        why = ([f"new aborts in {listed(hd)}"] if hd else []) + ([f"crashes: {', '.join(crash)}"] if crash else [])
        return "do not fly", "; ".join(why + ([f"figure-8 RMSE {f8:.2f} x pid"] if f8 > 1.5 else []))
    rest = [s for s in mine if new[s]]
    th = [s for s in ("hover", "doublet") if mine[s]["theta_rise_s"] > 5.0] if name != "pid" else []
    why = ([f"{len(rest)} runs with new aborts, e.g. {listed(rest[:4])}"] if rest else [])
    why += [f"figure-8 RMSE {f8:.2f} x pid"] if f8 > 1.1 else []
    why += [f"Theta rising > 5 s in {', '.join(th)}"] if th else []
    if why:
        return "fly with limits", "; ".join(why)
    return "fly", f"no abort pid does not also have; figure-8 RMSE {f8:.2f} x pid"


def findings(res: dict, groups: dict, names: list, rounds: list) -> list[str]:
    th = scenarios._FIG8_TH
    x, y = 0.5 * np.sin(2 * th), 0.25 * np.sin(th)
    dx, dy = np.gradient(x, th), np.gradient(y, th)
    ddx, ddy = np.gradient(dx, th), np.gradient(dy, th)
    kap = float(np.max(np.abs(dx * ddy - dy * ddx) / np.maximum((dx * dx + dy * dy) ** 1.5, 1e-12)))
    acc = scenarios.V_PATH ** 2 * kap
    pf = res[("pid", "figure8")]
    out = [f"- F path: the trajectory_pipeline figure-8 (1.0 x 0.5 m) has a {100 / kap:.1f} cm tip radius; at "
           f"{scenarios.V_PATH} m/s that needs {acc:.1f} m/s^2 ({np.degrees(np.arctan(acc / 9.81)):.0f} deg) against the "
           f"15 deg lean limit (gs_max_pitch_deg), so pid already tilts {pf['max_tilt_deg']:.1f} deg (abort T). PROPOSED: "
           "fly F slower (tilt ~ v^2) or round the tips before using T on F.",
           "- Z authority: "]
    zs = {n: (res[(n, "hover+mass_p15")]["z_rmse_cm"], res[(n, "hover+battery")]["z_rmse_cm"]) for n in names}
    out[-1] += (f"pid z RMSE {zs['pid'][0]:.1f} cm at mass +15 %, {zs['pid'][1]:.1f} cm under battery sag "
                f"(Z_ratePID Ui cap Ki x SumEMax = 0.435 x 250 = 109 < the extra hover PWM); mrac {zs.get('mrac', (0, 0))[0]:.1f} / "
                f"{zs.get('mrac', (0, 0))[1]:.1f} cm because its z u_ad carries it.")
    inj = [n for n in names if n not in ("pid", "pid_autotune") and not n.endswith(tuple(SUFFIX))]
    combos: dict[str, int] = {}
    for n in inj:
        for g in groups.values():
            for s in g:
                if "uad" in res[(n, s.name)]["aborts"]:
                    combos[res[(n, s.name)]["uad_axes"]] = combos.get(res[(n, s.name)]["uad_axes"], 0) + 1
    out.append(f"- Abort U trips in {sum(combos.values())} injected runs, by axes: "
               + ", ".join(f"{k} {v}" for k, v in sorted(combos.items(), key=lambda kv: -kv[1]))
               + ". Yaw and z: the bias weights carry the hover yaw imbalance (plant u_imb 350-500 U, logged ~430) and"
               " the hover thrust offset that Z_ratePID.U otherwise holds, so u_ad stays above 0.5 u_nom in steady"
               " hover by design. With Z masked (table above) yaw alone still trips it. PROPOSED: define U on p/r only, or"
               " on the change of u_ad, before the injected flights.")
    if rounds:
        r0 = rounds[0]
        pm = {a: r0[a]["rate"]["margins"].get("current", {}).get("pm_deg") for a in r0}
        out.append(f"- Rate-loop margins on the SIL (autotune round 1, firmware rows): PM roll {pm.get('roll')} deg, pitch "
                   f"{pm.get('pitch')} deg (Spec 45+5). pid_at = {len(rounds)} autotune rounds (+-30 % per round): lower "
                   "rate Kp, higher Kd; fewer clamp seconds but slower position tracking (table above).")
    hd = max(res[(n, "hover")]["heading_drift_deg"] for n in names)
    out += [f"- Heading: the plant's gyro bias (sim/bench scen.py, 0.3 deg/s/axis) turns the true heading up to {hd:.0f} deg "
            "in 40 s with no magnetometer; positions are scored in the navigation frame (sim/sil/plant.py p_nav).",
            "- Firmware units (not changed here, firmware read-only): API/mrac.c:318 divides imu_data.pit/.rol (degrees: "
            "StabilizerTask.c:1715, wfb_safety pitch_deg) by rbf_ang_scale in rad (0.26 = 15 deg, mrac.c:905), so the V3 "
            "angle grid saturates at ~0.3 deg; simplex roll_max/pitch_max (3.14) are compared in degrees too (mrac.c:749-750).",
            "- Plant vs sim/bench: yaw effectiveness 1.13 deg/s^2/U (constants.py) not 7.55 (gyroz Kp 8 limit-cycles at "
            "7.55); OF measured in the body frame and rotated by the yaw estimate (plant.run feeds world velocity).",
            "- Host us/tick is host CPU time, not STM32 cost; on target read mrac_cyc (DWT cycles)."]
    return out


def write_doc(path: Path, reg: dict, groups: dict, res: dict, val: list, wall: float, seed: int, n_rows: int,
              rounds: list) -> None:
    names = [n for n in reg if not n.endswith(tuple(SUFFIX))]
    noz = [n for n in reg if n.endswith("_noz")]
    pr_only = [n for n in reg if n.endswith("_pr")]
    hdr = "| " + " | ".join(short(n) for n in names) + " |"
    sep = "|" + "---|" * (len(names) + 1)
    allsc = [s.name for g in groups.values() for s in g]
    L = ["# SIL scenario matrix, 2026-10-04 (WP-31; WP-33 added st, lfhg, pr+st+lf and the p/r-only rows)", "",
         f"`python -m sim.sil.run --matrix --seed {seed}`: {n_rows} runs, wall {wall:.0f} s. Generated; do not edit.",
         "**Validation: " + ("see the replay section below.**" if val else
                             "UNVALIDATED.** No usable session under `logs/sessions` or `logs/campaigns` in this tree "
                             "(they are local to the lab checkout); `python -m sim.sil.run --matrix` there replays them."),
         "",
         "Firmware in the loop: `API/pid.c`, `API/mrac.c`, `API/mrac_math.c`, `API/controller.c` (V2 deficit included), built "
         "by `sim/sil/build.py` (32-bit MinGW: an executable on pipes, not ctypes). `Compute_Motor`/`Update_Des`/mixer of "
         "`TASK/StabilizerTask.c` (not host-buildable) are ported line-cited in `sim/sil/csrc/sil_server.c`. Plant: "
         "`sim/bench/plant.py` + per-axis rate gains of `ground_station/research/sim/constants.py` (`sim/sil/plant.py`). "
         "Every disturbance value is PROPOSED (`sim/sil/scenarios.py`); scored after an 8 s hover warmup.", "",
         "Cells: position RMSE cm (navigation frame) + abort flags (roadmap :189-190): X crash, U 1 s-RMS |u_ad| > 0.5 "
         "|u_nom| (axes p r y z), T tilt > 12 deg, C a motor at 4000 > 0.5 s, S simplex trip, P error > 0.5 m.", "",
         "## Controllers", "", "| name | what | host us/tick |", "|---|---|---|"]
    for n in names:
        us = np.mean([res[(n, s)]["host_us"] for s in allsc])
        L.append(f"| {short(n)} | {reg[n].doc} | {us:.1f} |")
    L += ["", "## Nominal (roadmap H, D, F)", "",
          "| ctrl | H rmse | D rmse | D overshoot cm | F rmse | max tilt | clamp s | u_ad/u_nom | Theta rise s | aborts |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for n in names:
        ms = [res[(n, s)] for s in ("hover", "doublet", "figure8")]
        ab = sorted({a for m in ms for a in m["aborts"]})
        top = max(ms, key=lambda m: m["uad_ratio"])
        L.append(f"| {short(n)} | {ms[0]['rmse_cm']:.1f} | {ms[1]['rmse_cm']:.1f} | {ms[1]['overshoot_cm']:.1f} | "
                 f"{ms[2]['rmse_cm']:.1f} | {max(m['max_tilt_deg'] for m in ms):.1f} | {max(m['clamp_s'] for m in ms):.2f} | "
                 f"{top['uad_ratio']:.2f} {top['uad_ratio_axis'] if top['uad_ratio'] > 0 else ''} | "
                 f"{max(m['theta_rise_s'] for m in ms):.0f} | {','.join(ab) or '-'} |")
    for title, g in (("Single disturbances on the hover", "single"), ("Pairs on the figure-8", "pairs"),
                     ("Worst plausible stack on the figure-8", "worst")):
        L += ["", f"## {title}", "", "| scenario " + hdr, sep]
        for s in groups[g]:
            nm = s.name.replace("figure8+", "").replace("hover+", "") if g != "worst" else "+".join(d.name for d in s.dists)
            L.append(f"| {nm} | " + " | ".join(cell(res[(n, s.name)]) for n in names) + " |")
    L += ["", "## Altitude under thrust changes (z RMSE cm)", "", "| scenario " + hdr, sep]
    for s in THRUST:
        L.append(f"| {s.replace('hover+', '') if s != 'hover' else 'hover'} | " +
                 " | ".join(f"{res[(n, s)]['z_rmse_cm']:.1f}" for n in names) + " |")
    L += ["", "## Worst case per controller (all scenarios)", "",
          "| ctrl | max tilt | max clamp s | max u_ad/u_nom | max Theta rise s | runs with aborts | crashes |",
          "|---|---|---|---|---|---|---|"]
    for n in names:
        ms = [res[(n, s)] for s in allsc]
        L.append(f"| {short(n)} | {max(m['max_tilt_deg'] for m in ms):.1f} | {max(m['clamp_s'] for m in ms):.2f} | "
                 f"{max(m['uad_ratio'] for m in ms):.2f} | {max(m['theta_rise_s'] for m in ms):.0f} | "
                 f"{sum(bool(m['aborts']) for m in ms)}/{len(ms)} | {sum(m['crashed'] for m in ms)} |")
    L += ["", "## Verdicts (rules PROPOSED: `sim/sil/run.py` verdict)", "", "| ctrl | verdict | reason |", "|---|---|---|"]
    for n in names:
        v, why = verdict(n, res, groups)
        L.append(f"| {short(n)} | {v} | {why} |")
    for sub, title in ((noz, "Same MRAC laws with Z not injected (g_ctrl_axis_mask 0x07)"),
                       (pr_only, "MRAC laws injected on pitch/roll only (g_ctrl_axis_mask 0x03, the WP-33 limit-test "
                                 "setup: docs/analysis/sil-limits-2026-10-04.md)")):
        if not sub:
            continue
        L += ["", f"## {title}", "",
              "| ctrl | H / D / F rmse | max u_ad/u_nom | runs with aborts | verdict | reason |", "|---|---|---|---|---|---|"]
        for n in sub:
            ms = [res[(n, s)] for s in allsc]
            v, why = verdict(n, res, groups)
            hdf = " / ".join("%.1f" % res[(n, s)]["rmse_cm"] for s in ("hover", "doublet", "figure8"))
            L.append(f"| {short(n)} | {hdf} | {max(m['uad_ratio'] for m in ms):.2f} | "
                     f"{sum(bool(m['aborts']) for m in ms)}/{len(ms)} | {v} | {why} |")
    L += ["", "## Findings", ""] + findings(res, groups, names, rounds)
    L += ["", "## Replay of logged flights (F)", ""]
    if val:
        L += ["| session | kind | axis | log rms cm | sim rms cm | FB diff rms cm |", "|---|---|---|---|---|---|"]
        for r in val:
            for ax in "xyz":
                L.append(f"| {r['session']} | {r['kind']} | {ax} | {r[ax]['log_rms_cm']:.1f} | {r[ax]['sim_rms_cm']:.1f} | "
                         f"{r[ax]['fb_diff_rms_cm']:.1f} |")
    else:
        L.append("None found: the matrix is unvalidated against flight data.")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--controller", default="pid")
    ap.add_argument("--scenario", default="hover")
    ap.add_argument("--matrix", action="store_true")
    ap.add_argument("--controllers", help="comma list for --matrix (default all)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4, help="--matrix worker processes (results do not depend on it)")
    ap.add_argument("--out", type=Path, default=DOC)
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    if not a.matrix:
        suf = next((s for s in SUFFIX if a.controller.endswith(s)), "")
        base = a.controller[:-len(suf)] if suf else a.controller
        reg, _ = registry(a.seed, [base])
        if base not in reg:
            ap.error(f"unknown controller {a.controller!r}; one of {', '.join(controllers.NAMES)} (+ _noz, _pr)")
        spec = {"_noz": controllers.no_z, "_pr": controllers.pr_only}.get(suf, lambda s: s)(reg[base])
        lg = engine.run([engine.Case(spec, scenarios.parse(a.scenario), a.seed)])[0]
        for k, v in metrics.compute(lg).items():
            print(f"{k:18s} {v:.3f}" if isinstance(v, float) else f"{k:18s} {v}")
        print(f"wall {time.perf_counter() - t0:.1f} s")
        return 0
    reg, rounds = registry(a.seed, a.controllers.split(",") if a.controllers else None)
    if "pid" not in reg:
        ap.error("--matrix needs pid in --controllers (the verdicts compare against it)")
    base = list(reg.values())
    reg.update({controllers.no_z(c).name: controllers.no_z(c) for c in base if c.inject})
    reg.update({controllers.pr_only(c).name: controllers.pr_only(c) for c in base if c.name in controllers.PR_ONLY})
    groups = scenarios.matrix()
    jobs = [(c, s.name, a.seed) for c in reg.values() for g in groups.values() for s in g]
    res = {(c.name, s): m for (c, s, _), m in zip(jobs, engine.run_metrics(jobs, a.workers))}
    val = validate.validate(REPO, a.seed)
    wall = time.perf_counter() - t0
    write_doc(a.out, reg, groups, res, val, wall, a.seed, len(jobs), rounds)
    print(f"wrote {a.out} ({len(jobs)} runs), wall {wall:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
