#!/usr/bin/env python3
"""
Host bit-exact equivalence test runner for MRAC firmware.
Compares a reference tree against a new tree across multiple control scenarios.
"""

import argparse
import glob
import io
import itertools
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import exe_cache  # noqa: E402  (the antivirus holds the first run of each new exe: reuse unchanged builds)

CFLAGS = [
    "-std=c99",
    "-O2",
    "-msse2",
    "-mfpmath=sse",
    "-ffp-contract=off",
    "-fno-fast-math",
    "-Wall",
    "-Wextra",
]

COV_NAMES = ["u_ad_sat", "e_freeze", "theta_upper_entry", "theta_lower_entry", "simplex_trips"]


def extract_base_tree(base_rev: str, target_dir: Path, repo_root: Path):
    cmd = ["git", "ls-tree", "-r", "--name-only", base_rev, "API/"]
    res = subprocess.run(
        cmd,
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    files = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    extracted = []
    for f in files:
        basename = os.path.basename(f)
        if basename.startswith("mrac") and (basename.endswith(".c") or basename.endswith(".h")):
            show_cmd = ["git", "show", f"{base_rev}:{f}"]
            content = subprocess.check_output(show_cmd, cwd=str(repo_root))
            dest = target_dir / basename
            with open(dest, "wb") as out:
                out.write(content)
            extracted.append(dest)
    if not extracted:
        raise RuntimeError(f"No MRAC files found in revision {base_rev}")
    return extracted


def copy_working_tree(target_dir: Path, repo_root: Path):
    api_dir = repo_root / "API"
    copied = []
    for entry in api_dir.iterdir():
        if entry.is_file() and entry.name.startswith("mrac") and (entry.name.endswith(".c") or entry.name.endswith(".h")):
            dest = target_dir / entry.name
            shutil.copy2(entry, dest)
            copied.append(dest)
    if not copied:
        raise RuntimeError(f"No MRAC files found in {api_dir}")
    return copied


def build_binary(tree_dir: Path, driver_path: Path, stubs_dir: Path, out_bin: Path, extra_flags=None) -> Path:
    """Build (or reuse the cached identical build of) out_bin.name; return the exe to run. tree_dir is a temp copy."""
    if extra_flags is None:
        extra_flags = []
    c_files = sorted(glob.glob(str(tree_dir / "mrac*.c")))
    if not c_files:
        raise RuntimeError(f"No mrac*.c files found in {tree_dir}")
    args = CFLAGS + extra_flags + [str(driver_path)] + c_files + ["-I", str(tree_dir), "-I", str(stubs_dir)]
    ok, exe, _built, stderr = exe_cache.build("gcc", "mrac_equiv_" + out_bin.name, args, out_bin.parent,
                                              {tree_dir.parent: "{tmp}"})
    if not ok:
        raise RuntimeError(f"gcc failed for {out_bin.name}:\n{stderr}")
    return exe


def parse_cov(line: str, cov_counters: dict):
    if line.startswith("cov "):
        parts = line.split()
        if len(parts) >= 3:
            cov_counters[parts[1]] = int(parts[2])


CHUNK = 1 << 20


def compare_streams(ref_bin: Path, new_bin: Path):
    """Run both binaries and compare their stdout line by line (each line stripped).

    Fast path: 1 MiB blocks compared as bytes; an equal block only counts its lines and reads its cov lines. From
    the first block that differs, the rest is compared line by line, so a mismatch reports as before. The driver
    prints 3.7 M lines per binary: the line loop alone cost about 13 s per pair (measured 2026-10-05).
    """
    proc_ref = subprocess.Popen([str(ref_bin)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    proc_new = subprocess.Popen([str(new_bin)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    line_no = 0
    lines_compared = 0
    cov_counters = {}
    mismatch_info = None
    tail = b""  # partial last line of the equal prefix, the same in both streams

    try:
        while True:
            r_blk = proc_ref.stdout.read(CHUNK)
            n_blk = proc_new.stdout.read(CHUNK)
            if r_blk != n_blk or not r_blk:
                break
            data = tail + r_blk
            cut = data.rfind(b"\n") + 1
            done, tail = data[:cut], data[cut:]
            count = done.count(b"\n")
            line_no += count
            lines_compared += count
            if b"cov " in done:
                for line in done.decode().splitlines():
                    parse_cov(line.strip(), cov_counters)
        # Line by line from the partial line on: whole lines first, then the rest of each pipe.
        r_buf, n_buf = tail + r_blk, tail + n_blk
        if not r_buf.endswith(b"\n"):
            r_buf += proc_ref.stdout.readline()
        if not n_buf.endswith(b"\n"):
            n_buf += proc_new.stdout.readline()
        r_iter = itertools.chain(io.BytesIO(r_buf), proc_ref.stdout)
        n_iter = itertools.chain(io.BytesIO(n_buf), proc_new.stdout)
        while True:
            r_line = next(r_iter, b"").decode()
            n_line = next(n_iter, b"").decode()

            if not r_line and not n_line:
                break

            line_no += 1
            if not r_line:
                mismatch_info = {
                    "line_no": line_no,
                    "tag": "<EOF_REF>",
                    "ref_hex": "EOF",
                    "new_hex": n_line.strip(),
                    "ref_flt": 0.0,
                    "new_flt": 0.0,
                }
                break
            if not n_line:
                mismatch_info = {
                    "line_no": line_no,
                    "tag": "<EOF_NEW>",
                    "ref_hex": r_line.strip(),
                    "new_hex": "EOF",
                    "ref_flt": 0.0,
                    "new_flt": 0.0,
                }
                break

            r_line = r_line.strip()
            n_line = n_line.strip()

            if r_line != n_line:
                r_parts = r_line.split()
                n_parts = n_line.split()
                tag = r_parts[0] if r_parts else "<empty>"
                r_hex = r_parts[1] if len(r_parts) > 1 else ""
                n_hex = n_parts[1] if len(n_parts) > 1 else ""
                try:
                    r_flt = struct.unpack("!f", bytes.fromhex(r_hex))[0]
                except Exception:
                    r_flt = 0.0
                try:
                    n_flt = struct.unpack("!f", bytes.fromhex(n_hex))[0]
                except Exception:
                    n_flt = 0.0
                mismatch_info = {
                    "line_no": line_no,
                    "tag": tag,
                    "ref_hex": r_hex,
                    "new_hex": n_hex,
                    "ref_flt": r_flt,
                    "new_flt": n_flt,
                }
                break

            lines_compared += 1
            parse_cov(r_line, cov_counters)
    finally:
        proc_ref.kill()
        proc_new.kill()
        proc_ref.wait()
        proc_new.wait()

    return lines_compared, cov_counters, mismatch_info


def check_coverage_and_lines(lines_compared: int, cov_counters: dict):
    if lines_compared < 100000:
        print(f"Error: fewer than 100000 lines were compared ({lines_compared})", file=sys.stderr)
        return False
    for name in COV_NAMES:
        cnt = cov_counters.get(name, 0)
        if cnt == 0:
            print(f"Error: coverage counter '{name}' is 0", file=sys.stderr)
            return False
    return True


def run_self_test(base_rev: str, repo_root: Path, driver_path: Path, stubs_dir: Path):
    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        base_dir = tmp_dir / "base"
        base_dir.mkdir()
        extract_base_tree(base_rev, base_dir, repo_root)

        bin_base = tmp_dir / "bin_base"
        bin_base = build_binary(base_dir, driver_path, stubs_dir, bin_base)

        # Perturbation (a): replace first 0.0174533f with 0.0174534f in mrac.c
        pert_a_dir = tmp_dir / "pert_a"
        shutil.copytree(base_dir, pert_a_dir)
        mrac_c_a = pert_a_dir / "mrac.c"
        with open(mrac_c_a, "r", encoding="utf-8") as f:
            code_a = f.read()
        target_a = "0.0174533f"
        if target_a not in code_a:
            print(f"Error: {target_a} not found in {mrac_c_a}", file=sys.stderr)
            return False
        code_a = code_a.replace(target_a, "0.0174534f", 1)
        with open(mrac_c_a, "w", encoding="utf-8") as f:
            f.write(code_a)

        bin_pert_a = tmp_dir / "bin_pert_a"
        bin_pert_a = build_binary(pert_a_dir, driver_path, stubs_dir, bin_pert_a)
        _, _, mismatch_a = compare_streams(bin_base, bin_pert_a)
        if mismatch_a is None:
            print("Self-test failed: perturbation (a) did not fail comparison against base", file=sys.stderr)
            return False

        # Perturbation (b): replace denom = 1.0f + Phi_sq with denom = 1.0001f + Phi_sq in mrac.c
        pert_b_dir = tmp_dir / "pert_b"
        shutil.copytree(base_dir, pert_b_dir)
        mrac_c_b = pert_b_dir / "mrac.c"
        with open(mrac_c_b, "r", encoding="utf-8") as f:
            code_b = f.read()
        target_b = "denom = 1.0f + Phi_sq"
        if target_b not in code_b:
            print(f"Error: '{target_b}' not found in {mrac_c_b}", file=sys.stderr)
            return False
        code_b = code_b.replace(target_b, "denom = 1.0001f + Phi_sq", 1)
        with open(mrac_c_b, "w", encoding="utf-8") as f:
            f.write(code_b)

        bin_pert_b = tmp_dir / "bin_pert_b"
        bin_pert_b = build_binary(pert_b_dir, driver_path, stubs_dir, bin_pert_b)
        _, _, mismatch_b = compare_streams(bin_base, bin_pert_b)
        if mismatch_b is None:
            print("Self-test failed: perturbation (b) did not fail comparison against base", file=sys.stderr)
            return False

        print("SELFTEST OK")
        return True


def main():
    parser = argparse.ArgumentParser(description="Host bit-exact equivalence test for MRAC firmware")
    parser.add_argument("--base", default="4458435", help="Base git commit revision (default: 4458435)")
    parser.add_argument("--self-test", action="store_true", help="Run self-test with intentional perturbations")
    parser.add_argument("--define", action="append", default=[], metavar="NAME=VAL",
                        help="Extra -D for the new-tree builds only (the base tree predates e.g. MRAC_CAPACITY)")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent.parent
    driver_path = repo_root / "API" / "tests" / "test_mrac_equiv.c"
    stubs_dir = repo_root / "API" / "tests" / "stubs"

    if args.self_test:
        success = run_self_test(args.base, repo_root, driver_path, stubs_dir)
        sys.exit(0 if success else 1)

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        ref_dir = tmp_dir / "ref"
        ref_dir.mkdir()
        extract_base_tree(args.base, ref_dir, repo_root)

        new_dir = tmp_dir / "new"
        new_dir.mkdir()
        copy_working_tree(new_dir, repo_root)

        new_defs = ["-D" + d for d in args.define] + ["-DMRAC_EQUIV_NEW_TREE"]

        # The four builds are independent, and so are the two compares (each runs its two binaries): run them at
        # once. Results are reported in the old order, plain first.
        builds = {
            "ref_plain": (ref_dir, []),
            "new_plain": (new_dir, new_defs),
            "ref_sigma": (ref_dir, ["-DMRAC_ENABLE_SIGMA_PRIOR"]),
            "new_sigma": (new_dir, ["-DMRAC_ENABLE_SIGMA_PRIOR"] + new_defs),
        }
        with ThreadPoolExecutor(max_workers=len(builds)) as pool:
            jobs = [pool.submit(build_binary, tree, driver_path, stubs_dir, tmp_dir / name, extra_flags=flags)
                    for name, (tree, flags) in builds.items()]
            exes = {name: job.result() for name, job in zip(builds, jobs)}
            runs = [pool.submit(compare_streams, exes["ref_" + v], exes["new_" + v])
                    for v in ("plain", "sigma")]
            results = [run.result() for run in runs]

        lines = []
        for n_lines, cov, info in results:
            if info is not None:
                print(
                    f"Mismatch at line {info['line_no']}: tag '{info['tag']}' ref={info['ref_hex']} ({info['ref_flt']:.6e}) != new={info['new_hex']} ({info['new_flt']:.6e})",
                    file=sys.stderr,
                )
                sys.exit(1)
            if not check_coverage_and_lines(n_lines, cov):
                sys.exit(1)
            lines.append(n_lines)

        print(f"EQUIV OK: {lines[0]} lines identical (plain) + {lines[1]} (sigma-prior)")
        sys.exit(0)


if __name__ == "__main__":
    main()
