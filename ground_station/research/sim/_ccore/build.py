"""Build the host PID driver from the real API/pid.c (WP-10 golden test).

A byte copy of API/pid.c is compiled against the stub headers next to this file (pid.h with the same
declarations, robot_types.h, SINS globals), because API/pid.h pulls in the stm32 header chain. An
executable, not a shared library, so a 32-bit gcc on a 64-bit Python still works.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

CCORE = pathlib.Path(__file__).resolve().parent
REPO = CCORE.parents[3]


def build_pid_driver() -> pathlib.Path:
    """Compile API/pid.c + pid_driver.c; returns the executable. Raises RuntimeError with the reason."""
    gcc = shutil.which("gcc")
    if gcc is None:
        raise RuntimeError("gcc not found on PATH")
    out = CCORE / "build" / ("pid_driver.exe" if sys.platform == "win32" else "pid_driver")
    out.parent.mkdir(exist_ok=True)
    src = out.parent / "pid.c"
    shutil.copyfile(REPO / "API" / "pid.c", src)
    cmd = [gcc, "-O0", "-msse2", "-mfpmath=sse", "-ffp-contract=off", "-I", str(CCORE), str(src),
           str(CCORE / "pid_driver.c"), "-lm", "-o", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"gcc failed: {res.stderr.strip()[-400:]}")
    return out


if __name__ == "__main__":
    print(build_pid_driver())
