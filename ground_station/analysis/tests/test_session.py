"""Tests for ground_station.analysis.session."""
from __future__ import annotations

import csv
import json
import tempfile

from ground_station.analysis.session import (
    compare_sessions,
    compute_effective_rate,
    compute_gaps,
    compute_jitter,
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


# ─────────────────────────────────────────────────────────────────────────────
# S6: jitter / gap / effective-rate tests
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeGaps:
    """Tests for compute_gaps: detects dt > threshold gaps per stream."""

    def test_no_gaps_on_regular_telemetry(self):
        """5 ms intervals with 5x threshold → no gaps."""
        store = SessionStore()
        sid = store.start_session("s", source="test")
        # 5 records at 5 ms = 50 Hz → threshold = 25 ms
        t = 1_000_000_000
        for i in range(5):
            store.append_telemetry(sid, 1, i, {"v": float(i)},
                                   source_time_ms=t // 1_000_000,
                                   time_ns=t)
            t += 5_000_000  # 5 ms
        gaps = compute_gaps(store, sid)
        assert gaps[1] == []

    def test_detects_gap_above_threshold(self):
        """A single 200 ms gap among 5 ms intervals is detected."""
        store = SessionStore()
        sid = store.start_session("s", source="test")
        t = 1_000_000_000
        # 3 normal records at 5 ms — increment t inside the loop
        for i in range(3):
            store.append_telemetry(sid, 1, i, {"v": float(i)},
                                   source_time_ms=t // 1_000_000,
                                   time_ns=t)
            t += 5_000_000
        # 1 large gap (200 ms)
        t += 200_000_000
        store.append_telemetry(sid, 1, 3, {"v": 3.0},
                               source_time_ms=t // 1_000_000,
                               time_ns=t)
        gaps = compute_gaps(store, sid)
        # threshold = 5 * 5ms = 25 ms; wall gaps: 5ms, 5ms, 205ms → 1 gap
        assert len(gaps[1]) == 1
        assert gaps[1][0]["dt_ns"] == 205_000_000

    def test_gap_includes_sequence_info(self):
        """Gap record carries sequence numbers of the gap endpoints."""
        store = SessionStore()
        sid = store.start_session("s", source="test")
        # Need 4 records so there are 3 gaps; median is based on the 2 non-gap dts
        t = 1_000_000_000
        store.append_telemetry(sid, 1, 0, {"v": 0.0}, time_ns=t)
        t += 5_000_000
        store.append_telemetry(sid, 1, 1, {"v": 1.0}, time_ns=t)
        t += 1_000_000_000  # 1 s gap
        store.append_telemetry(sid, 1, 2, {"v": 2.0}, time_ns=t)
        t += 5_000_000
        store.append_telemetry(sid, 1, 3, {"v": 3.0}, time_ns=t)
        gaps = compute_gaps(store, sid)
        assert len(gaps[1]) == 1
        assert gaps[1][0]["sequence"] == 2
        assert gaps[1][0]["prev_sequence"] == 1

    def test_custom_threshold_multiplier(self):
        """With 2x multiplier, smaller gaps are flagged."""
        store = SessionStore()
        sid = store.start_session("s", source="test")
        t = 1_000_000_000
        for i in range(4):
            store.append_telemetry(sid, 1, i, {"v": float(i)}, time_ns=t)
            t += 5_000_000
        t += 15_000_000  # 3x the 5ms interval → 15ms gap
        store.append_telemetry(sid, 1, 4, {"v": 4.0}, time_ns=t)
        # default 5x threshold = 25 ms → not flagged
        gaps_default = compute_gaps(store, sid)
        assert gaps_default[1] == []
        # 2x threshold = 10 ms → 15ms gap is flagged
        gaps_custom = compute_gaps(store, sid, gap_threshold_multiplier=2.0)
        assert len(gaps_custom[1]) == 1

    def test_empty_and_single_record_session(self):
        """Empty session or stream with <2 records returns no gaps."""
        store = SessionStore()
        sid_empty = store.start_session("s", source="test")
        assert compute_gaps(store, sid_empty) == {}

        sid_one = store.start_session("s", source="test")
        store.append_telemetry(sid_one, 1, 0, {"v": 1.0}, time_ns=1_000_000_000)
        # single record → no consecutive pair → empty
        assert compute_gaps(store, sid_one) == {}


class TestComputeJitter:
    """Tests for compute_jitter: |dt - median_dt| statistics per stream."""

    def test_jitter_near_zero_on_regular_telemetry(self):
        """Perfectly regular 10 ms intervals → jitter_mean close to zero."""
        store = SessionStore()
        sid = store.start_session("s", source="test")
        t = 1_000_000_000
        for i in range(5):
            store.append_telemetry(sid, 1, i, {"v": float(i)}, time_ns=t)
            t += 10_000_000  # 10 ms
        jitter = compute_jitter(store, sid)
        # all dts = 10 ms = median → |dt - median| = 0 for every pair
        assert jitter[1]["jitter_mean_ns"] == 0.0
        assert jitter[1]["jitter_max_ns"] == 0

    def test_jitter_nonzero_on_irregular_telemetry(self):
        """Irregular intervals produce non-zero jitter."""
        store = SessionStore()
        sid = store.start_session("s", source="test")
        # 5 records give 4 gaps.  Precompute timestamps so all intervals are used.
        intervals = [8_000_000, 10_000_000, 10_000_000, 12_000_000]
        t = 1_000_000_000
        # record 0 at initial t; then t advances by each interval for the next record
        store.append_telemetry(sid, 1, 0, {"v": 0.0}, time_ns=t)
        for i, dt in enumerate(intervals, start=1):
            t += dt
            store.append_telemetry(sid, 1, i, {"v": float(i)}, time_ns=t)
        jitter = compute_jitter(store, sid)
        # dts = [8, 10, 10, 12] ms → sorted [8,10,10,12] → median=10ms
        # deviations: [2, 0, 0, 2] ms → mean = 1ms
        assert jitter[1]["jitter_mean_ns"] == 1_000_000.0
        assert jitter[1]["jitter_max_ns"] == 2_000_000

    def test_jitter_std_reported(self):
        """jitter_std_ns is present and non-negative."""
        store = SessionStore()
        sid = store.start_session("s", source="test")
        t = 1_000_000_000
        for i in range(5):
            store.append_telemetry(sid, 1, i, {"v": float(i)}, time_ns=t)
            t += 10_000_000
        jitter = compute_jitter(store, sid)
        assert jitter[1]["jitter_std_ns"] is not None
        assert jitter[1]["jitter_std_ns"] >= 0

    def test_single_record_returns_none_stats(self):
        """A stream with one record cannot compute jitter."""
        store = SessionStore()
        sid = store.start_session("s", source="test")
        store.append_telemetry(sid, 1, 0, {"v": 1.0}, time_ns=1_000_000_000)
        jitter = compute_jitter(store, sid)
        assert jitter[1]["jitter_mean_ns"] is None
        assert jitter[1]["jitter_std_ns"] is None
        assert jitter[1]["jitter_max_ns"] is None

    def test_filters_by_stream_id(self):
        """Only the specified stream is included in the result."""
        store = SessionStore()
        sid = _seed_session(store)  # stream 1 + stream 2
        jitter = compute_jitter(store, sid, stream_id=1)
        assert 1 in jitter
        assert 2 not in jitter


class TestComputeEffectiveRate:
    """Tests for compute_effective_rate: firmware source_time_ms clock analysis."""

    def test_effective_rate_from_source_time_ms(self):
        """Rate is computed from source_time_ms deltas, not wall-clock."""
        store = SessionStore()
        sid = store.start_session("s", source="test")
        # 10 ms apart in firmware clock
        for i in range(4):
            store.append_telemetry(sid, 1, i, {"v": float(i)},
                                   source_time_ms=100 + i * 10,
                                   time_ns=1_000_000_000 + i * 15_000_000)
        # wall clock is 15 ms apart → 66.7 Hz; source is 10 ms apart → 100 Hz
        result = compute_effective_rate(store, sid)
        # effective rate from source_time_ms should be ~100 Hz
        assert result[1]["effective_rate_hz"] is not None
        assert 95 < result[1]["effective_rate_hz"] < 105

    def test_detects_clock_wrap_and_excludes_from_rate(self):
        """A uint32 wrap is counted but its delta is excluded; subsequent valid deltas remain."""
        store = SessionStore()
        sid = store.start_session("s", source="test")
        # record 0: near uint32 max
        store.append_telemetry(sid, 1, 0, {"v": 0.0},
                               source_time_ms=4_294_967_000,
                               time_ns=1_000_000_000)
        # record 1: wrapped to ~0 — delta excluded (negative)
        store.append_telemetry(sid, 1, 1, {"v": 1.0},
                               source_time_ms=1_000,
                               time_ns=1_010_000_000)
        # record 2: 10 ms later — valid delta included
        store.append_telemetry(sid, 1, 2, {"v": 2.0},
                               source_time_ms=1_010,
                               time_ns=1_020_000_000)
        # record 3: another 10 ms — valid delta included
        store.append_telemetry(sid, 1, 3, {"v": 3.0},
                               source_time_ms=1_020,
                               time_ns=1_030_000_000)
        result = compute_effective_rate(store, sid)
        assert result[1]["clock_wrap_count"] == 1
        # 2 valid deltas of 10 ms → ~100 Hz
        assert 95 < result[1]["effective_rate_hz"] < 105

    def test_missing_source_time_returns_empty(self):
        """Streams with no source_time_ms data produce empty effective_rate dict."""
        store = SessionStore()
        sid = store.start_session("s", source="test")
        store.append_telemetry(sid, 1, 0, {"v": 0.0}, time_ns=1_000_000_000)
        store.append_telemetry(sid, 1, 1, {"v": 1.0}, time_ns=1_010_000_000)
        result = compute_effective_rate(store, sid)
        assert result == {}

    def test_clock_drift_ppm_reported(self):
        """source_clock_drift_ppm is computed when both clocks are available."""
        store = SessionStore()
        sid = store.start_session("s", source="test")
        # source runs at exactly 10 ms (100 Hz); wall clock also 10 ms (100 Hz)
        for i in range(4):
            store.append_telemetry(sid, 1, i, {"v": float(i)},
                                   source_time_ms=100 + i * 10,
                                   time_ns=1_000_000_000 + i * 10_000_000)
        result = compute_effective_rate(store, sid)
        # drift should be near zero (both clocks aligned)
        assert result[1]["source_clock_drift_ppm"] is not None
        assert abs(result[1]["source_clock_drift_ppm"]) < 100  # within 100 ppm

    def test_filters_by_stream_id(self):
        """Only the specified stream appears in the result."""
        store = SessionStore()
        sid = _seed_session(store)  # stream 1 + stream 2
        result = compute_effective_rate(store, sid, stream_id=2)
        assert 2 in result
        assert 1 not in result

    def test_sample_count_excludes_wrapped_dts(self):
        """sample_count reflects non-wrapped deltas only."""
        store = SessionStore()
        sid = store.start_session("s", source="test")
        store.append_telemetry(sid, 1, 0, {"v": 0.0},
                               source_time_ms=100, time_ns=1_000_000_000)
        store.append_telemetry(sid, 1, 1, {"v": 1.0},
                               source_time_ms=110, time_ns=1_010_000_000)
        store.append_telemetry(sid, 1, 2, {"v": 2.0},
                               source_time_ms=120, time_ns=1_020_000_000)
        # 3 records → 2 source deltas
        result = compute_effective_rate(store, sid)
        assert result[1]["sample_count"] == 2
