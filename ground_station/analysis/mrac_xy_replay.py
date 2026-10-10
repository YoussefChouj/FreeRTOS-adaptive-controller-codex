"""Open-loop replay of the x/y MRAC velocity layers (MRAC_AXIS_X/Y) on flight logs, through the host-built law.

API/mrac*.c are compiled with gcc around mrac_xy_replay_host.c (as mrac_log_replay.py does for the 4 attitude/Z
axes). Every log with Ctrler.locx/ysPID.Des/FB/U is resampled to MRAC_DT (5 ms) and fed to MRAC_Control with x/y
learning and injection on from power-on. The plant does NOT respond (the logs are PID-only flights): this shows
the tracking error the law would see, whether it would sit in tanh saturation (e_sat) or hard freeze (e_freeze),
and how large u_ad and Theta would grow, not how the closed loop would change.

Logs without flight_phase use a proxy airborne mask: position loop active (any of x/y Des or U nonzero).

    python -m ground_station.analysis.mrac_xy_replay [--md out.md] [--ref-bw 2]

--ref-bw runs x/y with a first-order reference model at that bandwidth (rad/s) instead of the boot passthrough.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from ground_station.analysis import log_corpus as lc

REPO = Path(__file__).resolve().parents[2]
DRIVER = Path(__file__).with_name("mrac_xy_replay_host.c")
DT = 0.005                       # MRAC_DT, API/mrac.h
N_OUT = 9
E_SAT, E_FREEZE = 0.4, 1.2       # mrac_config_z column of MRAC_SET (mrac.c), copied into mrac_config_x/y
U_MAX = 1.0                      # mrac_config_x.u_max, m/s^2 (PROPOSED in mrac.c)
KEYS = [f"Ctrler.{p}.{f}" for p in ("locxsPID", "locysPID") for f in ("Des", "FB", "U")]


def build(out_dir: Path) -> Path:
    src = out_dir / "src"
    src.mkdir(exist_ok=True)
    for f in (REPO / "API").glob("mrac*.[ch]"):
        shutil.copy2(f, src / f.name)
    exe = out_dir / "mrac_xy_replay.exe"
    cmd = ["gcc", "-std=c99", "-O2", "-ffp-contract=off", str(DRIVER), *map(str, sorted(src.glob("mrac*.c"))),
           "-I", str(src), "-I", str(REPO / "API" / "tests" / "stubs"), "-lm", "-o", str(exe)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(res.stderr)
    return exe


def has_xy(ref: lc.LogRef) -> bool:
    """Cheap pre-filter: the x/y U key in a vofa slot header or in the first 4 MB of a session telemetry.csv."""
    if ref.kind == "vofa":
        stem = ref.path.name[: -len(".meta.json")]
        return any("locxsPID.U" in p.open(errors="ignore").readline() for p in ref.path.parent.glob(f"{stem}.slot*.csv"))
    if ref.kind == "session":
        for p in (ref.path / "telemetry.csv", ref.path / "raw" / "telemetry.csv"):
            if p.exists():
                with p.open(errors="ignore") as fh:
                    return "locxsPID.U" in fh.read(4 << 20)
    return False


def inputs(series: lc.Series) -> tuple[np.ndarray, np.ndarray]:
    t, g = lc.grid(series, KEYS, DT)
    if len(t) == 0:
        return t, np.zeros((0, 8))
    if "flight_phase" in series:
        arm_key = "DroneStatus.ARM_Status" if "DroneStatus.ARM_Status" in series else "status.arm"
        armed = lc.hold(series, arm_key, t, 1.0)
        phase = np.rint(lc.hold(series, "flight_phase", t, 0.0))
    else:
        active = sum(np.abs(np.nan_to_num(g[k])) for k in KEYS if not k.endswith(".FB")) > 0
        armed, phase = active.astype(float), active.astype(float)      # 1 = MRAC_PHASE_FLYING
    cols = [np.nan_to_num(g[k]) for k in KEYS]
    return t, np.column_stack([armed, phase] + cols)


def run(exe: Path, ins: np.ndarray, work: Path, ref_bw: float | None = None) -> np.ndarray:
    fin, fout = work / "in.f32", work / "out.f32"
    ins.astype(np.float32).tofile(fin)
    extra = [] if ref_bw is None else [str(ref_bw)]
    subprocess.run([str(exe), str(fin), str(fout), *extra], check=True, capture_output=True)
    return np.fromfile(fout, dtype=np.float32).reshape(-1, N_OUT).astype(float)


def stats(name: str, ins: np.ndarray, out: np.ndarray) -> list[dict] | None:
    gate = out[:, 8] > 0.5
    if gate.sum() < int(2.0 / DT):
        return None
    rows = []
    for a, ax in enumerate("xy"):
        e, u, th = out[gate, 2 + a], out[gate, a], out[:, 4 + a]
        unom = ins[gate, 4 + 3 * a] / 100.0
        rows.append(dict(log=name, axis=ax, s=gate.sum() * DT, finite=bool(np.isfinite(out).all()),
                         e_p50=np.percentile(np.abs(e), 50), e_p99=np.percentile(np.abs(e), 99),
                         e_max=np.abs(e).max(), f_sat=float(np.mean(np.abs(e) > E_SAT)),
                         f_freeze=float(np.mean(np.abs(e) > E_FREEZE)), u_max=np.abs(u).max(),
                         f_clamp=float(np.mean(np.abs(u) >= 0.99 * U_MAX)), unom_p99=np.percentile(np.abs(unom), 99),
                         th_max=np.nanmax(th), th_end=th[-1]))
    return rows


def table(rows: list[dict]) -> str:
    head = ("| log | ax | gated s | abs e p50 / p99 / max m/s | > e_sat | > e_freeze | max abs u_ad | at u_max | "
            "u_nom p99 | Theta max / end |")
    lines = [head, "|" + "---|" * 10]
    for r in rows:
        lines.append(f"| {r['log']} | {r['axis']} | {r['s']:.0f} | {r['e_p50']:.2f} / {r['e_p99']:.2f} / "
                     f"{r['e_max']:.2f} | {100 * r['f_sat']:.1f}% | {100 * r['f_freeze']:.1f}% | {r['u_max']:.2f} | "
                     f"{100 * r['f_clamp']:.1f}% | {r['unom_p99']:.2f} | {r['th_max']:.2f} / {r['th_end']:.2f} |"
                     + ("" if r["finite"] else " NON-FINITE"))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--md", type=Path, help="write the table here")
    ap.add_argument("--ref-bw", type=float, help="first-order reference model bandwidth for x/y, rad/s")
    args = ap.parse_args()
    rows: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        exe = build(work)
        for ref in lc.find_logs():
            if not has_xy(ref):
                continue
            series = lc.load(ref)
            if not all(k in series for k in KEYS):
                continue
            t, ins = inputs(series)
            if len(t) == 0:
                continue
            r = stats(ref.name, ins, run(exe, ins, work, args.ref_bw))
            if r:
                rows += r
    md = table(rows)
    all_e = [r for r in rows]
    summary = (f"\n\n{len(rows) // 2} logs, x+y. Worst e p99 {max(r['e_p99'] for r in all_e):.2f} m/s "
               f"(e_sat {E_SAT}, e_freeze {E_FREEZE}); worst abs u_ad {max(r['u_max'] for r in all_e):.2f} "
               f"(u_max {U_MAX}); all finite: {all(r['finite'] for r in all_e)}.") if rows else "\nno logs"
    print(md + summary)
    if args.md:
        args.md.write_text(md + summary + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
