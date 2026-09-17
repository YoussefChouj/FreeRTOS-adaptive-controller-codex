"""Analysis tools for completed ground-station sessions."""
from __future__ import annotations

import csv
import json
import statistics
from dataclasses import dataclass
from typing import Any, Iterator

from ground_station.service.storage import SessionStore


@dataclass(frozen=True)
class StreamStats:
    stream_id: int
    count: int
    rate_hz: float | None  # estimated from median dt
    loss_events: int


@dataclass(frozen=True)
class TelemetryWindow:
    start_ns: int
    end_ns: int
    duration_ms: float
    records: int
    streams: dict[int, StreamStats]


def query_telemetry(store: SessionStore, session_id: str,
                   stream_id: int | None = None,
                   key: str | None = None,
                   since_ns: int | None = None,
                   until_ns: int | None = None) -> Iterator[dict[str, Any]]:
    """Yield telemetry records matching the filter.

    Args:
        store: Session store.
        session_id: Session to query.
        stream_id: Filter by stream ID (default: all).
        key: Filter by variable name (exact match in values JSON).
        since_ns: Inclusive start timestamp in nanoseconds.
        until_ns: Exclusive end timestamp.
    """
    sql = "SELECT time_ns,stream_id,sequence,source_time_ms,values_json FROM telemetry WHERE session_id=?"
    params: list[Any] = [session_id]
    if stream_id is not None:
        sql += " AND stream_id=?"
        params.append(stream_id)
    if since_ns is not None:
        sql += " AND time_ns>=?"
        params.append(since_ns)
    if until_ns is not None:
        sql += " AND time_ns<?"
        params.append(until_ns)
    sql += " ORDER BY time_ns,id"
    for row in store._db.execute(sql, params):
        values = json.loads(row[4])
        if key is not None and key not in values:
            continue
        yield {
            "time_ns": row[0],
            "stream_id": row[1],
            "sequence": row[2],
            "source_time_ms": row[3],
            "values": values,
        }


def telemetry_stats(store: SessionStore, session_id: str,
                   stream_id: int | None = None) -> dict[int, StreamStats]:
    """Compute per-stream statistics for a session."""
    stream_dts: dict[int, list[int]] = {}
    stream_count: dict[int, int] = {}
    prev_row: dict[int, dict[str, Any]] = {}

    for row in query_telemetry(store, session_id, stream_id=stream_id):
        sid = row["stream_id"]
        stream_count[sid] = stream_count.get(sid, 0) + 1
        if sid in prev_row:
            dt = row["time_ns"] - prev_row[sid]["time_ns"]
            stream_dts.setdefault(sid, []).append(dt)
        prev_row[sid] = row

    result: dict[int, StreamStats] = {}
    for sid, count in stream_count.items():
        dts = stream_dts.get(sid, [])
        median_ns = statistics.median(dts) if len(dts) >= 2 else None
        rate = (1e9 / median_ns) if median_ns else None
        # loss events: gaps > 5x median dt
        loss = 0
        if median_ns and len(dts) >= 2:
            threshold = median_ns * 5
            loss = sum(1 for dt in dts if dt > threshold)
        result[sid] = StreamStats(sid, count, rate, loss)
    return result


def compare_sessions(store: SessionStore, session_a: str, session_b: str,
                    stream_id: int, key: str) -> dict[str, Any]:
    """Compare one telemetry key across two sessions.

    Returns: {session_a: {min, max, mean, std, n}, session_b: {...},
              diff_mean, diff_max, better_session: "a"|"b"|"tie"}
    """
    def _extract(session_id: str) -> list[float]:
        vals = []
        for rec in query_telemetry(store, session_id, stream_id=stream_id, key=key):
            v = rec["values"].get(key)
            if v is not None:
                vals.append(float(v))
        return vals

    def _stats(vals: list[float]) -> dict[str, float]:
        if not vals:
            return {"min": 0.0, "max": 0.0, "mean": 0.0, "std": 0.0, "n": 0}
        return {
            "min": min(vals),
            "max": max(vals),
            "mean": statistics.mean(vals),
            "std": statistics.stdev(vals) if len(vals) > 1 else 0.0,
            "n": len(vals),
        }

    a_vals = _extract(session_a)
    b_vals = _extract(session_b)
    a_stats = _stats(a_vals)
    b_stats = _stats(b_vals)
    diff_mean = a_stats["mean"] - b_stats["mean"]
    diff_max = a_stats["max"] - b_stats["max"]
    # "better" = lower mean absolute value (closer to zero)
    if abs(a_stats["mean"]) < abs(b_stats["mean"]):
        better = "a"
    elif abs(b_stats["mean"]) < abs(a_stats["mean"]):
        better = "b"
    else:
        better = "tie"
    return {
        session_a: a_stats,
        session_b: b_stats,
        "diff_mean": diff_mean,
        "diff_max": diff_max,
        "better_session": better,
    }


def export_session_csv(store: SessionStore, session_id: str,
                      output_path: str, stream_id: int | None = None) -> None:
    """Export session telemetry to a CSV file.

    Columns: time_ns, stream_id, sequence, source_time_ms, <all value keys>
    """
    # Raise KeyError if session does not exist (API handler catches it → 404)
    store.session(session_id)
    records = list(query_telemetry(store, session_id, stream_id=stream_id))
    if not records:
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["time_ns", "stream_id", "sequence", "source_time_ms"])
        return

    # Collect all unique value keys from the first N records to avoid scanning all
    all_keys: set[str] = set()
    for rec in records[:100]:
        all_keys.update(rec["values"].keys())
    value_keys = sorted(all_keys)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["time_ns", "stream_id", "sequence", "source_time_ms"] + value_keys)
        for rec in records:
            row = [rec["time_ns"], rec["stream_id"], rec["sequence"], rec["source_time_ms"]]
            row.extend(rec["values"].get(k, "") for k in value_keys)
            writer.writerow(row)
