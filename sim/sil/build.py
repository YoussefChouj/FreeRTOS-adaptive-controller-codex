"""Host build of the firmware controller code for the SIL: API/pid.c, API/mrac*.c, API/controller.c + csrc/sil_server.c.

Not a ctypes shared library: the only gcc here is 32-bit MinGW (`gcc -dumpmachine` = mingw32) and Python is 64-bit,
so a DLL from it cannot be loaded (the same reason ground_station/research/sim/_ccore/build.py builds an executable).
The server is an executable stepped over binary pipes (fw.py); one process per simulated row also gives every row its
own copy of the firmware globals (Ctrler, mrac_state).

Flags: CFLAGS of API/tests/run_mrac_equiv.py. The firmware files are byte copies in build/src so the stubs in
csrc/stubs win over the firmware header chain (as run_mrac_equiv.py does). controller.c is compiled with -D__CC_ARM,
its only use of the macro being the V2 mixer deficit block (API/controller.c:46-80, 86-88), so the real
mrac_mixer_deficit feeds V2 from the ported mixer's previous-tick values.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

SIL = Path(__file__).resolve().parent
REPO = SIL.parents[1]
API = REPO / "API"
CSRC = SIL / "csrc"
BUILD = SIL / "build"
FIRMWARE = ["pid.c", "pid.h", "mrac.c", "mrac.h", "mrac_math.c", "mrac_math.h", "mrac_variant.h",
            "controller.c", "controller.h"]
CFLAGS = ["-std=gnu99", "-O2", "-msse2", "-mfpmath=sse", "-ffp-contract=off", "-fno-fast-math", "-Wall", "-Wextra"]
# pre-existing firmware warnings (PID_ROW leaves aw_mode/Kt to zero-init; the unused MRAC_InverseMixer stub)
FW_QUIET = ["-Wno-missing-field-initializers", "-Wno-unused-parameter", "-Wno-unused-function"]


def _sources() -> list[Path]:
    return [API / f for f in FIRMWARE] + sorted(CSRC.rglob("*.[ch]"))


def _digest(variant: int) -> str:
    h = hashlib.sha256(f"{variant}{CFLAGS}".encode())
    for p in _sources():
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


def sim_digest() -> str:
    """Firmware + SIL python + bench plant: the key for results cached from simulations (autotune)."""
    h = hashlib.sha256(_digest(0).encode())
    for p in sorted(SIL.glob("*.py")) + [REPO / "sim" / "bench" / "plant.py"]:
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


def build(variant: int = 0) -> Path:
    """Executable for MRAC_VARIANT `variant` (0 STRUCT6, 1 STRUCT6_RBF12); rebuilt when a source changes."""
    gcc = shutil.which("gcc")
    if gcc is None:
        raise RuntimeError("gcc not found on PATH")
    tag = _digest(variant)
    out = BUILD / f"sil_v{variant}_{tag}.exe"
    if out.exists():
        return out
    work = BUILD / f"src_v{variant}_{tag}"
    work.mkdir(parents=True, exist_ok=True)
    for f in FIRMWARE:
        shutil.copy2(API / f, work / f)
    inc = ["-I", str(work), "-I", str(CSRC / "stubs")]
    dfl = [f"-DMRAC_VARIANT={variant}"]
    objs = []
    for src, extra in ((work / "pid.c", FW_QUIET), (work / "mrac.c", FW_QUIET), (work / "mrac_math.c", FW_QUIET),
                       (work / "controller.c", FW_QUIET + ["-D__CC_ARM"]), (CSRC / "sil_server.c", [])):
        obj = work / (src.stem + ".o")
        _gcc([gcc, *CFLAGS, *dfl, *extra, *inc, "-c", str(src), "-o", str(obj)])
        objs.append(str(obj))
    _gcc([gcc, *objs, "-lm", "-o", str(out)])
    return out


def _gcc(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"gcc failed: {' '.join(cmd[-3:])}\n{res.stderr.strip()[-1500:]}")
    warn = [ln for ln in res.stderr.splitlines() if "warning:" in ln]
    if warn:
        print("\n".join(warn), file=sys.stderr)


if __name__ == "__main__":
    for v in (0, 1):
        print(build(v))
