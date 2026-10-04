#!/usr/bin/env python3
"""Differential trace of TASK/StabilizerTask.c: the file at a git ref vs the working tree (WP-37).

Builds tools/fw_trace/stab_trace.c twice with host gcc, once around each version of the file (and that tree's
API/pid.c), runs both on the same seeded random ticks and compares the hash checkpoints. Equal hashes mean the two
versions made the same external calls with the same arguments in the same order and left the same state on every
tick. `--cov` also builds the new version with gcov and prints the line coverage of TASK/StabilizerTask.c, so a pass
says how much of the file the random ticks reached. Never touches OBJ/.

    python tools/fw_trace.py                       # vs HEAD, 4 seeds x 200000 ticks
    python tools/fw_trace.py --base wp/36 --cov
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
HARNESS = REPO / "tools" / "fw_trace" / "stab_trace.c"
CFLAGS = ["-std=gnu99", "-O0", "-msse2", "-mfpmath=sse", "-ffp-contract=off", "-fno-strict-aliasing", "-w"]


def build(gcc: str, root: Path, exe: Path, cov: bool) -> None:
    src = (root / "TASK" / "StabilizerTask.c").as_posix()
    args = [gcc, *CFLAGS, *(["--coverage"] if cov else []), *fw_equiv.flags(root), f'-DSTAB_SRC="{src}"',
            str(HARNESS), str(root / "API" / "pid.c"), "-lm", "-o", str(exe)]
    res = subprocess.run(args, cwd=exe.parent, capture_output=True, text=True, errors="replace")
    if res.returncode != 0:
        raise SystemExit(f"build {root}:\n{res.stderr[-3000:]}")


def run(exe: Path, ticks: int, seed: int, every: int) -> list[str]:
    res = subprocess.run([str(exe), str(ticks), str(seed), str(every)], cwd=exe.parent, capture_output=True,
                         text=True, timeout=1800)
    if res.returncode != 0:
        raise SystemExit(f"run {exe} seed {seed}: exit {res.returncode}\n{res.stderr[-2000:]}")
    return res.stdout.splitlines()


def coverage(gcov: str, out: Path) -> str:
    res = subprocess.run([gcov, "-n", "-o", str(out), str(out / "stab_trace.c")], cwd=out, capture_output=True,
                         text=True, errors="replace")
    text = res.stdout
    m = re.search(r"File '[^']*StabilizerTask\.c'\s*\nLines executed:([\d.]+)% of (\d+)", text)
    return f"{m.group(1)}% of {m.group(2)} lines" if m else "not found:\n" + text[-1500:]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        base = t / "base"
        tar = subprocess.run(["git", "archive", args.base, *fw_equiv.ARCHIVE], cwd=REPO, capture_output=True, check=True)
        tarfile.open(fileobj=io.BytesIO(tar.stdout)).extractall(base)
        (t / "a").mkdir()
        (t / "b").mkdir()
        exe_a, exe_b = t / "a" / "trace.exe", t / "b" / "trace.exe"
        build(gcc, base, exe_a, False)
        build(gcc, REPO, exe_b, args.cov)
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
            print("coverage TASK/StabilizerTask.c (new):", coverage(gcov, t / "b") if gcov else "gcov not on PATH")
    print("FW-TRACE OK" if ok else "FW-TRACE DIFFER")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
