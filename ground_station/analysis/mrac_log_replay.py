"""Open-loop replay of the WP-27 MRAC variants on flight logs, through the host-built firmware law.

API/mrac*.c are compiled with gcc (as API/tests/run_mrac_equiv.py and tests/test_mrac_variants_host.py do) around
mrac_log_replay_host.c; every log tick (resampled to MRAC_DT = 5 ms) is fed to MRAC_Control. The plant does NOT
respond: x, r, u_nom are the flown ones, so this shows what each law would have commanded and how its weights
evolve, not how the closed loop would have changed. Variants are set through MRAC_VariantParamSet (CMD 0x1D)
from the WP-27 campaign presets (ground_station/analysis/controllers/mrac_v*.yaml); PR and 3L have no flight
value, their knobs here are PROPOSED. All runs are as if injected (output_injection_on 1, simplex observe-only).

    python -m ground_station.analysis.mrac_log_replay [--root <checkout with logs/>] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from ground_station.analysis import adaptive_review as ar
from ground_station.analysis import log_corpus as lc
from ground_station.analysis.mrac_variants import AXES as VAR_AXES
from ground_station.analysis.mrac_variants import GAMMA_SCALE_FIELD, VARIANT_FIELDS
from ground_station.research.sim.constants import MIXER_R_P, MIXER_YAW, MIXER_Z

DT = 0.005                                  # MRAC_DT, API/mrac.h
AXES = ("pitch", "roll", "yaw", "z")        # driver order = MRAC_Axis_e
RATE = {"pitch": "gyroyPID", "roll": "gyroxPID", "yaw": "gyrozPID", "z": "Z_ratePID"}
TO_MIXER = {"pitch": MIXER_R_P, "roll": MIXER_R_P, "yaw": MIXER_YAW, "z": MIXER_Z}  # mrac.h:38-40 (LIGHT)
MOTOR_LO, MOTOR_HI = 2000.0, 4000.0         # controller.c motor_cut, BSP/pwm.h Motor_PWM_ZERO/MAX
YAW_MIX_DIR = -1.0                          # g_yaw_mix_dir default, TASK/StabilizerTask.c:88
N_RBF, N_OUT = 12, 19 + 24
ABORT_RATIO, ABORT_HOLD_S = 0.5, 1.0        # docs/workflow-b/mrac-variants.md:51 (watch by hand)
GROWTH_MAX_S = 5.0                          # V3 pass rule "no Theta-norm growth > 5 s"
WARMUP_S = 10.0                             # PROPOSED: gated time before growth counts (Theta starts at 0)
REQUIRED = [f"Ctrler.{RATE[a]}.{f}" for a in ("pitch", "roll", "yaw") for f in ("Des", "FB", "U")]

FB_COL = {"pitch": 3, "roll": 6}           # driver_inputs layout: armed, phase, (Des, FB, U) x pitch roll yaw ...
DEG2RAD = 0.0174533                         # MRAC_DEG2RAD, API/mrac.c: rate FB enters the law in rad/s
REPO = Path(__file__).resolve().parents[2]
DRIVER = Path(__file__).with_name("mrac_log_replay_host.c")

PR_KNOBS = {"kappa_pr": 1.0}                # PROPOSED: mid-range of the 0x1D bound [0, 2]; no flight value
L3_KNOBS = {"lam_ang": 2.2}                 # PROPOSED: the sim-tuned value (docs/workflow-b/mrac-variants.md:25)


# ----------------------------------------------------------------------------- variants
def preset(controller: str, name: str) -> dict[str, float]:
    from ground_station.analysis.controller_descriptor import CONTROLLERS_DIR, load
    return dict(load(CONTROLLERS_DIR / f"{controller}.yaml").presets[name])


def knob_args(params: dict[str, float]) -> list[str]:
    """Descriptor symbols -> driver 'axis:field:value' args (the CMD 0x1D idx split)."""
    names = [f for f, _lo, _hi in VARIANT_FIELDS]
    out = []
    for sym, val in params.items():
        if sym.startswith("mrac_g_gamma["):
            out.append(f"{int(sym[13])}:{GAMMA_SCALE_FIELD}:{val}")
        else:
            member, field = sym.split(".")
            out.append(f"{VAR_AXES.index(member)}:{names.index(field)}:{val}")
    return out


def variants() -> dict[str, dict]:
    """name -> {args, rbf (build)}. V1/V2/V3 = the WP-27 campaign presets. Angles go in as logged (degrees):
    since WP-38 the firmware converts them (the 'V3 rad*' variant that pre-converted them is gone)."""
    v1 = preset("mrac_v1", "pid_ref")
    p_r = ("mrac_config_pitch", "mrac_config_roll")
    pr = {**v1, **{f"{m}.{k}": v for m in p_r + ("mrac_config_yaw",) for k, v in PR_KNOBS.items()}}
    l3 = {**v1, **{f"{m}.{k}": v for m in p_r for k, v in L3_KNOBS.items()}}
    v3 = preset("mrac_v3", "v3_rbf12")
    return {
        "OFF": dict(args=[], rbf=False),
        "V1 g1": dict(args=knob_args(v1), rbf=False),
        "V1 g0.25": dict(args=knob_args(preset("mrac_v1", "v1_refmodel")), rbf=False),
        "V2": dict(args=knob_args(preset("mrac_v2", "v2_sataware")), rbf=False),
        "PR*": dict(args=knob_args(pr), rbf=False),
        "3L*": dict(args=knob_args(l3), rbf=False),
        "V3": dict(args=knob_args(v3), rbf=True),
    }


def variants_3l() -> dict[str, dict]:
    """3L-v2 (overnight 2026-10-09) against its base vp6 = V1 refmodel (gamma x0.25) + layer 1 lam_ang 4 on pitch/roll.
    L2 = composite prediction-error law, D = self-tuning gain. b_axis 49 rad/s^2 per u = median fit_b on the
    2026-10-08 logs (pitch 46-51, roll 46-60); pe_delay 7 ticks = 35 ms, the buffer max (fit 40-50 ms).
    gamma_c, wc_pe, p_max, p_forget are PROPOSED: this replay is what ranks them."""
    base = {**preset("mrac_v1", "v1_refmodel"), **{f"{m}.lam_ang": 4.0 for m in ("mrac_config_pitch", "mrac_config_roll")}}
    pr = lambda **kw: {f"{m}.{k}": v for m in ("mrac_config_pitch", "mrac_config_roll") for k, v in kw.items()}
    l2 = lambda g, wc=20.0: pr(gamma_c=g, b_axis=49.0, pe_delay=7, wc_pe=wc)
    d = pr(p_max=4.0, p_forget=0.5)
    out = {"OFF": dict(args=[], rbf=False), "vp6": dict(args=knob_args(base), rbf=False),
           "vp6+D": dict(args=knob_args({**base, **d}), rbf=False)}
    for g in (2.0, 8.0, 12.0, 20.0):
        out[f"vp6+L2 g{g:g}"] = dict(args=knob_args({**base, **l2(g)}), rbf=False)
    out["vp6+L2 g8 wc8"] = dict(args=knob_args({**base, **l2(8.0, 8.0)}), rbf=False)
    out["vp6+L2 g8+D"] = dict(args=knob_args({**base, **l2(8.0), **d}), rbf=False)
    te_off = {f"mrac_g_gamma[{i}][*]": 0.0 for i in (0, 1)}   # tracking-error learning off on pitch/roll
    for g in (2.0, 4.0, 8.0, 12.0, 20.0):                       # gamma_c var-table bound is 20 (mrac.c)
        out[f"L2only g{g:g}"] = dict(args=knob_args({**base, **te_off, **l2(g)}), rbf=False)
    out["vp6+L2 g20 d4"] = dict(args=knob_args({**base, **l2(20.0), **pr(pe_delay=4)}), rbf=False)  # delay mismatch
    return out


VARIANT_SETS = {"wp27": variants, "3l": variants_3l}


# ----------------------------------------------------------------------------- host build
def build(out_dir: Path, rbf: bool) -> Path:
    """gcc the driver with only the API/mrac* sources (so the test stubs win), STRUCT6 or RBF12 build."""
    src = out_dir / "src"
    src.mkdir(exist_ok=True)
    for f in (REPO / "API").glob("mrac*.[ch]"):
        shutil.copy2(f, src / f.name)
    exe = out_dir / f"mrac_log_replay_{int(rbf)}.exe"
    if not exe.exists():
        cmd = ["gcc", "-std=c99", "-O2", "-ffp-contract=off", f"-DMRAC_VARIANT={int(rbf)}", str(DRIVER),
               *map(str, sorted(src.glob("mrac*.c"))), "-I", str(src), "-I", str(REPO / "API" / "tests" / "stubs"),
               "-lm", "-o", str(exe)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(res.stderr)
    return exe


def run_driver(exe: Path, inputs: np.ndarray, args: list[str], work: Path, inj: int = 1) -> np.ndarray:
    fin, fout = work / "in.f32", work / "out.f32"
    inputs.astype(np.float32).tofile(fin)
    res = subprocess.run([str(exe), str(fin), str(fout), *args, f"inj:{inj}", "simplex:2"], capture_output=True,
                         text=True, check=True)
    if any(ln.strip() == "set 0" for ln in res.stdout.splitlines()):
        raise ValueError(f"MRAC_VariantParamSet refused one of {args}")
    return np.fromfile(fout, dtype=np.float32).reshape(-1, N_OUT).astype(float)


# ----------------------------------------------------------------------------- log -> driver input
def mixer_deficit(throttle, u_gx, u_gy, u_gz, yaw_dir=YAW_MIX_DIR):
    """controller.c mrac_mixer_deficit on the flown mixer inputs (u_gyroy = -gyroyPID.U, StabilizerTask.c:1376),
    in MRAC units per axis (pitch, roll, yaw, z). Shadow logs: u_ad never reached the mixer, so this is exact."""
    uy, yz = -np.asarray(u_gy), yaw_dir * np.asarray(u_gz)

    def cut(m):
        return np.where(m > MOTOR_HI, m - MOTOR_HI, np.where(m < MOTOR_LO, m - MOTOR_LO, 0.0))
    d1, d2 = cut(throttle - uy - u_gx - yz), cut(throttle + uy + u_gx - yz)
    d3, d4 = cut(throttle - uy + u_gx + yz), cut(throttle + uy - u_gx + yz)
    return np.stack([-0.25 * (-d1 + d2 - d3 + d4) / TO_MIXER["pitch"], 0.25 * (-d1 + d2 + d3 - d4) / TO_MIXER["roll"],
                     0.25 * yaw_dir * (-d1 - d2 + d3 + d4) / TO_MIXER["yaw"], 0.25 * (d1 + d2 + d3 + d4) / TO_MIXER["z"]],
                    axis=1)


def driver_inputs(series: lc.Series, dt: float = DT) -> tuple[np.ndarray, np.ndarray, dict]:
    """(t, inputs[N, 20], info) on a dt grid; info has the airborne mask, flown injection flag and logged u_ad."""
    keys = REQUIRED + [k for k in (f"Ctrler.Z_ratePID.{f}" for f in ("Des", "FB", "U")) if k in series]
    t, g = lc.grid(series, keys + [k for k in ("imu_data.pit", "imu_data.rol", "Throttle_out") if k in series], dt)
    z = np.zeros(len(t))
    col = {k: g.get(k, z) for k in keys}
    zr = [g.get(f"Ctrler.Z_ratePID.{f}", z) for f in ("Des", "FB", "U")]
    arm_key = "DroneStatus.ARM_Status" if "DroneStatus.ARM_Status" in series else "status.arm"
    armed = lc.hold(series, arm_key, t, 1.0)
    phase = np.rint(lc.hold(series, "flight_phase", t, 0.0))
    u = {a: col[f"Ctrler.{RATE[a]}.U"] for a in ("pitch", "roll", "yaw")}
    if "Throttle_out" in g:
        udef = mixer_deficit(g["Throttle_out"], u["roll"], u["pitch"], u["yaw"],
                             lc.hold(series, "g_yaw_mix_dir", t, YAW_MIX_DIR))
    else:
        udef = np.zeros((len(t), 4))
    ins = np.column_stack([armed, phase] + [col[f"Ctrler.{RATE[a]}.{f}"] for a in ("pitch", "roll", "yaw")
                                            for f in ("Des", "FB", "U")]
                          + zr + [g.get("imu_data.pit", z), g.get("imu_data.rol", z), udef])
    logged = {a: lc.hold(series, f"mrac_state.{a if a != 'z' else 'z_rate'}.u_ad", t) for a in AXES}
    info = dict(airborne=lc.airborne(series, t), inj=lc.hold(series, "mrac_flags.output_injection_on", t, np.nan),
                logged_u_ad=logged, has_z="Ctrler.Z_ratePID.U" in series, has_throttle="Throttle_out" in g,
                fs_log=_rate(series, "Ctrler.gyroxPID.FB"))
    return t, ins, info


def _rate(series, key):
    ts = series[key][0]
    return float(1.0 / np.median(np.diff(ts))) if len(ts) > 2 else float("nan")


# ----------------------------------------------------------------------------- metrics
def longest_run(mask: np.ndarray, dt: float = DT) -> float:
    m = np.r_[False, np.asarray(mask, bool), False]
    e = np.flatnonzero(np.diff(m.astype(int)))
    return float(max((b - a for a, b in zip(e[::2], e[1::2])), default=0) * dt)


def runs_at_least(mask: np.ndarray, hold_s: float, dt: float = DT) -> int:
    m = np.r_[False, np.asarray(mask, bool), False]
    e = np.flatnonzero(np.diff(m.astype(int)))
    return int(sum((b - a) * dt >= hold_s for a, b in zip(e[::2], e[1::2])))


def growth_s(norm: np.ndarray, dt: float = DT, smooth_s: float = 0.5) -> float:
    """Longest span over which the (smoothed) Theta norm keeps rising."""
    n = max(1, int(smooth_s / dt))
    s = np.convolve(norm, np.ones(n) / n, mode="same")
    return longest_run(np.diff(s, prepend=s[0]) > 1e-9, dt)


def axis_metrics(out: np.ndarray, udef: np.ndarray, mask: np.ndarray, ax: int, dt: float = DT) -> dict:
    u_ad, u_nom, th = out[:, ax], out[:, 4 + ax], out[:, 8 + ax]
    m = mask & (out[:, 16] > 0.5)                                   # airborne and learn-gated
    if not m.any():
        return dict(n=0)
    over = m & (np.abs(u_ad) > ABORT_RATIO * np.abs(u_nom))
    th_m = th[m][int(WARMUP_S / dt):]
    return dict(
        n=int(m.sum()), rms_u_ad=float(np.sqrt(np.mean(u_ad[m] ** 2))), rms_u_nom=float(np.sqrt(np.mean(u_nom[m] ** 2))),
        over_frac=float(over.sum() / m.sum()), over_max_s=longest_run(over, dt), aborts=runs_at_least(over, ABORT_HOLD_S, dt),
        th_end=float(th[m][-1]), th_max=float(th[m].max()),
        th_growth_s=growth_s(th_m, dt) if len(th_m) > 1 else float("nan"),
        udef_frac=float(np.mean(np.abs(udef[m]) > 1e-9)), finite=bool(np.all(np.isfinite(u_ad))))


def rbf_spread(out: np.ndarray, mask: np.ndarray, ax: int) -> dict:
    """V3 grid use on the gated ticks: Gaussians that ever reach 0.5, and the share of activation in the top one."""
    phi = out[:, 19 + ax * N_RBF: 19 + (ax + 1) * N_RBF][mask & (out[:, 16] > 0.5)]
    if not len(phi):
        return dict(active=0, top_share=float("nan"), mean_sum=float("nan"))
    tot = phi.sum(axis=0)
    return dict(active=int((phi.max(axis=0) > 0.5).sum()),
                top_share=float(tot.max() / tot.sum()) if tot.sum() > 0 else float("nan"),
                mean_sum=float(phi.sum(axis=1).mean()))


def air_runs(air: np.ndarray, min_s: float = 5.0, trim_s: float = 1.0, dt: float = DT) -> list[tuple[int, int]]:
    """Airborne runs of at least min_s, trim_s cut at both ends (take-off and landing transients)."""
    e = np.flatnonzero(np.diff(np.r_[False, air, False].astype(int)))
    k = int(round(trim_s / dt))
    return [(a + k, b - k) for a, b in zip(e[::2], e[1::2]) if (b - a) * dt >= min_s]


def needed(ins: np.ndarray, u_applied: np.ndarray, runs: list, ax: str):
    """-Delta_hat per run on the 100 Hz grid, as adaptive_review.uncertainty: fit b and tau on 2-8 Hz, then
    negd = LPF3(u(t - tau)) - LPF3(xdot)/b. The ideal u_ad equals it."""
    x = ins[:, FB_COL[ax]] * DEG2RAD
    pairs = [(np.gradient(x[a:b:2]) * ar.FS, u_applied[a:b:2]) for a, b in runs]
    b, k, r = ar.fit_b(pairs)
    return (float(b), int(k), float(r)), [ar.filt(ar.delay(u, k), hi=3.0) - ar.filt(xd, hi=3.0) / b for xd, u in pairs]


def cancel_metrics(u_ad: np.ndarray, runs: list, nd: list) -> dict:
    """cancel = 1 - var(u_ad - negd) / var(negd) over all runs (per-run means removed: the dynamic part);
    1 = cancels all, 0 = no help, < 0 adds disturbance. Band phase of u_ad against negd in ar.BAND (ideal 0)."""
    v = np.concatenate([u_ad[a:b:2] - u_ad[a:b:2].mean() for a, b in runs])
    n = np.concatenate([x - x.mean() for x in nd])
    if not np.isfinite(v).all() or np.var(n) < 1e-15:
        return {}
    ph, gain, coh = ar.band_tf(n, v)
    return dict(cancel=float(1.0 - np.var(v - n) / np.var(n)), band_phase=float(ph), band_gain=float(gain),
                band_coh=float(coh), rms_ratio=float(np.std(v) / np.std(n)))


def replay_log(series: lc.Series, work: Path, which: dict | None = None, shadow: bool = False) -> dict:
    t, ins, info = driver_inputs(series)
    air = info["airborne"]
    inj = info["inj"][air]
    res = dict(airborne_s=float(air.sum() * DT), fs_log=info["fs_log"], has_z=info["has_z"],
               has_throttle=info["has_throttle"], inj_flown=float(np.nanmax(inj)) if np.isfinite(inj).any() else None,
               variants={})
    if not air.any():
        return res
    inj_flown = int((res["inj_flown"] or 0) > 0.5)
    runs, nd = air_runs(air), {}
    res["cancel_runs_s"] = float(sum(b - a for a, b in runs) * DT)
    for name, v in (which or variants()).items():
        x = ins.copy()
        # OFF replays the flown injection flag, so its u_ad can be checked against the logged one. --shadow: every
        # variant computes u_ad without injecting it, exact on PID logs (the replayed u_ad never reached the drone)
        out = run_driver(build(work, v["rbf"]), x, v["args"], work, inj_flown if name == "OFF" or shadow else 1)
        if name == "OFF" and runs:
            for i, a in enumerate(("pitch", "roll")):     # applied u = u_nom + the flown u_ad when it was injected
                lu = np.nan_to_num(info["logged_u_ad"][a]) * inj_flown
                res.setdefault("fit", {})[a], nd[a] = needed(ins, out[:, 4 + i] + lu, runs, a)
        row = {a: axis_metrics(out, ins[:, 16 + i], air, i) for i, a in enumerate(AXES) if a != "z" or info["has_z"]}
        row["would_trips"] = int(out[-1, 18])
        row["trip_reasons"] = sorted({int(r) for r in out[air, 17] if r})
        if v["rbf"]:
            row["rbf"] = {a: rbf_spread(out, air, i) for i, a in enumerate(("pitch", "roll"))}
        for i, a in enumerate(("pitch", "roll")):
            if a in nd and a in row:
                row[a].update(cancel_metrics(out[:, i], runs, nd[a]))
        if name == "OFF":
            for i, a in enumerate(AXES):
                lu = info["logged_u_ad"][a]
                ok = air & np.isfinite(lu) & (out[:, 16] > 0.5)
                if ok.sum() > 50 and np.std(lu[ok]) > 0 and np.std(out[ok, i]) > 0:
                    row.setdefault(a, {})["corr_logged"] = float(np.corrcoef(lu[ok], out[ok, i])[0, 1])
        res["variants"][name] = row
    return res


# ----------------------------------------------------------------------------- report
def to_markdown(results: dict) -> str:
    lines = ["| log | variant | axis | rms u_ad / u_nom | >0.5 u_nom frac / max s / aborts | |Theta| end / max / grow s "
             "| u_def frac | corr logged |", "|---|---|---|---|---|---|---|---|"]
    for log, r in results.items():
        for name, row in r.get("variants", {}).items():
            for a in AXES:
                m = row.get(a)
                if not m or not m.get("n"):
                    continue
                lines.append(f"| {log} | {name} | {a} | {m['rms_u_ad']:.3f} / {m['rms_u_nom']:.3f} | "
                             f"{m['over_frac']:.2f} / {m['over_max_s']:.1f} / {m['aborts']} | {m['th_end']:.3f} / "
                             f"{m['th_max']:.3f} / {m['th_growth_s']:.1f} | {m['udef_frac']:.3f} | "
                             f"{m.get('corr_logged', float('nan')):.2f} |")
    return "\n".join(lines)


def summarize(results: dict) -> str:
    """One row per variant and axis across logs: medians, worst cases and abort-rule counts."""
    lines = ["| variant | axis | logs | med rms u_ad/u_nom | med frac >0.5 u_nom | logs w/ abort | max grow s (>5 s logs) "
             "| max |Theta| | u_def>0 frac | V3 RBF active / top share |", "|---|---|---|---|---|---|---|---|---|---|"]
    names = list(next((r["variants"] for r in results.values() if r.get("variants")), {}))
    for name in names:
        for a in AXES:
            ms = [r["variants"][name][a] for r in results.values()
                  if name in r.get("variants", {}) and r["variants"][name].get(a, {}).get("n")]
            if not ms:
                continue
            g = [m["th_growth_s"] for m in ms if np.isfinite(m["th_growth_s"])]
            rb = [r["variants"][name]["rbf"][a] for r in results.values()
                  if "rbf" in r.get("variants", {}).get(name, {}) and a in r["variants"][name]["rbf"]]
            rbs = (f"{np.median([x['active'] for x in rb]):.0f} / {np.nanmedian([x['top_share'] for x in rb]):.2f}"
                   if rb else "-")
            lines.append(f"| {name} | {a} | {len(ms)} | {np.median([m['rms_u_ad'] / m['rms_u_nom'] for m in ms]):.2f} | "
                         f"{np.median([m['over_frac'] for m in ms]):.2f} | {sum(m['aborts'] > 0 for m in ms)} | "
                         f"{max(g, default=float('nan')):.1f} ({sum(x > GROWTH_MAX_S for x in g)}) | "
                         f"{max(m['th_max'] for m in ms):.3f} | {np.median([m['udef_frac'] for m in ms]):.3f} | {rbs} |")
    return "\n".join(lines)


def cancel_table(results: dict) -> str:
    """Pitch/roll swing cancellation per variant: median and range of cancel over logs, median band phase."""
    lines = ["| variant | axis | logs | cancel med (min..max) | band phase med deg | rms u_ad / rms needed |",
             "|---|---|---|---|---|---|"]
    names = list(next((r["variants"] for r in results.values() if r.get("variants")), {}))
    for name in names:
        for a in ("pitch", "roll"):
            ms = [r["variants"][name][a] for r in results.values() if "cancel" in r["variants"].get(name, {}).get(a, {})]
            if ms:
                c = [m["cancel"] for m in ms]
                lines.append(f"| {name} | {a} | {len(ms)} | {np.median(c):+.2f} ({min(c):+.2f}..{max(c):+.2f}) | "
                             f"{np.median([m['band_phase'] for m in ms]):+.0f} | "
                             f"{np.median([m['rms_ratio'] for m in ms]):.2f} |")
    return "\n".join(lines)


def fill_pid_u(series: lc.Series) -> None:
    """stream_log presets log mrac_state.<ax>.u_nom, not Ctrler.<rate>PID.U: rebuild U exactly (mrac.c:1329-1332)."""
    for a, st in (("pitch", "pitch"), ("roll", "roll"), ("yaw", "yaw"), ("z", "z_rate")):
        k, src = f"Ctrler.{RATE[a]}.U", f"mrac_state.{st}.u_nom"
        if k not in series and src in series:
            t, v = series[src]
            series[k] = (t, v * TO_MIXER[a])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(REPO), help="checkout whose logs/ to scan")
    ap.add_argument("--json", help="write the full results here")
    ap.add_argument("--only", nargs="*", help="log names to replay (default: every log with the MRAC inputs)")
    ap.add_argument("--per-log", action="store_true", help="print one row per log instead of the summary")
    ap.add_argument("--set", choices=sorted(VARIANT_SETS), default="wp27", help="variant set (3l = 3L-v2 vs vp6)")
    ap.add_argument("--shadow", action="store_true",
                    help="no variant injects (exact on PID logs: the composite law sees the u that was flown)")
    args = ap.parse_args(argv)
    results = {}
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        for ref in lc.find_logs(args.root):
            if args.only and not {ref.name, ref.name.rsplit("/", 1)[-1]} & set(args.only):
                continue
            s = lc.load(ref)
            fill_pid_u(s)
            if not all(k in s for k in REQUIRED + ["flight_phase"]):
                continue
            r = replay_log(s, work, VARIANT_SETS[args.set](), args.shadow)
            if r["variants"]:
                results[ref.name] = r
                print(f"{ref.name}: airborne {r['airborne_s']:.1f} s, log {r['fs_log']:.0f} Hz", flush=True)
    print(to_markdown(results) if args.per_log else summarize(results))
    print(cancel_table(results))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1, default=float), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
