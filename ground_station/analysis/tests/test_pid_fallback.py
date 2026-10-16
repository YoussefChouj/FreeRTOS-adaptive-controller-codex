"""Unit test for API/pid_fallback.h via pytest (Section C, lab-friction-2026-10-15)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TEST_SRC = ROOT / "tests" / "firmware_host" / "test_pid_fallback.c"


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not on PATH")
def test_pid_fallback_host_suite(tmp_path):
    """Compile and execute the test_pid_fallback C suite."""
    exe = tmp_path / "test_pid_fallback.exe"
    res = subprocess.run(
        ["gcc", "-std=c99", "-Wall", "-Werror", f"-I{ROOT / 'API'}", str(TEST_SRC), "-o", str(exe)],
        capture_output=True, text=True
    )
    assert res.returncode == 0, f"Compilation failed: {res.stderr}"
    assert "warning:" not in res.stderr, res.stderr

    run_res = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run_res.returncode == 0, f"Test failed: {run_res.stderr}\n{run_res.stdout}"
    assert "test_pid_fallback: all passed" in run_res.stdout
