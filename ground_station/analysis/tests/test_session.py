"""Tests for ground_station.analysis.session."""
from __future__ import annotations

import csv
import json
import tempfile

from ground_station.analysis.session import (
    compare_sessions,
    export_session_csv,
    query_telemetry,
    telemetry_stats,
)
from ground_station.service.storage import SessionStore


def _seed_session(store: SessionStore) -> str:
    sid = store.start_session("test-schema", source="test")
    # Stream 1: 3 records, 10ms apart
    store.append_telemetry(sid, stream_id=1, sequence=0,
                           values={"altitude": 10.0, "speed": 1.0},
                           source_time_ms=100, time_ns=1_000_000_000)
    store.append_telemetry(sid, stream_id=1, sequence=1,
                           values={"altitude": 11.0, "speed": 2.0},
                           source_time_ms=105, time_ns=1_010_000_000)
    store.append_telemetry(sid, stream_id=1, sequence=2,
                           values={"altitude": 12.0, "speed": 3.0},
                           source_time_ms=110, time_ns=1_020_000_000)
    # Stream 2: 2 records, 15ms apart
    store.append_telemetry(sid, stream_id=2, sequence=0,
                           values={"temperature": 25.0},
                           source_time_ms=100, time_ns=1_000_000_000)
    store.append_telemetry(sid, stream_id=2, sequence=1,
                           values={"temperature": 26.0},
                           source_time_ms=105, time_ns=1_015_000_000)
    store.append_event(sid, "test_event", {"info": "hello"}, time_ns=1_005_000_000)
    return sid


class TestQueryTelemetry:
    def test_returns_all_records_by_default(self):
        store = SessionStore()
        sid = _seed_session(store)
        rows = list(query_telemetry(store, sid))
        assert len(rows) == 5  # 3 stream1 + 2 stream2

    def test_filters_by_stream_id(self):
        store = SessionStore()
        sid = _seed_session(store)
        rows = list(query_telemetry(store, sid, stream_id=1))
        assert len(rows) == 3
        assert all(r["stream_id"] == 1 for r in rows)

    def test_filters_by_key(self):
        store = SessionStore()
        sid = _seed_session(store)
        rows = list(query_telemetry(store, sid, key="altitude"))
        assert all("altitude" in r["values"] for r in rows)
        assert all(r["stream_id"] == 1 for r in rows)

    def test_filters_by_time_range(self):
        store = SessionStore()
        sid = _seed_session(store)
        # SQL: time_ns >= 1_005_000_000 AND time_ns < 1_020_000_001 (until_ns is exclusive)
        # stream1 seq0 (1.0s), stream1 seq1 (1.01s), stream2 seq0 (1.015s) → 3 rows
        rows = list(query_telemetry(store, sid, since_ns=1_005_000_000,
                                   until_ns=1_020_000_001))
        assert len(rows) == 3

    def test_filters_by_stream_and_key(self):
        store = SessionStore()
        sid = _seed_session(store)
        rows = list(query_telemetry(store, sid, stream_id=1, key="speed"))
        assert len(rows) == 3
        assert all("speed" in r["values"] for r in rows)


class TestTelemetryStats:
    def test_counts_per_stream(self):
        store = SessionStore()
        sid = _seed_session(store)
        stats = telemetry_stats(store, sid)
        assert stats[1].count == 3
        assert stats[2].count == 2

    def test_estimates_rate_hz(self):
        store = SessionStore()
        sid = _seed_session(store)
        stats = telemetry_stats(store, sid)
        # 10 ms median gap → ~100 Hz
        assert stats[1].rate_hz is not None
        assert 90 < stats[1].rate_hz < 110

    def test_no_loss_on_regular_telemetry(self):
        store = SessionStore()
        sid = _seed_session(store)
        stats = telemetry_stats(store, sid)
        assert stats[1].loss_events == 0
        assert stats[2].loss_events == 0

    def test_detects_loss_with_large_gap(self):
        """Loss detection: need 4+ records (3+ gaps) so median < max gap."""
        store = SessionStore()
        sid = store.start_session("s", source="test")
        # 4 records: gaps = 5ms, 5ms, 1000ms
        store.append_telemetry(sid, 1, 0, {"v": 1.0}, time_ns=1_000_000_000)
        store.append_telemetry(sid, 1, 1, {"v": 2.0}, time_ns=1_005_000_000)   # 5ms
        store.append_telemetry(sid, 1, 2, {"v": 3.0}, time_ns=1_010_000_000)   # 5ms
        store.append_telemetry(sid, 1, 3, {"v": 4.0}, time_ns=2_010_000_000)   # 1000ms
        stats = telemetry_stats(store, sid)
        # dts=[5ms, 5ms, 1000ms], median=5ms, threshold=25ms, 1000ms > 25ms → loss=1
        assert stats[1].loss_events >= 1

    def test_filters_by_stream_id(self):
        store = SessionStore()
        sid = _seed_session(store)
        stats = telemetry_stats(store, sid, stream_id=1)
        assert 1 in stats
        assert 2 not in stats


class TestCompareSessions:
    def test_computes_stats_for_each_session(self):
        store = SessionStore()
        sid_a = store.start_session("s", source="test")
        store.append_telemetry(sid_a, 1, 0, {"altitude": 10.0}, time_ns=1_000_000_000)
        store.append_telemetry(sid_a, 1, 1, {"altitude": 20.0}, time_ns=1_010_000_000)
        sid_b = store.start_session("s", source="test")
        store.append_telemetry(sid_b, 1, 0, {"altitude": 15.0}, time_ns=1_000_000_000)
        store.append_telemetry(sid_b, 1, 1, {"altitude": 25.0}, time_ns=1_010_000_000)
        result = compare_sessions(store, sid_a, sid_b, stream_id=1, key="altitude")
        assert result[sid_a]["mean"] == 15.0
        assert result[sid_b]["mean"] == 20.0
        assert result["diff_mean"] == -5.0
        assert result["better_session"] == "a"  # lower absolute mean

    def test_empty_session_returns_zeros(self):
        store = SessionStore()
        sid_a = store.start_session("s", source="test")
        sid_b = store.start_session("s", source="test")
        result = compare_sessions(store, sid_a, sid_b, stream_id=1, key="altitude")
        assert result[sid_a]["n"] == 0
        assert result[sid_b]["n"] == 0


class TestExportCsv:
    def test_writes_header_and_rows(self):
        store = SessionStore()
        sid = _seed_session(store)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            path = f.name
        export_session_csv(store, sid, path)
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 5
        assert "time_ns" in reader.fieldnames
        assert "altitude" in reader.fieldnames
        assert "temperature" in reader.fieldnames

    def test_empty_session_produces_header_only(self):
        store = SessionStore()
        sid = store.start_session("s", source="test")
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            path = f.name
        export_session_csv(store, sid, path)
        with open(path, newline="", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 1  # header only

    def test_filters_by_stream_id(self):
        store = SessionStore()
        sid = _seed_session(store)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            path = f.name
        export_session_csv(store, sid, path, stream_id=1)
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 3
