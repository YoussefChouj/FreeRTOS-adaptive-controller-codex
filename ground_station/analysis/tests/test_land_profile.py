"""API/land_profile.h on the host: the 2-stage sink schedule (2026-10-21, operator style: slow on top, fast below 0.30 m), the xy hold band below 0.12 m and
the touchdown xy freeze below 0.04 m (2026-10-19)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
# Thresholds are tested either side, not on the edge: 32-bit MinGW may compare float constants in x87 precision.
SRC = r"""#include <stdio.h>
#include "land_profile.h"
int main(void)
{
	const float z[] = {1.0f, 0.31f, 0.29f, 0.16f, 0.14f, 0.13f, 0.11f, 0.05f, 0.03f, 0.0f};
	for (unsigned i = 0; i < sizeof z / sizeof z[0]; i++)
		printf("%.2f %u %u %u %u\n", LandProfile_SinkMps(z[i]), LandProfile_XyFreeze(1, z[i]), LandProfile_XyFreeze(0, z[i]),
		       LandProfile_XyHold(1, z[i]), LandProfile_XyHold(0, z[i]));
	return 0;
}
"""


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not on PATH")
def test_sink_schedule_xy_hold_and_freeze(tmp_path):
    (tmp_path / "t.c").write_text(SRC)
    exe = tmp_path / "t.exe"
    res = subprocess.run(["gcc", "-std=c99", "-Wall", "-Wextra", f"-I{ROOT / 'API'}", str(tmp_path / "t.c"), "-o", str(exe)],
                         capture_output=True, text=True)
    assert res.returncode == 0 and "warning:" not in res.stderr, res.stderr
    rows = [ln.split() for ln in subprocess.run([str(exe)], capture_output=True, text=True).stdout.splitlines()]
    assert [r[0] for r in rows] == ["0.40", "0.40"] + ["0.90"] * 8
    assert [r[1] for r in rows] == ["0", "0", "0", "0", "0", "0", "0", "0", "1", "1"]   # landing, below 0.04 m only
    assert [r[3] for r in rows] == ["0", "0", "0", "0", "0", "0", "1", "1", "1", "1"]   # landing, below 0.12 m
    assert all(r[2] == "0" and r[4] == "0" for r in rows)                               # never outside LANDING
