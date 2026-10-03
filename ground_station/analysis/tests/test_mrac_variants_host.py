"""WP-27 MRAC variants, run on the host: build API/mrac*.c with gcc and mrac_variants_host.c.

OFF = today's law is proven by ``python API/tests/run_mrac_equiv.py`` (EQUIV OK with the variants compiled in).
These tests cover what that harness cannot: the V3 RBF12 build with rbf_on 0 equals the STRUCT6 build, every
variant switched ON stays inside the u_max clamp and finite (also with NaN inputs), and CMD 0x1D bounds.
Skipped when gcc is not on PATH.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ground_station.analysis.mrac_variants import VARIANT_FIELDS

REPO = Path(__file__).resolve().parents[3]
API = REPO / "API"
DRIVER = Path(__file__).with_name("mrac_variants_host.c")
STEPS = 3000

pytestmark = pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not on PATH")

FIELD = {name: i for i, (name, _lo, _hi) in enumerate(VARIANT_FIELDS)}


@pytest.fixture(scope="module")
def build(tmp_path_factory):
    out = tmp_path_factory.mktemp("mrac_host")
    src = out / "src"  # only the MRAC files, as API/tests/run_mrac_equiv.py does, so the stubs win
    src.mkdir()
    for f in API.glob("mrac*.[ch]"):
        shutil.copy2(f, src / f.name)
    bins = {}

    def get(variant: int) -> Path:
        if variant not in bins:
            exe = out / f"host_v{variant}.exe"
            cmd = ["gcc", "-std=c99", "-O1", "-ffp-contract=off", "-Wall", "-Wextra",
                   f"-DMRAC_VARIANT={variant}", str(DRIVER), *map(str, sorted(src.glob("mrac*.c"))),
                   "-I", str(src), "-I", str(API / "tests" / "stubs"), "-lm", "-o", str(exe)]
            res = subprocess.run(cmd, capture_output=True, text=True)
            assert res.returncode == 0, res.stderr
            # the only warnings allowed are the pre-existing unused MRAC_InverseMixer stub
            warnings = [ln for ln in res.stderr.splitlines() if "warning:" in ln and "InverseMixer" not in ln
                        and "current_vbatt" not in ln and "pwm" not in ln]
            assert not warnings, "\n".join(warnings)
            bins[variant] = exe
        return bins[variant]
    return get


def run(exe: Path, *args: str) -> dict:
    res = subprocess.run([str(exe), str(STEPS), *args], capture_output=True, text=True, check=True)
    out = {"ticks": [], "set": [], "max": {}, "umax": {}, "vid": {}, "nonfinite": None}
    for line in res.stdout.splitlines():
        head, *rest = line.split()
        if head == "t":
            out["ticks"].append(tuple(rest))
        elif head == "set":
            out["set"].append(int(rest[0]))
        elif head in ("max", "umax"):
            out[head][int(rest[0])] = float(rest[1])
        elif head == "vid":
            out["vid"][int(rest[0])] = int(rest[1])
        elif head == "nonfinite":
            out["nonfinite"] = int(rest[0])
    return out


def sets(axes, **fields) -> list[str]:
    return [f"{a}:{FIELD[k]}:{v}" for a in axes for k, v in fields.items()]


def assert_bounded(r: dict) -> None:
    assert r["nonfinite"] == 0
    for a in range(4):
        assert r["max"][a] <= r["umax"][a] * (1 + 1e-6)


def test_rbf_build_with_rbf_off_equals_struct6(build):
    base = run(build(0))
    rbf_off = run(build(1))
    assert len(base["ticks"]) == STEPS
    assert rbf_off["ticks"] == base["ticks"]
    assert base["vid"] == {0: 0, 1: 0, 2: 0, 3: 0} == rbf_off["vid"]


V1_PR = sets((0, 1), ref_type=2, drive_norm=1, lam_edot=0.0018, ref_delay_s=0.01) + sets((2,), ref_type=1, drive_norm=1)
CASES = {
    "v1_refmodel": (0, V1_PR, 0x01 | 0x02 | 0x04),
    "pr_kappa_crm": (0, V1_PR + sets((0, 1), kappa_pr=0.5, crm_ell=10), 0x01 | 0x02 | 0x04 | 0x08 | 0x10),
    "v2_sataware": (0, V1_PR + sets((0, 1, 2), mu_sat=0.85) + ["udef:0.3"], 0x01 | 0x02 | 0x04 | 0x20),
    "3l_layer1": (0, V1_PR + sets((0, 1), lam_ang=4), 0x01 | 0x02 | 0x04 | 0x40),
    "v3_rbf12": (1, V1_PR + sets((0, 1), rbf_on=1), 0x01 | 0x02 | 0x04 | 0x80),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_variant_on_is_active_bounded_and_finite(build, name):
    variant, args, pitch_vid = CASES[name]
    off = run(build(variant))
    on = run(build(variant), *args)
    assert all(on["set"]), on["set"]
    assert on["vid"][0] == pitch_vid
    assert on["ticks"] != off["ticks"]          # the variant changes the law
    assert_bounded(on)


@pytest.mark.parametrize("name", sorted(CASES))
def test_variant_on_survives_nan_inputs(build, name):
    variant, args, _ = CASES[name]
    r = run(build(variant), *args, "nan_rate:500:510", "nan_ang:900:910")
    assert_bounded(r)


def test_variant_set_rejects_out_of_range(build):
    exe = build(1)
    bad = ["4:0:1", "0:12:1", "0:0:3", "0:0:-2", "0:1:0.05", "0:5:51", "0:9:0", "0:4:nan", "0:6:-0.1"]
    good = ["0:0:-1", "3:0:2", "0:1:0.035", "0:5:50", "0:11:0.25", "0:8:1"]
    r = run(exe, *bad, *good)
    assert r["set"] == [0] * len(bad) + [1] * len(good)


def test_python_field_table_matches_firmware():
    rows = re.findall(r"MRAC_VAR_FIELD\(\s*([-\d.]+)f,\s*([-\d.]+)f,\s*\d\s*\),?\s*/\*\s*(\w+)",
                      (API / "mrac.c").read_text(encoding="utf-8", errors="replace"))
    assert [(n, float(lo), float(hi)) for lo, hi, n in rows] == [(n, float(lo), float(hi)) for n, lo, hi in VARIANT_FIELDS]
    enum = re.findall(r"MRAC_VF_(\w+)", (API / "mrac.h").read_text(encoding="utf-8", errors="replace"))
    assert [e.lower() for e in enum if e != "COUNT"][:len(VARIANT_FIELDS)] == [n for n, _, _ in VARIANT_FIELDS]
