"""Tests for the per-session CSV telemetry recorder (CsvRecorder).

Covers: long-format CSV layout, buffered flush on stop, error counting when
the target directory is not writable, and the /health ``recorder`` block.
"""
from __future__ import annotations

import csv

import pytest

from ground_station.service.api import _recorder_status
from ground_station.service.storage import CsvRecorder


def test_recorder_writes_long_format_csv(tmp_path):
    rec = CsvRecorder(tmp_path, enabled=True, flush_interval_s=0.05)
    rec.start()
    try:
        assert rec.started
        assert rec.path is not None
        assert rec.path.parent.name.startswith("20")  # YYYYmmdd-HHMMSS stamp
        assert rec.path.name == "telemetry.csv"
        rec.note(0, {"status.arm": 1.0, "mrac.alt": 2.5}, received_ns=100)
        rec.note("rtos", {"xTickCount": 42}, received_ns=200)
    finally:
        rec.stop()

    with open(rec.path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3
    assert rows[0] == {"received_ns": "100", "slot": "0",
                       "key": "status.arm", "value": "1.0"}
    assert rows[1]["key"] == "mrac.alt" and rows[1]["slot"] == "0"
    # Long format robust to key-set changes: rtos sample has its own key.
    assert rows[2] == {"received_ns": "200", "slot": "rtos",
                       "key": "xTickCount", "value": "42"}
    assert rec.rows == 3
    assert rec.errors == 0


def test_recorder_disabled_by_enabled_false(tmp_path):
    rec = CsvRecorder(tmp_path, enabled=False)
    rec.start()
    try:
        assert not rec.started
        assert rec.path is None
        rec.note(0, {"k": 1}, received_ns=1)
    finally:
        rec.stop()
    assert rec.rows == 0


def test_recorder_bad_dir_counts_error(tmp_path):
    # A regular file as a path component makes mkdir(parents=True) fail,
    # which the recorder must swallow and count as an error, not raise.
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    rec = CsvRecorder(blocker / "sub" / "sessions", enabled=True)
    rec.start()
    try:
        assert not rec.started
        assert rec.path is None
        assert rec.errors >= 1
    finally:
        rec.stop()


class _Stub:
    pass


def test_health_recorder_block_missing_recorder():
    # A service object without a recorder must not break /health.
    status = _recorder_status(_Stub())
    assert status["enabled"] is False
    assert status["path"] is None
    assert status["rows"] == 0


def test_health_recorder_block_reports_status(tmp_path):
    rec = CsvRecorder(tmp_path, enabled=True)
    rec.start()
    try:
        rec.note(0, {"status.arm": 1}, received_ns=5)
        svc = _Stub()
        svc.recorder = rec
        status = _recorder_status(svc)
        assert status["enabled"] is True
        assert status["started"] is True
        assert status["path"] is not None
    finally:
        rec.stop()
    # After stop the CSV is flushed; rows reflect what was written.
    svc = _Stub()
    svc.recorder = rec
    status = _recorder_status(svc)
    assert status["rows"] == 1