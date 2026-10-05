#!/usr/bin/env python3
"""Build and run every host C test of the firmware (API/tests, tests/firmware_host) with gcc.

One row per test: the sources and the flags from the build line in the test's header comment. A test
passes when it builds and exits 0. Exit 1 if any test fails. Run from anywhere; paths are repo-relative.
`{src}` is a scratch copy of API/mrac*.[ch], for tests whose stubs must win over the headers next to mrac.c
(the same trick as API/tests/run_mrac_equiv.py).

    python tools/host_tests.py            # all rows
    python tools/host_tests.py pid_guards # rows whose name contains the argument
    python tools/host_tests.py --tidy     # clang-tidy (.clang-tidy) on each API/*.c the rows build, same flags
    python tools/host_tests.py --arm      # arm-none-eabi-gcc -fsyntax-only on the same files (skips if absent)
    python tools/host_tests.py --float    # gcc -Werror=double-promotion on the same files (single-precision FPU)
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# Rows build and run in parallel (gcc and the test exe are separate processes). Capped at 4: the lab laptop's
# AC adapter drops under full load. HOST_TESTS_JOBS=1 gives the old serial run.
JOBS = int(os.environ.get("HOST_TESTS_JOBS", min(4, os.cpu_count() or 1)))
# Built exes are kept here, named by a hash of gcc, the flags and the content of every source and header the row
# compiles (gcc -MM). On this host the first run of a new .exe is held by the antivirus scan (measured: pid_guards
# 27-50 s first run, 0.47 s after), so an unchanged row reruns its scanned exe. HOST_TESTS_CACHE=0 = fresh temp build.
CACHE = REPO / ".cache" / "host_tests"
CACHE_ON = os.environ.get("HOST_TESTS_CACHE", "1") != "0"

W = ["-Wall", "-Wextra"]
STUBS = ["-IAPI/tests/stubs", "-IAPI"]
FWH = ["-Itests/firmware_host/stubs", "-IAPI"]

# name                 sources                                                                flags
HOST_TESTS = [
    ("drift_fix",        ["API/tests/test_drift_fix.c", "API/pid.c"],                            ["-std=c99", *W, "-DSTUBS_ROBOT_TYPES_H", *STUBS]),
    ("pid_guards",       ["API/tests/test_pid_guards.c", "API/pid.c"],                           ["-std=c99", *W, "-Wno-missing-field-initializers", "-DSTUBS_ROBOT_TYPES_H", *STUBS]),
    ("ekf_of_shadow",    ["API/tests/test_ekf_of_shadow.c", "API/ekf_of.c"],                     ["-std=c99", *W, "-IAPI", "-IAPI/tests/stubs_wp14"]),
    ("rpm_median",       ["API/tests/test_rpm_median.c"],                                        ["-std=c99", *W, "-IBSP"]),
    ("thrust_estimators", ["API/thrust_estimators.c", "API/tests/test_thrust_estimators.c"],     ["-std=c99", *W, *STUBS, "-IBSP", "-IGlobal_file", "-ITASK", "-IUSER"]),
    ("mrac_sigma_prior", ["API/tests/test_mrac_sigma_prior.c", "{src}/mrac.c"],                  ["-std=c99", "-Wall", "-DMRAC_ENABLE_SIGMA_PRIOR", "-I{src}", "-IAPI/tests/stubs"]),
    ("mrac_inputs",      ["API/tests/test_mrac_inputs.c", "{src}/mrac.c", "{src}/mrac_math.c"], ["-std=c99", *W, "-I{src}", "-IAPI/tests/stubs"]),
    ("mrac_inputs_rbf",  ["API/tests/test_mrac_inputs.c", "{src}/mrac.c", "{src}/mrac_math.c"], ["-std=c99", *W, "-DMRAC_VARIANT=1", "-I{src}", "-IAPI/tests/stubs"]),
    ("mrac_sizeof",      ["API/tests/mrac_sizeof.c"],                                            ["-std=c99", *STUBS]),
    ("fw_controller",    ["tests/firmware_host/test_controller.c", "API/controller.c"],          ["-std=c99", "-Wall", "-Werror", *FWH]),
    ("mixer",            ["API/tests/test_mixer.c", "API/controller.c"],                         ["-std=c99", *W, "-msse2", "-mfpmath=sse", *FWH]),
    ("subscribe_8_slots", ["API/tests/test_subscribe_harness.c", "API/subscribe.c"],             ["-m32", "-std=c99", *W, "-Wno-unused-parameter", "-Wno-type-limits",
                                                                                                  *STUBS, "-Ifirmware", "-DSUBSCRIBE_ADDR_SRAM_LO=0x00000000U",
                                                                                                  "-DSUBSCRIBE_ADDR_SRAM_HI=0xFFFFFFFEU", "-DSUBSCRIBE_UART5_ENABLED=1",
                                                                                                  "-DSUBSCRIBE_MAX_SLOTS=8U"]),
    ("fw_ekf_gate",      ["tests/firmware_host/test_ekf_gate.c", "API/ekf.c"],                   ["-std=c89", "-pedantic", "-Wall", "-Werror", *FWH]),
    ("fw_idle_decouple", ["tests/firmware_host/test_idle_decouple.c"],                           ["-std=c89", "-Wall", *FWH]),
    ("fw_wfb_traj",      ["tests/firmware_host/test_wfb_traj.c", "API/wfb_traj.c"],              ["-std=c99", *W, "-IAPI"]),
    ("fw_wfb_safety",    ["tests/firmware_host/test_wfb_safety.c", "API/wfb_safety.c"],          ["-std=c99", *W, "-IAPI"]),
    ("fw_wfb_prim",      ["tests/firmware_host/test_wfb_prim.c", "API/wfb_prim.c"],              ["-std=c99", *W, "-IAPI"]),
    ("fw_wfb_glue",      ["tests/firmware_host/test_wfb_glue.c", "API/wfb_glue.c", "API/wfb_traj.c", "API/wfb_safety.c", "API/wfb_prim.c"],
                                                                                                 ["-std=c99", *W, "-Werror", "-Wdeclaration-after-statement", "-Wvla", "-IAPI"]),
    ("fw_prearm",        ["tests/firmware_host/test_prearm.c", "API/prearm.c"],                  ["-std=c99", *W, "-Werror", "-Wdeclaration-after-statement", "-IAPI"]),
    ("fw_health",        ["tests/firmware_host/test_fw_health.c", "API/fw_health.c"],            ["-std=c99", *W, "-Werror", "-Wdeclaration-after-statement", "-IAPI"]),
]


def _tail(text: str, n: int = 12) -> str:
    return "\n".join("    " + line for line in text.strip().splitlines()[-n:])


def _key(gcc: str, ident: str, row: list[str], args: list[str], src: Path) -> str:
    """Hash of gcc, the row as written and every file it compiles; "" if gcc -MM fails (the build reports it)."""
    dep = subprocess.run([gcc, *args, "-MM"], cwd=REPO, capture_output=True, text=True)
    if dep.returncode != 0:
        return ""
    h = hashlib.sha256((ident + "\0" + "\0".join(row)).encode())
    for tok in re.split(r"(?<!\\)\s+", dep.stdout.replace("\\\n", " ")):
        if tok and not tok.endswith(":"):
            path = tok.replace("\\ ", " ")
            h.update(("\0" + path.replace(src.as_posix(), "{src}") + "\0").encode())
            h.update((REPO / path).read_bytes())
    return h.hexdigest()[:16]


def run_one(gcc: str, ident: str, name: str, sources: list[str], flags: list[str], out_dir: Path) -> tuple[bool, bool, str]:
    """Build (unless cached) and run one row; return (passed, built, report line). Rows share only the {src} copy."""
    src = out_dir / "src"
    args = [a.replace("{src}", src.as_posix()) for a in [*flags, *sources]]
    key = _key(gcc, ident, [*flags, *sources], args, src) if CACHE_ON else ""
    exe = CACHE / f"{name}_{key}.exe" if key else out_dir / f"{name}.exe"
    built = not exe.exists()
    if built:
        tmp = exe.with_name(f"{name}_{os.getpid()}.tmp.exe") if key else exe
        build = subprocess.run([gcc, *args, "-lm", "-o", str(tmp)], cwd=REPO, capture_output=True, text=True)
        if build.returncode != 0:
            return False, True, f"FAIL {name}: build\n{_tail(build.stderr)}"
        if key:
            os.replace(tmp, exe)
            for old in CACHE.glob(f"{name}_*.exe"):     # this row's older builds (exact name: mrac_inputs != _rbf)
                if old != exe and re.fullmatch(re.escape(name) + r"_[0-9a-f]{16}\.exe", old.name):
                    old.unlink(missing_ok=True)
    try:
        res = subprocess.run([str(exe)], cwd=REPO, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return False, built, f"FAIL {name}: timeout"
    if res.returncode != 0:
        return False, built, f"FAIL {name}: exit {res.returncode}\n{_tail(res.stdout + res.stderr)}"
    last = (res.stdout.strip().splitlines() or [""])[-1]
    return True, built, f"PASS {name}: {last[:100]}"


def find_tidy() -> str | None:
    exe = shutil.which("clang-tidy")
    if exe is None and Path("C:/Program Files/LLVM/bin/clang-tidy.exe").exists():
        exe = "C:/Program Files/LLVM/bin/clang-tidy.exe"
    return exe


def gcc_system_args() -> list[str]:
    """On Windows clang-tidy has no C library of its own: borrow MinGW gcc's target and include dirs."""
    gcc = shutil.which("gcc")
    if sys.platform != "win32" or gcc is None:
        return []
    machine = subprocess.run([gcc, "-dumpmachine"], capture_output=True, text=True).stdout.strip()
    probe = subprocess.run([gcc, "-xc", "-E", "-v", "-"], stdin=subprocess.DEVNULL, capture_output=True, text=True).stderr
    dirs, inside = [], False
    for line in probe.splitlines():
        if line.startswith("#include <...>"):
            inside = True
        elif line.startswith("End of search list"):
            break
        elif inside:
            dirs.append(line.strip())
    arch = "x86_64" if machine.startswith("x86_64") else "i686"
    return [f"--target={arch}-pc-windows-gnu", "-nostdinc"] + [a for d in dirs for a in ("-isystem", d)]


def firmware_jobs(src: Path) -> dict[str, list[str]]:
    """Each firmware file (API/*.c, not the tests) the rows build -> the include/define flags of its first row."""
    for f in (REPO / "API").glob("mrac*.[ch]"):
        shutil.copy2(f, src / f.name)
    jobs: dict[str, list[str]] = {}
    for _name, sources, flags in HOST_TESTS:
        fw_flags = [a.replace("{src}", src.as_posix()) for a in flags if a.startswith(("-I", "-D", "-std"))]
        for s in sources:
            if s.startswith(("API/", "{src}/")) and not s.startswith("API/tests/"):
                jobs.setdefault(s.replace("{src}", src.as_posix()), fw_flags)
    jobs[f"{src.as_posix()}/mrac_math.c"] = jobs[f"{src.as_posix()}/mrac.c"]   # built by run_mrac_equiv and the SIL
    return jobs


def over_firmware(tool: str, cmd) -> int:
    """Run cmd(path, flags) on every firmware file; PASS/FAIL per file, exit 1 if any fails."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp)
        jobs = firmware_jobs(src)
        failed = []
        for path, flags in jobs.items():
            res = subprocess.run(cmd(path, flags), cwd=REPO, capture_output=True, text=True)
            label = Path(path).name if path.startswith(src.as_posix()) else path
            if res.returncode != 0:
                failed.append(label)
                print(f"FAIL {tool} {label}\n{_tail(res.stdout + res.stderr, 40)}")
            else:
                print(f"PASS {tool} {label}")
    print(f"{tool}: {len(jobs) - len(failed)}/{len(jobs)} files clean")
    return 1 if failed else 0


def tidy() -> int:
    exe = find_tidy()
    if exe is None:
        print("FAIL clang-tidy: not installed")
        return 1
    sys_args = gcc_system_args()
    return over_firmware("clang-tidy", lambda path, flags: [
        exe, "--quiet", f"--config-file={REPO / '.clang-tidy'}", path, "--", *sys_args, *flags])


ARM = ["-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=hard", "-mfpu=fpv4-sp-d16", "-fsyntax-only", "-Wall"]


def arm() -> int:
    """The same files and host stubs, parsed for the flight target (32-bit, single-precision FPU, newlib)."""
    exe = shutil.which("arm-none-eabi-gcc")
    if exe is None:
        print("SKIP arm-none-eabi-gcc: not installed (the Keil build is the target check)")
        return 0
    return over_firmware("arm-none-eabi-gcc", lambda path, flags: [exe, *ARM, *flags, path])


def float_only() -> int:
    """No implicit float -> double promotion: the M4 FPU is single precision, so each one is a software double
    call (__aeabi_dmul, ...). Zero in every file today (2026-10-05); the step keeps it there. -mfpmath=sse: the
    32-bit host gcc otherwise uses x87 excess precision under -std=c99, which hides the warning."""
    gcc = shutil.which("gcc")
    if gcc is None:
        print("FAIL float: gcc not on PATH")
        return 1
    return over_firmware("double-promotion", lambda path, flags: [
        gcc, "-fsyntax-only", "-msse2", "-mfpmath=sse", "-Werror=double-promotion", *flags, path])


def main(argv: list[str]) -> int:
    if argv == ["--tidy"]:
        return tidy()
    if argv == ["--arm"]:
        return arm()
    if argv == ["--float"]:
        return float_only()
    gcc = shutil.which("gcc")
    if gcc is None:
        print("FAIL host tests: gcc not on PATH")
        return 1
    rows = [r for r in HOST_TESTS if not argv or any(a in r[0] for a in argv)]
    failed, n_built = [], 0
    ident = gcc + "\0" + subprocess.run([gcc, "--version"], capture_output=True, text=True).stdout
    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(JOBS) as pool:
        (Path(tmp) / "src").mkdir()
        for f in (REPO / "API").glob("mrac*.[ch]"):
            shutil.copy2(f, Path(tmp) / "src" / f.name)
        # map() yields in row order, so the report reads the same as a serial run
        for (name, _, _), (ok, built, line) in zip(rows, pool.map(lambda r: run_one(gcc, ident, *r, Path(tmp)), rows)):
            print(line, flush=True)
            n_built += built
            if not ok:
                failed.append(name)
    print(f"host tests: {len(rows) - len(failed)}/{len(rows)} passed ({n_built} built, {len(rows) - n_built} cached)" + (f"; failed: {' '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
