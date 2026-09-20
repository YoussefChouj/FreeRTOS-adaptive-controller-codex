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
    # S6: jitter and gap analysis
    jitter_mean_ns: float | None
    jitter_std_ns: float | None
    jitter_max_ns: int | None
    gap_count: int
    gap_max_ns: int | None
    effective_rate_hz: float | None   # estimated from source_time_ms deltas
    source_clock_drift_ppm: float | None  # clock drift vs wall-clock (ppm)


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
    """Compute per-stream statistics for a session.

    S6 additions:
      * jitter_mean_ns / jitter_std_ns / jitter_max_ns — inter-frame jitter stats
      * gap_count / gap_max_ns — gap events (dt > 5x median dt)
      * effective_rate_hz — rate estimated from firmware source_time_ms clock
      * source_clock_drift_ppm — how much the firmware clock drifts vs wall clock
    """
    stream_dts: dict[int, list[int]] = {}
    stream_count: dict[int, int] = {}
    prev_row: dict[int, dict[str, Any]] = {}
    # S6: track source_time_ms deltas for effective rate
    source_dts: dict[int, list[int]] = {}
    prev_source_ms: dict[int, float] = {}

    for row in query_telemetry(store, session_id, stream_id=stream_id):
        sid = row["stream_id"]
        stream_count[sid] = stream_count.get(sid, 0) + 1
        if sid in prev_row:
            dt = row["time_ns"] - prev_row[sid]["time_ns"]
            stream_dts.setdefault(sid, []).append(dt)
        # S6: source_time_ms deltas (firmware clock)
        st_ms = row.get("source_time_ms")
        if st_ms is not None:
            prev_ms = prev_source_ms.get(sid)
            if prev_ms is not None:
                dt_ms = int(st_ms) - int(prev_ms)
                if dt_ms >= 0:  # ignore clock wrap
                    source_dts.setdefault(sid, []).append(dt_ms)
            prev_source_ms[sid] = float(st_ms)
        prev_row[sid] = row

    result: dict[int, StreamStats] = {}
    for sid, count in stream_count.items():
        dts = stream_dts.get(sid, [])
        median_ns = statistics.median(dts) if len(dts) >= 2 else None
        rate = (1e9 / median_ns) if median_ns else None
        # loss events: gaps > 5x median dt
        loss = 0
        gap_max = None
        if median_ns and len(dts) >= 2:
            threshold = median_ns * 5
            loss = sum(1 for dt in dts if dt > threshold)
            gap_max = max((dt for dt in dts if dt > threshold), default=None)

        # S6: jitter stats (deviation from median dt)
        jitter_vals = []
        if median_ns and len(dts) >= 2:
            jitter_vals = [abs(dt - median_ns) for dt in dts]
        jitter_mean = statistics.mean(jitter_vals) if jitter_vals else None
        jitter_std = statistics.stdev(jitter_vals) if len(jitter_vals) > 1 else None
        jitter_max = max(jitter_vals) if jitter_vals else None

        # S6: effective rate from firmware source_time_ms clock
        src_dts = source_dts.get(sid, [])
        src_median_ms = statistics.median(src_dts) if len(src_dts) >= 2 else None
        effective_rate = (1000.0 / src_median_ms) if src_median_ms else None

        # S6: source clock drift (ppm vs wall clock)
        # drift_ppm = ((wall_rate - source_rate) / wall_rate) * 1e6
        drift_ppm = None
        if median_ns and src_median_ms is not None:
            wall_rate_hz = 1e9 / median_ns
            src_rate_hz = 1000.0 / src_median_ms
            if wall_rate_hz > 0:
                drift_ppm = ((wall_rate_hz - src_rate_hz) / wall_rate_hz) * 1e6

        result[sid] = StreamStats(
            sid, count, rate, loss,
            jitter_mean, jitter_std, jitter_max,
            loss, gap_max,
            effective_rate, drift_ppm,
        )
    return result


def compute_gaps(store: SessionStore, session_id: str,
                stream_id: int | None = None,
                *, gap_threshold_multiplier: float = 5.0
                ) -> dict[int, list[dict[str, Any]]]:
    """Return all gap events per stream where dt > gap_threshold_multiplier × median.

    S6: gap analysis for the capture system. Useful for identifying Wi-Fi packet loss
    bursts and diagnosing telemetry link quality.

    Args:
        store: Session store.
        session_id: Session to analyse.
        stream_id: Filter by stream (default: all streams).
        gap_threshold_multiplier: Multiplier on median dt to define a gap. Default 5.0
            means a gap is recorded when dt > 5 × median_dt.

    Returns:
        { stream_id: [ { time_ns, dt_ns, sequence, next_sequence }, ... ] }
    """
    stream_dts: dict[int, list[tuple[int, int, int, int]]] = {}
    prev_row: dict[int, dict[str, Any]] = {}

    for row in query_telemetry(store, session_id, stream_id=stream_id):
        sid = row["stream_id"]
        if sid in prev_row:
            dt = row["time_ns"] - prev_row[sid]["time_ns"]
            stream_dts.setdefault(sid, []).append(
                (dt, row["time_ns"], row["sequence"], prev_row[sid]["sequence"])
            )
        prev_row[sid] = row

    result: dict[int, list[dict[str, Any]]] = {}
    for sid, dts in stream_dts.items():
        if len(dts) < 2:
            continue
        median_ns = statistics.median([d[0] for d in dts])
        threshold = median_ns * gap_threshold_multiplier
        gaps = [
            {"time_ns": t, "dt_ns": dt, "sequence": seq, "prev_sequence": prev_seq}
            for dt, t, seq, prev_seq in dts
            if dt > threshold
        ]
        result[sid] = gaps
    return result


def compute_jitter(store: SessionStore, session_id: str,
                  stream_id: int | None = None,
                  ) -> dict[int, dict[str, float | None]]:
    """Return jitter statistics per stream.

    S6: jitter = |dt - median_dt| for each consecutive pair.
    Returns per-stream: { jitter_mean_ns, jitter_std_ns, jitter_max_ns, jitter_median_ns }.

    Useful for:
      * Verifying that a 50 Hz subscription actually delivers 50 Hz with low jitter
      * Detecting scheduling irregularities in the firmware Send_Task
      * Setting appropriate timeouts in downstream analysers
    """
    stream_dts: dict[int, list[int]] = {}
    prev_row: dict[int, dict[str, Any]] = {}

    for row in query_telemetry(store, session_id, stream_id=stream_id):
        sid = row["stream_id"]
        if sid in prev_row:
            dt = row["time_ns"] - prev_row[sid]["time_ns"]
            stream_dts.setdefault(sid, []).append(dt)
        prev_row[sid] = row

    result: dict[int, dict[str, float | None]] = {}
    # Iterate over all streams that had at least one record (prev_row),
    # not just those with 2+ records (stream_dts).
    for sid in prev_row:
        dts = stream_dts.get(sid, [])
        if len(dts) < 2:
            result[sid] = {
                "jitter_mean_ns": None, "jitter_std_ns": None,
                "jitter_max_ns": None, "jitter_median_ns": None,
            }
            continue
        median_ns = statistics.median(dts)
        jitters = [abs(dt - median_ns) for dt in dts]
        result[sid] = {
            "jitter_mean_ns": statistics.mean(jitters),
            "jitter_std_ns": statistics.stdev(jitters) if len(jitters) > 1 else None,
            "jitter_max_ns": max(jitters),
            "jitter_median_ns": statistics.median(jitters),
        }
    return result


def compute_effective_rate(store: SessionStore, session_id: str,
                           stream_id: int | None = None,
                           ) -> dict[int, dict[str, Any]]:
    """Compute effective rate from firmware source_time_ms clock deltas.

    S6: distinguishes between wall-clock rate (receiver-side) and firmware-clock rate
    (source-side). The firmware clock can drift; this function reports:
      * effective_rate_hz — rate from firmware source_time_ms
      * source_clock_drift_ppm — drift vs wall clock in ppm
      * clock_wrap_count — how many times source_time_ms wrapped (mod overflow)

    Use this to verify that a subscription at divider D actually delivers D Hz
    according to the firmware's own clock, independent of Wi-Fi delivery jitter.
    """
    source_dts: dict[int, list[int]] = {}
    wrap_count: dict[int, int] = {}
    prev_ms: dict[int, float] = {}

    for row in query_telemetry(store, session_id, stream_id=stream_id):
        sid = row["stream_id"]
        st_ms = row.get("source_time_ms")
        if st_ms is None:
            continue
        prev = prev_ms.get(sid)
        if prev is not None:
            dt_ms = int(st_ms) - int(prev)
            if dt_ms < 0:
                # Clock wrap: source_time_ms is uint32 and wrapped
                wrap_count[sid] = wrap_count.get(sid, 0) + 1
            else:
                source_dts.setdefault(sid, []).append(dt_ms)
        prev_ms[sid] = float(st_ms)

    result: dict[int, dict[str, Any]] = {}
    for sid in list(source_dts.keys()) + list(wrap_count.keys()):
        dts = source_dts.get(sid, [])
        median_ms = statistics.median(dts) if len(dts) >= 2 else None
        src_rate = (1000.0 / median_ms) if median_ms else None

        # Compute wall-clock rate for drift calculation
        wall_dts: list[int] = []
        prev_row_local: dict[int, dict[str, Any]] = {}
        for row in query_telemetry(store, session_id, stream_id=stream_id):
            s = row["stream_id"]
            if s != sid:
                continue
            if s in prev_row_local:
                wall_dts.append(row["time_ns"] - prev_row_local[s]["time_ns"])
            prev_row_local[s] = row
        wall_median_ns = statistics.median(wall_dts) if len(wall_dts) >= 2 else None
        wall_rate = (1e9 / wall_median_ns) if wall_median_ns else None
        drift_ppm = None
        if wall_rate is not None and src_rate is not None and src_rate > 0:
            drift_ppm = ((wall_rate - src_rate) / wall_rate) * 1e6

        result[sid] = {
            "effective_rate_hz": round(src_rate, 3) if src_rate is not None else None,
            "source_clock_drift_ppm": round(drift_ppm, 3) if drift_ppm is not None else None,
            "clock_wrap_count": wrap_count.get(sid, 0),
            "sample_count": len(dts),
        }
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
