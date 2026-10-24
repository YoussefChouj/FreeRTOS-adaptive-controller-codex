#!/usr/bin/env python3
"""All-loop MRAC equivalence runner: with every new layer off, the output is bit-identical to the base tree.

Spec: docs/design/2026-10-24-all-loop-mrac-layers.md item 1 (and its safety rule: "with every new layer off
(the default), the output is bit-identical to today's firmware").

Two independent proofs, because the new work touches two different files:

  mrac  API/mrac.c vs the base tree, tick by tick, over the scenarios of API/tests/test_mrac_equiv.c
        (every flag combination, the simplex, the sigma-prior case).  Plain and sigma-prior builds.
  stab  TASK/StabilizerTask.c vs the base tree, over the seeded random control ticks of
        tools/fw_trace/stab_trace.c.  The hash covers every external call (id + arguments, in order) and the
        state the file owns, so equal hashes mean the same control path was taken with the same numbers.

Both are "the base tree" at --base (default: the commit this branch was cut from).  The new layers are off by
default, so the two trees must agree bit for bit; the first commit that breaks this is the bug.

The mrac half is a copy of the harness of API/tests/run_mrac_equiv.py (same driver, same compare), so the
numbers it prints are the ones that suite prints.  It is repeated here because that runner has its own base
(91ce5287) and its own file set (API/mrac* only): the all-loop work also changes TASK/StabilizerTask.c, and
the two proofs must be read together.

  python API/tests/run_all_loop_equiv.py --base <commit>
  python API/tests/run_all_loop_equiv.py --base <commit> --only stab
"""
from __future__ import annotations

import argparse
import io
import itertools
import shutil
import subprocess
import sys
import tarfile
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from API.tests.run_mrac_equiv import (  # noqa: E402  (the harness is shared, not copied)
    build_binary,
    compare_streams,
    copy_working_tree,
    extract_base_tree,
    gate_defs,
)

CHUNK = 1 << 20
# The fw_trace harness links API/pid.c and API/controller.c of the tree under test and the file under test
# itself; the base tree needs the same set, so the archive covers them.
FW_ARCHIVE = ["USER", "TASK", "Global_file", "BSP", "API", "firmware", "NN"]


def mrac_proof(base_rev: str, tmp: Path) -> tuple[bool, str]:
    """API/mrac.c, tick by tick, plain and sigma-prior. Returns (ok, text)."""
    driver = REPO / "API" / "tests" / "test_mrac_equiv.c"
    stubs = REPO / "API" / "tests" / "stubs"

    ref_dir, new_dir = tmp / "mrac_ref", tmp / "mrac_new"
    ref_dir.mkdir()
    new_dir.mkdir()
    extract_base_tree(base_rev, ref_dir, REPO)
    copy_working_tree(new_dir, REPO)

    ref_defs = gate_defs(ref_dir)
    new_defs = gate_defs(new_dir)
    builds = {
        "ref_plain": (ref_dir, ref_defs),
        "new_plain": (new_dir, new_defs),
        "ref_sigma": (ref_dir, ["-DMRAC_ENABLE_SIGMA_PRIOR"] + ref_defs),
        "new_sigma": (new_dir, ["-DMRAC_ENABLE_SIGMA_PRIOR"] + new_defs),
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [pool.submit(build_binary, tree, driver, stubs, tmp / name, extra_flags=flags)
                for name, (tree, flags) in builds.items()]
        exes = {name: job.result() for name, job in zip(builds, jobs)}
        runs = [pool.submit(compare_streams, exes["ref_" + v], exes["new_" + v]) for v in ("plain", "sigma")]
        results = [r.result() for r in runs]

    lines = []
    for n_lines, _cov, info in results:
        if info is not None:
            return False, (f"MISMATCH at line {info['line_no']}: tag '{info['tag']}' "
                           f"ref={info['ref_hex']} ({info['ref_flt']:.6e}) != "
                           f"new={info['new_hex']} ({info['new_flt']:.6e})")
        lines.append(n_lines)
    return True, f"mrac: {lines[0]} lines identical (plain) + {lines[1]} (sigma-prior)"


def _archive_tree(base_rev: str, dest: Path) -> None:
    tar = subprocess.run(["git", "archive", base_rev, *FW_ARCHIVE], cwd=REPO, capture_output=True, check=True)
    safe = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
    tarfile.open(fileobj=io.BytesIO(tar.stdout)).extractall(dest, **safe)


def stab_proof(base_rev: str, tmp: Path, ticks: int, seeds: list[int]) -> tuple[bool, str]:
    """TASK/StabilizerTask.c, seeded random control ticks, hash-equal means the same calls in the same order."""
    from tools import fw_equiv, fw_trace

    gcc = shutil.which("gcc")
    if gcc is None:
        return True, "stab: SKIP (gcc not on PATH)"

    base, new = tmp / "stab_base", tmp / "stab_new"
    base.mkdir()
    _archive_tree(base_rev, base)

    exe_a, exe_b = tmp / "stab_a" / "trace.exe", tmp / "stab_b" / "trace.exe"
    exe_a.parent.mkdir()
    exe_b.parent.mkdir()
    try:
        fw_trace.build(gcc, "stab", base, exe_a, False, True)
        fw_trace.build(gcc, "stab", REPO, exe_b, False, False)
    except SystemExit as e:
        return False, f"stab: build failed: {e}"

    out = []
    ok = True
    for seed in seeds:
        a = fw_trace.run(exe_a, ticks, seed, max(1, ticks // 10))
        b = fw_trace.run(exe_b, ticks, seed, max(1, ticks // 10))
        if a != b:
            for i, (x, y) in enumerate(zip(a, b)):
                if x != y:
                    out.append(f"seed {seed}: DIFFER at checkpoint {i}: {x!r} != {y!r}")
                    break
            else:
                out.append(f"seed {seed}: DIFFER (checkpoint count {len(a)} vs {len(b)})")
            ok = False
        else:
            out.append(f"seed {seed}: same ({a[-1] if a else 'no checkpoint'})")
    return ok, "stab: " + "; ".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default=None,
                    help="base git commit (default: the commit this branch was cut from, i.e. the merge base "
                         "with main)")
    ap.add_argument("--only", choices=["mrac", "stab"], default=None, help="run one half only")
    ap.add_argument("--ticks", type=int, default=20000, help="stab: ticks per seed")
    ap.add_argument("--seeds", default="1,2", help="stab: seeds")
    args = ap.parse_args()

    if shutil.which("gcc") is None:
        print("SKIP all-loop-equiv: gcc not on PATH", file=sys.stderr)
        sys.exit(77)

    base_rev = args.base
    if base_rev is None:
        # The commit the branch was cut from: the merge base with main is what "today's firmware" means when
        # main has moved on, so the proof survives a rebase.
        r = subprocess.run(["git", "merge-base", "HEAD", "main"], cwd=REPO, capture_output=True, text=True)
        base_rev = r.stdout.strip() or "HEAD"

    print(f"all-loop equivalence: base {base_rev}", file=sys.stderr)

    ok = True
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        if args.only in (None, "mrac"):
            good, text = mrac_proof(base_rev, tmp)
            print(("OK   " if good else "FAIL ") + text, file=sys.stderr)
            ok = ok and good
        if args.only in (None, "stab"):
            seeds = [int(s) for s in args.seeds.split(",")]
            good, text = stab_proof(base_rev, tmp, args.ticks, seeds)
            print(("OK   " if good else "FAIL ") + text, file=sys.stderr)
            ok = ok and good

    print("ALL-LOOP EQUIV OK" if ok else "ALL-LOOP EQUIV DIFFER")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
