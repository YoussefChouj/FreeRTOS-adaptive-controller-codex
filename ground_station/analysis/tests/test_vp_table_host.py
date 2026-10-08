"""The vp table (TASK/StabilizerTask.c, s_vp + Keil_VariantPoll) applied on the host: every row is accepted by the
MRAC setters in the build it needs (basis > 0 rows only in MULTI), and the 3L-v2 rows land their fields.
The block is copied out of StabilizerTask.c verbatim, so a row the setters refuse fails here, not in the lab
(vp_active 0xEE). Skipped when gcc is not on PATH.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tools import exe_cache

REPO = Path(__file__).resolve().parents[3]
API = REPO / "API"
DRIVER = Path(__file__).with_name("vp_table_host.c")
START = "/* MRAC variant rows"
END = "\tvp_active = ok ? id : 0xEEU;\n}\n"
COLS = ("id", "active", "basis", "gamma_c", "b_axis", "pe_delay", "wc_pe", "p_max", "g_p", "g_y", "g_rbf", "te_off", "p_forget")

pytestmark = pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not on PATH")


@pytest.fixture(scope="module")
def rows(tmp_path_factory):
    out = tmp_path_factory.mktemp("vp_table")
    src = out / "src"
    src.mkdir()
    for f in API.glob("mrac*.[ch]"):
        shutil.copy2(f, src / f.name)
    text = (REPO / "TASK" / "StabilizerTask.c").read_text(encoding="utf-8").replace("\r\n", "\n")
    a = text.index(START)
    (src / "vp_rows.inc").write_text(text[a:text.index(END, a) + len(END)], encoding="utf-8")

    def run(variant: int) -> dict[int, dict]:
        args = ["-std=c99", "-O1", "-Wall", "-Wextra", f"-DMRAC_VARIANT={variant}", str(DRIVER),
                *map(str, sorted(src.glob("mrac*.c"))), "-I", str(src), "-I", str(API / "tests" / "stubs")]
        ok, exe, _built, stderr = exe_cache.build("gcc", f"vp_table_v{variant}", args, out, {src: "{src}"})
        assert ok, stderr
        assert "warning:" not in stderr, stderr
        res = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
        table = {}
        for line in res.stdout.splitlines():
            vals = [float(v) for v in line.split()[1:]]
            table[int(vals[0])] = dict(zip(COLS, vals))
        return table
    return run


def test_every_row_applies_in_the_build_it_needs(rows):
    default, multi = rows(0), rows(2)
    assert len(default) >= 20 and default.keys() == multi.keys()
    for vid, r in default.items():
        want = 0xEE if r["basis"] > 0 else vid      # basis > 0 is refused outside the MULTI build, by design
        assert r["active"] == want, (vid, r)
        assert multi[vid]["active"] == vid, (vid, multi[vid])


def test_3l_v2_rows_land_their_fields(rows):
    for build in (rows(0), rows(2)):
        for vid, r in build.items():
            if r["gamma_c"] > 0:                    # an L2 row: the composite law has its plant gain and delay
                assert r["b_axis"] > 0 and 1 <= r["wc_pe"] <= 100, (vid, r)
            if r["te_off"]:                         # tracking-error learning off on p/r, yaw keeps it
                assert r["g_p"] == 0 and r["g_y"] > 0, (vid, r)
                assert r["g_rbf"] in (-1, 0), (vid, r)   # MULTI ext block stays off too
            if r["p_max"] > 0:                      # a D row: the self-tuning gain has its forgetting rate
                assert r["p_forget"] > 0, (vid, r)
    r19 = rows(0)[19]
    assert (r19["te_off"], r19["gamma_c"], r19["p_max"], r19["p_forget"]) == (1, 20, 20, 5)   # replay winner, d_gain_range.md
    user = rows(0)[100]
    assert (user["gamma_c"], user["b_axis"], user["pe_delay"]) == (20, 49, 7)   # vp_user starts as row 16
