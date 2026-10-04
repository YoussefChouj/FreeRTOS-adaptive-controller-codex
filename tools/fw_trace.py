#!/usr/bin/env python3
"""Differential trace of a firmware file: the version at a git ref vs the working tree (WP-37).

Builds a harness of tools/fw_trace/ twice with host gcc, once around each version of the file (and that tree's
API/pid.c and API/controller.c), runs both on the same seeded random ticks and compares the hash checkpoints. Equal
hashes mean the two versions made the same external calls with the same arguments in the same order and left the
same state on every tick. Targets:
  stab  TASK/StabilizerTask.c, one control tick (tools/fw_trace/stab_trace.c)
  cmd   TASK/send_data.c, the ground-station command path (tools/fw_trace/cmd_trace.c)
  mrac  API/mrac.c, MRAC_Control with random flags and variant fields (tools/fw_trace/mrac_trace.c)
Functions a file references but its harness never runs are linked as empty dummies (generated from the linker's
undefined-reference list). `--cov` also builds the new version with gcov and prints the line coverage of the code
the target exercises. Never touches OBJ/.

    python tools/fw_trace.py                          # stab vs HEAD, 4 seeds x 200000 ticks
    python tools/fw_trace.py cmd --base wp/36 --cov
"""
from __future__ import annotations

import argparse
import io
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fw_equiv  # noqa: E402  (include path, defines, git archive list)

REPO = fw_equiv.REPO
CFLAGS = ["-std=gnu99", "-O0", "-msse2", "-mfpmath=sse", "-ffp-contract=off", "-fno-strict-aliasing", "-w"]
# API/controller.c is linked for the mixer table (Mix_Motor); its entry points are renamed so the harness's
# recording stubs of Controller_Update/CheckSwitch/Init stay the ones the file under test calls.
RENAME = ["-DController_Update=real_Controller_Update", "-DController_CheckSwitch=real_Controller_CheckSwitch",
          "-DController_Init=real_Controller_Init"]

#        harness          macro       file under test          also linked (tree)   functions --cov reports (regex)
TARGETS = {
    "stab": ("stab_trace.c", "STAB_SRC", "TASK/StabilizerTask.c", ["API/pid.c", "ctrl"], None),  # whole file runs
    "cmd":  ("cmd_trace.c",  "SEND_SRC", "TASK/send_data.c",      ["API/pid.c", "ctrl"],
             r"Cmd_\w+|GsCmd_Dispatch|Process_GroundStation_Command|MracElemParamApply|CommandSafetyReject|"
             r"SendTransactionResult|TransactionWasSeen|RememberTransaction|GroundStation_AbortAllPaths"),
    "mrac": ("mrac_trace.c", "MRAC_SRC", "API/mrac.c",            ["API/mrac_math.c"], None),
}


def run_ok(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, errors="replace")


def build(gcc: str, target: str, root: Path, exe: Path, cov: bool) -> None:
    harness, macro, rel, extra, _ = TARGETS[target]
    out = exe.parent
    ctrl, dummy_c, dummy_o = out / "controller.o", out / "dummies.c", out / "dummies.o"
    if "ctrl" in extra:
        res = run_ok([gcc, *CFLAGS, "-c", *fw_equiv.flags(root), *RENAME, str(root / "API" / "controller.c"), "-o",
                      str(ctrl)], out)
        if res.returncode != 0:
            raise SystemExit(f"build {root} controller.c:\n{res.stderr[-3000:]}")
    link = [gcc, *CFLAGS, *(["--coverage"] if cov else []), *fw_equiv.flags(root),
            f'-D{macro}="{(root / rel).as_posix()}"', str(REPO / "tools" / "fw_trace" / harness),
            *(str(ctrl) if e == "ctrl" else str(root / e) for e in extra)]
    res = run_ok([*link, "-lm", "-o", str(exe)], out)
    missing = sorted(set(re.findall(r"undefined reference to `_?(\w+)'", res.stderr)))
    if res.returncode != 0 and missing:
        dummy_c.write_text("".join(f"void {m}(void) {{ }}\n" for m in missing))
        res = run_ok([gcc, *CFLAGS, "-c", str(dummy_c), "-o", str(dummy_o)], out)
        if res.returncode == 0:
            res = run_ok([*link, str(dummy_o), "-lm", "-o", str(exe)], out)
    if res.returncode != 0:
        raise SystemExit(f"build {root}:\n{res.stderr[-3000:]}")


def run(exe: Path, ticks: int, seed: int, every: int) -> list[str]:
    res = subprocess.run([str(exe), str(ticks), str(seed), str(every)], cwd=exe.parent, capture_output=True,
                         text=True, timeout=1800)
    if res.returncode != 0:
        raise SystemExit(f"run {exe} seed {seed}: exit {res.returncode}\n{res.stderr[-2000:]}")
    return res.stdout.splitlines()


def coverage(gcov: str, out: Path, harness: str, rel: str, funcs: str | None) -> str:
    """Line coverage of the functions of `rel` matching `funcs` (gcov -f per-function summaries)."""
    text = run_ok([gcov, "-n", "-f", "-o", str(out), str(out / harness)], out).stdout
    name = Path(rel).name
    done = total = nfun = 0
    for m in re.finditer(r"Function '([^']+)'\nLines executed:([\d.]+)% of (\d+)", text):
        fn = m.group(1).lstrip("_")
        if funcs and re.fullmatch(funcs, fn):
            n = int(m.group(3))
            done += round(float(m.group(2)) * n / 100.0)
            total += n
            nfun += 1
    whole = re.search(r"File '[^']*%s'\s*\nLines executed:([\d.]+)%% of (\d+)" % re.escape(name), text)
    part = f"{100.0 * done / total:.2f}% of {total} lines in {nfun} functions; " if total else ""
    return part + (f"whole file {whole.group(1)}% of {whole.group(2)} lines" if whole else "file not in the gcov output")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", nargs="?", default="stab", choices=sorted(TARGETS))
    ap.add_argument("--base", default="HEAD")
    ap.add_argument("--ticks", type=int, default=200000)
    ap.add_argument("--seeds", default="1,2,3,4")
    ap.add_argument("--every", type=int, default=1000, help="checkpoint period, ticks")
    ap.add_argument("--cov", action="store_true")
    args = ap.parse_args(argv)
    gcc = shutil.which("gcc")
    if gcc is None:
        print("FAIL fw_trace: gcc not on PATH")
        return 1
    harness, _, rel, _, funcs = TARGETS[args.target]
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        base = t / "base"
        tar = subprocess.run(["git", "archive", args.base, *fw_equiv.ARCHIVE], cwd=REPO, capture_output=True, check=True)
        tarfile.open(fileobj=io.BytesIO(tar.stdout)).extractall(base)
        (t / "a").mkdir()
        (t / "b").mkdir()
        exe_a, exe_b = t / "a" / "trace.exe", t / "b" / "trace.exe"
        build(gcc, args.target, base, exe_a, False)
        build(gcc, args.target, REPO, exe_b, args.cov)
        for seed in (int(s) for s in args.seeds.split(",")):
            a = run(exe_a, args.ticks, seed, args.every)
            b = run(exe_b, args.ticks, seed, args.every)
            first = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), None)
            if first is None and len(a) == len(b):
                print(f"seed {seed}: same, {b[-1]}")
            else:
                ok = False
                where = a[first] if first is not None else "length"
                print(f"seed {seed}: DIFFER at checkpoint {first}: base '{where}' new '{b[first] if first is not None else ''}'")
        if args.cov:
            gcov = shutil.which("gcov")
            print(f"coverage {rel} (new):", coverage(gcov, t / "b", harness, rel, funcs) if gcov else "gcov not on PATH")
    print(f"FW-TRACE {args.target} OK" if ok else f"FW-TRACE {args.target} DIFFER")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
