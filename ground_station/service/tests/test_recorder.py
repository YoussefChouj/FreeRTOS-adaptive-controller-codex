"""Tests for the opt-in per-session recorder (CsvRecorder).

Covers: long-format CSV layout, buffered flush on stop, error counting when
the target directory is not writable, recording OFF by default (no directory
until start), start/stop/start creating two directories, the manifest and
event-log fields, command-lifecycle + note events, notes buffered while
stopped, and the /health ``recorder`` block.
"""
from __future__ import annotations

import csv
import json

import pytest

from ground_station.service.api import _recorder_status
from ground_station.service.storage import CsvRecorder


def _events(rec):
    events = []
    for line in open(rec.events_path, encoding="utf-8"):
        events.append(json.loads(line))
    return events


def test_recorder_off_by_default_creates_no_directory(tmp_path):
    # DEFAULT: nothing is written at startup — no directory exists until an
    # explicit start. This is the proof that recording is opt-in.
    rec = CsvRecorder(tmp_path, enabled=True)
    try:
        assert not rec.recording
        assert rec.session_dir is None
        assert rec.path is None
    finally:
        rec.stop()
    assert list(tmp_path.iterdir()) == []


def test_recorder_writes_long_format_csv(tmp_path):
    rec = CsvRecorder(tmp_path, enabled=True, flush_interval_s=0.05)
    assert rec.start()
    try:
        assert rec.recording
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
    assert not rec.start()
    try:
        assert not rec.recording
        assert rec.session_dir is None
        assert rec.path is None
        rec.note(0, {"k": 1}, received_ns=1)
    finally:
        rec.stop()
    assert rec.rows == 0
    assert list(tmp_path.iterdir()) == []


def test_recorder_bad_dir_counts_error(tmp_path):
    # A regular file as a path component makes mkdir(parents=True) fail,
    # which the recorder must swallow and count as an error, not raise.
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    rec = CsvRecorder(blocker / "sub" / "sessions", enabled=True)
    assert not rec.start()
    try:
        assert not rec.recording
        assert rec.path is None
        assert rec.errors >= 1
    finally:
        rec.stop()


def test_start_stop_start_creates_two_directories(tmp_path):
    rec = CsvRecorder(tmp_path, enabled=True)
    assert rec.start()
    first = rec.session_dir
    assert first is not None and first.exists()
    rec.stop()

    # Starting while stopped creates a SECOND, distinct directory.
    assert rec.start(label="second")
    second = rec.session_dir
    assert second is not None and second.exists()
    assert second != first
    assert second.name.endswith("-second")
    rec.stop()

    dirs = sorted(p.name for p in tmp_path.iterdir())
    assert len(dirs) == 2, dirs


def test_start_while_recording_is_noop(tmp_path):
    rec = CsvRecorder(tmp_path, enabled=True)
    assert rec.start()
    dir1 = rec.session_dir
    assert not rec.start()  # no-op while already recording
    assert rec.session_dir == dir1
    rec.stop()
    assert len(list(tmp_path.iterdir())) == 1


def test_manifest_fields(tmp_path):
    rec = CsvRecorder(tmp_path, enabled=True)
    rec.start(requested_by="operator", reason="unit test",
              label="lab", subscribe_layout={"schema_id": "r1", "slots": ["0"]},
              context={"started_commit": "abc1234", "firmware_elf": {"path": "OBJ/JX_FLY.axf", "size": 1}, "g_ctrl_select": 1})
    rec.note(0, {"status.arm": 1}, received_ns=5)
    rec.stop()

    with open(rec.manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["schema_version"] == 1
    assert manifest["requested_by"] == "operator"
    assert manifest["reason"] == "unit test"
    assert manifest["label"] == "lab"
    assert manifest["subscribe_layout"] == {"schema_id": "r1", "slots": ["0"]}
    assert manifest["context"]["started_commit"] == "abc1234"
    assert manifest["context"]["firmware_elf"]["size"] == 1
    assert manifest["context"]["g_ctrl_select"] == 1
    assert manifest["started_at"] and manifest["started_at_epoch"]
    assert manifest["stopped_at"] and manifest["stopped_at_epoch"]
    assert manifest["rows"] == 1
    assert set(manifest["files"]) == {"telemetry.csv", "events.jsonl",
                                      "manifest.json"}


def test_event_log_command_lifecycle_and_notes(tmp_path):
    rec = CsvRecorder(tmp_path, enabled=True)
    rec.start(requested_by="agent:test", reason="events")
    rec.add_event("command", {
        "id": 0x1E, "idx": 1, "value": 0.5, "transaction_id": 7,
        "lifecycle": "acknowledged", "reason": None,
    }, source="service")
    rec.add_event("arm_state", {"state": "armed"}, source="service")
    rec.add_event("stream_stall", {"status": "stalled"}, source="service")
    rec.stop()

    events = _events(rec)
    kinds = [e["kind"] for e in events]
    assert kinds == ["recording_start", "command", "arm_state", "stream_stall",
                     "recording_stop"]
    cmd = events[1]
    assert cmd["data"]["lifecycle"] == "acknowledged"
    assert cmd["data"]["id"] == 0x1E
    assert cmd["data"]["transaction_id"] == 7
    assert cmd["t"] > 0 and "iso" in cmd
    assert events[0]["kind"] == "recording_start"


def test_notes_buffered_before_start_flushed_on_start(tmp_path):
    rec = CsvRecorder(tmp_path, enabled=True)
    # While stopped, notes are buffered (not on disk) and retrievable.
    assert rec.buffered_notes() == []
    assert rec.add_note("before start", kind="goal", source="agent:x") == 1
    assert rec.buffered_notes() == [{"text": "before start", "kind": "goal",
                                     "source": "agent:x"}]
    # No recording yet -> still no directory.
    assert list(tmp_path.iterdir()) == []

    rec.start(requested_by="operator", reason="flush buffer")
    try:
        assert rec.add_note("during") == 0  # written straight through
    finally:
        rec.stop()

    events = _events(rec)
    note_events = [e for e in events if e["kind"] in ("note", "goal", "marker")]
    texts = [e["data"]["text"] for e in note_events]
    assert texts == ["before start", "during"]
    assert note_events[0]["kind"] == "goal"
    # Buffer cleared once flushed.
    assert rec.buffered_notes() == []


class _Stub:
    pass


def test_health_recorder_block_missing_recorder():
    # A service object without a recorder must not break /health.
    status = _recorder_status(_Stub())
    assert status["enabled"] is False
    assert status["recording"] is False
    assert status["path"] is None
    assert status["rows"] == 0


def test_health_recorder_block_reports_status_not_recording(tmp_path):
    rec = CsvRecorder(tmp_path, enabled=True)
    svc = _Stub()
    svc.recorder = rec
    # Enabled but NOT recording yet -> honest "recording": False.
    status = _recorder_status(svc)
    assert status["enabled"] is True
    assert status["recording"] is False
    assert status["path"] is None

    rec.start()
    try:
        rec.note(0, {"status.arm": 1}, received_ns=5)
        status = _recorder_status(svc)
        assert status["enabled"] is True
        assert status["recording"] is True
        assert status["path"] is not None
    finally:
        rec.stop()
    # After stop the CSV is flushed; rows reflect what was written.
    status = _recorder_status(svc)
    assert status["recording"] is False
    assert status["rows"] == 1