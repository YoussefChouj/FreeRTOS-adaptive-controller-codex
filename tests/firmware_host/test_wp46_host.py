"""WP-46 cross-checks: the exp-row and xy no-fight host tests pass, the s_xyrm bandwidths match the SIL fit, and the
no-fight test's copy of s_xyrm is the firmware table. Run: python -m pytest tests/firmware_host/test_wp46_host.py -s -q"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import exe_cache  # noqa: E402 (after sys.path insert)
import host_tests  # noqa: E402 (after sys.path insert)

MEASURED_PID_BW = 2.0   # s_xyrm row 0 comment: "measured 2.0"


def xyrm_rows(path: Path) -> list[tuple[float, ...]]:
    """The s_xyrm[4] initializer as 4 rows of (ref_type, bw, crm_ell, g), comments stripped."""
    text = path.read_text(encoding="utf-8", errors="replace")
    body = re.search(r"s_xyrm\[4\]\s*=\s*\{(.*?)\};", text, re.S).group(1)
    nums = [float(n) for n in re.findall(r"(-?\d+\.\d*)f", re.sub(r"/\*.*?\*/", "", body, flags=re.S))]
    assert len(nums) == 16, path
    return [tuple(nums[i:i + 4]) for i in range(0, 16, 4)]


FW = xyrm_rows(ROOT / "TASK" / "StabilizerTask.c")


def test_host_rows_pass():
    res = subprocess.run([sys.executable, "tools/host_tests.py", "fw_exp_row", "mrac_xy_nofight"], cwd=ROOT,
                         capture_output=True, text=True, timeout=600)
    print(res.stdout)
    assert "PASS fw_exp_row" in res.stdout and "PASS mrac_xy_nofight" in res.stdout


def test_nofight_copy_equals_firmware_table():
    assert xyrm_rows(ROOT / "tests" / "firmware_host" / "test_mrac_xy_nofight.c") == FW


def test_xyrm_bw_matches_sil_fit():
    res = subprocess.run([sys.executable, "tools/xyrm_fit.py"], cwd=ROOT, capture_output=True, text=True, timeout=600)
    fit = {int(k): float(v) for k, v in re.findall(r"kind (\d) bw ([0-9.]+)", res.stdout)}
    assert sorted(fit) == [0, 1, 2, 3], res.stdout + res.stderr
    print(f"kind 0 PID: SIL fit bw {fit[0]:.2f}, firmware row (measured) {MEASURED_PID_BW}")
    for kind in (1, 2, 3):
        assert round(fit[kind], 1) == pytest.approx(FW[kind][1]), f"kind {kind}: fit {fit[kind]:.4f} vs s_xyrm {FW[kind][1]}"


def test_nofight_ratio_and_disturbance_rejection():
    name, sources, flags = next(r for r in host_tests.HOST_TESTS if r[0] == "mrac_xy_nofight")
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc not on PATH")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        src.mkdir()
        for f in (ROOT / "API").glob("mrac*.[ch]"):
            shutil.copy2(f, src / f.name)
        args = [a.replace("{src}", src.as_posix()) for a in [*flags, *sources]]
        ok, exe, _, stderr = exe_cache.build(gcc, name, args, Path(tmp), {src: "{src}"})
        assert ok, stderr
        out = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=300).stdout
    ratios = {int(k): float(v) for k, v in re.findall(r"kind (\d) no-dist:.*ratio=([0-9.eE+-]+)", out)}
    dist = {int(k): (float(a), float(b)) for k, a, b in
            re.findall(r"kind (\d) dist: err_no_inj=([0-9.eE+-]+), err_with_inj=([0-9.eE+-]+)", out)}
    assert sorted(ratios) == sorted(dist) == [0, 1, 2, 3], out
    for kind in range(4):
        print(f"kind {kind}: ratio {ratios[kind]:.4f}, err no_inj {dist[kind][0]:.3f} with_inj {dist[kind][1]:.3f}")
        assert ratios[kind] < 0.05
        assert dist[kind][1] < dist[kind][0]
