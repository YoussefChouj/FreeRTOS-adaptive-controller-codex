"""Ingest-lag histogram: buckets, the exact 200 ms count, percentiles, a flight window, and the real ingest loop
feeding it."""
from __future__ import annotations

import threading
import time

from ground_station.comm import lag_histogram as lh
from ground_station.comm.wifi_bridge import WifiBridge

MS = 1_000_000
S = 1_000_000_000


def test_buckets_and_exact_limit_edge():
    h = lh.LagHistogram()
    for lag_ms in (0.1, 0.25, 0.26, 199.9, 200.0, 200.001, 9000):
        h.add(int(lag_ms * MS), 100 * S)
    snap = h.snapshot()
    counts = snap["counts"]
    assert snap["total"] == 7 and len(counts) == len(lh.EDGES_MS) + 1
    assert counts[0] == 2                       # 0.1 and exactly 0.25 ms: an edge is the bucket's inclusive top
    assert counts[1] == 1                       # 0.26 ms
    assert counts[lh.EDGES_MS.index(200)] == 2  # 199.9 and exactly 200 ms are not over the limit
    assert snap["over_limit"] == 2              # 200.001 ms and 9 s are
    assert counts[-1] == 1 and snap["max_ms"] == 9000.0


def test_percentiles_report_bucket_upper_edge():
    counts = [0] * (len(lh.EDGES_MS) + 1)
    counts[lh.EDGES_MS.index(1)] = 98
    counts[lh.EDGES_MS.index(50)] = 2
    assert lh.percentile_ms(counts, 0.50) == 1.0
    assert lh.percentile_ms(counts, 0.98) == 1.0
    assert lh.percentile_ms(counts, 0.99) == 50.0
    assert lh.percentile_ms([0] * len(counts), 0.5) is None
    counts[-1] = 1000
    assert lh.percentile_ms(counts, 0.99) == float("inf")


def test_window_subtracts_and_takes_the_exact_max_inside_it():
    h = lh.LagHistogram()
    h.add(500 * MS, 10 * S)                     # before the flight: must not count
    before = h.snapshot(since_s=0)
    h.add(3 * MS, 20 * S)
    h.add(int(7.2 * MS), 20 * S + 5)            # same second: the per-second max keeps the larger
    h.add(1 * MS, 21 * S)
    after = h.snapshot(since_s=0)
    w = lh.window(before, after, 20, 21)
    assert w["frames"] == 3 and w["over_200ms"] == 0
    assert w["max_ms"] == 7.2                   # exact, not the 7.5 bucket edge, and not the 500 ms before it
    assert w["p50_ms"] == 3.0
    assert [s for s, _ in h.snapshot(since_s=20)["seconds"]] == [20, 21]


def test_seconds_ring_is_bounded():
    h = lh.LagHistogram()
    for sec in range(lh.SECONDS_KEPT + 50):
        h.add(MS, sec * S)
    secs = h.snapshot(since_s=0)["seconds"]
    assert len(secs) == lh.SECONDS_KEPT and secs[0][0] == 50


def test_ingest_loop_files_every_frame():
    """The real ingest loop: each queued frame's lag (receive stamp -> callback done) lands in the histogram, so a
    frame that waited 250 ms in the queue counts as over the limit."""
    bridge = WifiBridge(vofa_enabled=False)
    bridge._publish_telem = lambda *a: None
    done = threading.Event()
    seen = []

    def ingest(tag, payload, t_ns):
        seen.append(t_ns)
        if len(seen) == 3:
            done.set()

    ingest.takes_time_ns = True
    bridge._on_telemetry = ingest
    now = time.time_ns()
    for t_ns in (now, now - 250 * MS, now):
        bridge._ingest_q.put(("attitude", b"", t_ns))
    t = threading.Thread(target=bridge._ingest_loop, daemon=True)
    t.start()
    try:
        assert done.wait(2.0)
        deadline = time.monotonic() + 1.0
        while bridge.ingest_lag_hist.snapshot()["total"] < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        snap = bridge.ingest_lag_hist.snapshot(since_s=now // S)
        assert snap["total"] == 3
        assert snap["over_limit"] == 1
        assert 250 <= snap["max_ms"] < 1000
        assert max(m for _, m in snap["seconds"]) == snap["max_ms"]
    finally:
        bridge._stop.set()
        t.join(timeout=1.0)
