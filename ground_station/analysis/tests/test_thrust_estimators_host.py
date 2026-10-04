"""API/tests/test_thrust_estimators.c built with gcc and run (the thrust-estimator host test, WP-15 / WP-34).

Skipped when gcc is not on PATH.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
API = REPO / "API"

pytestmark = pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not on PATH")


def test_thrust_estimators_host(tmp_path):
    exe = tmp_path / "te.exe"
    build = subprocess.run(["gcc", "-std=c99", "-Wall", "-Wextra", "-I", str(API / "tests" / "stubs"), "-I", str(API),
                            str(API / "thrust_estimators.c"), str(API / "tests" / "test_thrust_estimators.c"),
                            "-lm", "-o", str(exe)], capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run.returncode == 0 and " 0 failed" in run.stdout, run.stdout
