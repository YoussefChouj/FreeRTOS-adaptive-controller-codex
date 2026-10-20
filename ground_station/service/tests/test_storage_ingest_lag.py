"""SessionStore ingest must not block on the store lock (lab 10-19 ingest lag).

z1, mem1 and 1017-E1 logged 0.5-1.3 s ingest_lag_s and stale_telemetry
aborts: the ingest thread inserted, pruned and committed under the lock that
paged reads hold. Appends now only queue rows for a writer thread.
"""
from __future__ import annotations

import statistics
import threading
import time

from ground_station.service.storage import SessionStore


def _count(store, table):
    return store._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_append_does_not_wait_for_a_reader_holding_the_lock():
    store = SessionStore(max_rows=100)
    sid = store.start_session("s")
    held, release = threading.Event(), threading.Event()

    def slow_reader():
        with store._lock:
            held.set()
            release.wait(2.0)

    reader = threading.Thread(target=slow_reader)
    reader.start()
    assert held.wait(2.0)
    try:
        t0 = time.perf_counter()
        store.append_telemetry(sid, 0, 1, {"x": 1.0})
        store.append_raw_frame(sid, "rx", b"\x01\x02")
        blocked_s = time.perf_counter() - t0
    finally:
        release.set()
        reader.join()
    assert blocked_s < 0.005
    assert [r["sequence"] for r in store.iter_records(sid)] == [1]


def test_appends_stay_fast_with_pruning_and_a_concurrent_reader():
    store = SessionStore(max_rows=100)          # prunes every 100 rows
    sid = store.start_session("s")
    stop = threading.Event()
    reads = []

    def reader():
        while not stop.is_set():
            reads.append(len(list(store.iter_records(sid, limit=200))))

    thread = threading.Thread(target=reader)
    thread.start()
    costs = []
    try:
        for seq in range(700):                   # > 600 frames, 7 prunes
            t0 = time.perf_counter()
            store.append_raw_frame(sid, "rx", b"\x00" * 32)
            store.append_telemetry(sid, 2, seq, {"x": seq, "y": -seq, "z": 0.5})
            costs.append(time.perf_counter() - t0)
            if seq % 50 == 0:
                time.sleep(0.002)                # let the reader interleave
    finally:
        stop.set()
        thread.join()
    costs.sort()
    assert statistics.median(costs) < 0.001
    assert costs[int(0.99 * len(costs))] < 0.005
    assert reads                                 # the reader really ran
    assert store.flush()
    assert _count(store, "telemetry") <= 200     # the prune ran
    assert _count(store, "raw_frames") <= 200
    newest = list(store.iter_records(sid))
    assert newest[-1]["sequence"] == 699
    assert store.write_errors == 0


def test_records_are_readable_right_after_append():
    store = SessionStore()
    sid = store.start_session("s")
    for seq in range(3):
        store.append_telemetry(sid, 1, seq, {"v": seq}, source_time_ms=10 * seq,
                               time_ns=1_000 * (seq + 1))
    store.append_event(sid, "note", {"text": "hi"}, time_ns=2_500)
    records = list(store.iter_records(sid))
    assert [r["type"] for r in records] == ["telemetry", "telemetry", "event", "telemetry"]
    assert [r["values"] for r in records if r["type"] == "telemetry"] == [
        {"v": 0}, {"v": 1}, {"v": 2}]
    assert records[-1]["source_time_ms"] == 20


def test_close_flushes_commits_and_joins_the_writer(tmp_path):
    path = tmp_path / "s.db"
    store = SessionStore(path)
    sid = store.start_session("s")
    for seq in range(500):
        store.append_telemetry(sid, 0, seq, {"x": seq})
    writer = store._writer
    assert writer is not None and writer.is_alive()
    store.close()
    assert not writer.is_alive()
    reopened = SessionStore(path)
    assert _count(reopened, "telemetry") == 500
    reopened.close()


def test_writer_survives_a_bad_row_and_appends_after_close_raise():
    store = SessionStore()
    sid = store.start_session("s")
    store.append_telemetry("no-such-session", 0, 0, {"x": 0})   # FK violation
    store.append_telemetry(sid, 0, 1, {"x": 1})
    assert store.flush()
    assert store.write_errors == 1
    assert "telemetry" in store.last_write_error
    assert [r["sequence"] for r in store.iter_records(sid)] == [1]
    store.close()
    try:
        store.append_telemetry(sid, 0, 2, {"x": 2})
    except Exception as exc:                    # sqlite3.ProgrammingError
        assert "closed" in str(exc)
    else:
        raise AssertionError("append after close() must raise")


def test_a_full_queue_waits_for_the_writer_instead_of_growing(monkeypatch):
    import ground_station.service.storage as storage
    monkeypatch.setattr(storage, "MAX_QUEUED_ROWS", 10)
    store = SessionStore()
    sid = store.start_session("s")
    store._lock.acquire()                        # stall the writer
    try:
        for seq in range(10):
            store.append_telemetry(sid, 0, seq, {"x": seq})
        extra = threading.Thread(
            target=store.append_telemetry, args=(sid, 0, 10, {"x": 10}))
        extra.start()
        extra.join(0.2)
        assert extra.is_alive()                  # the 11th row waits
        assert store._pending == 10
    finally:
        store._lock.release()
    extra.join(2.0)
    assert not extra.is_alive()
    assert [r["sequence"] for r in store.iter_records(sid)] == list(range(11))
