"""Tests for Workflow B flight scoring (ground_station/analysis/workflow_b_adapter.py).

Each test builds a session in tmp_path: position columns in slot 0 (the base timeline), motors in slot 1
(so they go through host-time alignment), and a manifest from campaign_capture.write_manifest.
"""
from __future__ import annotations

import csv
import math
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from ground_station.analysis import workflow_b_adapter as wba
from ground_station.analysis.workflow_b_adapter import flight_rows, score
from ground_station.livewatch.campaign_capture import MOTORS, POSITION_AXES, ManifestError, write_manifest

REPO = Path(__file__).resolve().parents[3]
POSITION = [name for a in POSITION_AXES for name in (a.feedback, a.reference)]


def _write_csv(path: Path, times, columns: dict) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["t_src_ms", "t_host_s", "seq"] + list(columns))
        for i, t in enumerate(times):
            writer.writerow([i * 10, repr(float(t)), i] + [repr(float(columns[c][i])) for c in columns])


def _position(n: int, dx_cm=0.0, dy_cm=0.0, dz_m=0.0) -> dict:
    """Tracking errors per sample (scalar or length n): x, y in cm as the loc loops, z in m."""
    x, y, z = POSITION_AXES
    errors = [np.broadcast_to(np.asarray(e, float), (n,)) for e in (dx_cm, dy_cm, dz_m)]
    columns = {}
    for axis, ref, err in ((x, 100.0, errors[0]), (y, 50.0, errors[1]), (z, 1.0, errors[2])):
        columns[axis.feedback] = ref + err
        columns[axis.reference] = np.full(n, ref)
    return columns


def _motors(motor1) -> dict:
    """motor1 as given, motors 2-4 at a mid-range 3000 (CCR 2000..4000)."""
    motor1 = np.asarray(motor1, float)
    return {m: motor1 if i == 0 else np.full(motor1.size, 3000.0) for i, m in enumerate(MOTORS)}


def _session(tmp_path: Path, times, position: dict, motor_times, motors: dict, segments) -> Path:
    _write_csv(tmp_path / "run.slot0.csv", times, position)
    _write_csv(tmp_path / "run.slot1.csv", motor_times, motors)
    write_manifest(
        tmp_path,
        pack_id="test",
        rate_hz=100,
        slots=[
            {"slot": 0, "hz": 100.0, "csv": "run.slot0.csv", "vars": POSITION},
            {"slot": 1, "hz": 100.0, "csv": "run.slot1.csv", "vars": list(MOTORS)},
        ],
        dropped=[],
        segments=[{"name": name, "t0_host_s": t0, "t1_host_s": t1} for name, t0, t1 in segments],
    )
    return tmp_path


def _times(n: int) -> list[float]:
    return [i / 100 for i in range(n)]


def test_hand_computed_rmse_and_sat(tmp_path):
    # 3 cm and 4 cm horizontal (0.05 m), 0.12 m vertical: |e| = 0.13 m on every sample.
    # motor1 saturated (4000 >= SAT_HI) on 8 of 20 samples: 8 of 80 motor samples.
    t = _times(20)
    session = _session(tmp_path, t, _position(20, 3.0, 4.0, 0.12), t, _motors([4000] * 8 + [3000] * 12),
                       [("hover", 0.0, 1.0)])
    [row] = flight_rows(session)
    assert row == {"traj": "hover", "rmse": pytest.approx(0.13), "rmse_xy": pytest.approx(0.05),
                   "rmse_z": pytest.approx(0.12), "sat": pytest.approx(0.1), "n": 20, "diverged": False}


def test_two_segments_are_half_open(tmp_path):
    # x error 3 cm before t = 0.20, 6 cm from it on; the sample at exactly 0.20 belongs to the second.
    t = _times(40)
    session = _session(tmp_path, t, _position(40, [3.0] * 20 + [6.0] * 20), t, _motors([3000] * 40),
                       [("second", 20 / 100, 40 / 100), ("first", 0.0, 20 / 100)])
    rows = flight_rows(session)
    assert [(r["traj"], r["n"]) for r in rows] == [("first", 20), ("second", 20)]
    assert [r["rmse_xy"] for r in rows] == [pytest.approx(0.03), pytest.approx(0.06)]


def test_too_short_or_non_finite_segment_diverges(tmp_path):
    t = _times(40)
    dx = np.full(40, 3.0)
    dx[25] = np.nan  # one lost position sample inside "gap"
    session = _session(tmp_path, t, _position(40, dx), t, _motors([3000] * 40),
                       [("short", 0.0, 9 / 100), ("ten", 9 / 100, 19 / 100), ("gap", 19 / 100, 1.0)])
    short, ten, gap = flight_rows(session)
    assert (short["n"], short["diverged"], short["rmse"]) == (9, True, math.inf)
    assert short["rmse_xy"] == pytest.approx(0.03)  # still reported, as bench does
    assert (ten["n"], ten["diverged"], ten["rmse"]) == (10, False, pytest.approx(0.03))
    assert gap["diverged"] and gap["rmse"] == math.inf and math.isnan(gap["rmse_xy"])


def test_alignment_leaves_telemetry_gaps_empty(tmp_path):
    # Motors are sampled 1 ms after base samples 0-9 (saturated) and 35-39 (not). Base samples 0-19
    # are within 0.1 s of a saturated sample, 26-39 of an unsaturated one, and 20-25 of neither:
    # those six drop out. Motors 2-4 never saturate, so sat = 20 / (34 * 4); filling the gap would give
    # 23 / (40 * 4).
    t = _times(40)
    motor_idx = list(range(10)) + list(range(35, 40))
    session = _session(tmp_path, t, _position(40), [i / 100 + 0.001 for i in motor_idx],
                       _motors([4000] * 10 + [3000] * 5), [("hover", 0.0, 1.0)])
    [row] = flight_rows(session)
    assert row["sat"] == pytest.approx(20 / (34 * 4))
    assert row["n"] == 40 and not row["diverged"]


def test_session_without_motor_columns_is_rejected(tmp_path):
    t = _times(20)
    _write_csv(tmp_path / "run.slot0.csv", t, _position(20))
    write_manifest(tmp_path, pack_id="test", rate_hz=100, dropped=[],
                   slots=[{"slot": 0, "hz": 100.0, "csv": "run.slot0.csv", "vars": POSITION}],
                   segments=[{"name": "hover", "t0_host_s": 0.0, "t1_host_s": 1.0}])
    with pytest.raises(ManifestError, match="did not record"):
        flight_rows(tmp_path)


def test_score_is_the_bench_objective():
    rows = [{"rmse": 0.1, "sat": 0.0}, {"rmse": 0.3, "sat": 0.1}, {"rmse": math.inf, "sat": 0.2}]
    # median 0.3 + 0.25 * mean(0.1, 0.3, 2.0 capped) + 5 * (mean sat 0.1 - budget 0.05) = 0.75
    assert score(rows) == pytest.approx(0.75)
    assert score(rows) == wba._bench().objective(rows)


def test_score_of_no_rows_raises():
    with pytest.raises(ValueError):
        score([])


def test_bench_imports_from_another_cwd(tmp_path):
    code = ("from ground_station.analysis.workflow_b_adapter import score; "
            "print(score([{'rmse': 0.2, 'sat': 0.0}]))")
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert float(out.stdout) == pytest.approx(0.25)  # 0.2 + 0.25 * 0.2


def test_adapter_holds_no_symbol_or_controller_names():
    source = Path(wba.__file__).read_text(encoding="utf-8")
    assert not re.search(r"(?i)mrac|pid|ctrler|mymotor", source)
