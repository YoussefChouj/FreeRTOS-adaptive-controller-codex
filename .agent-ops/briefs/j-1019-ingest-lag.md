# Fix 10-19 host SQLite ingest_lag_s spikes in TelemetryStore

Read AGENTS.md first. Touch ONLY `ground_station/service/storage.py` and `ground_station/service/tests/test_storage_ingest_lag.py` (new test file). Do not touch `USER/`, `OBJ/`, or `logs/`.

## Context & Root Cause (2026-10-19 Lab)
During `z1` (`07_lab-z-ground-effect`, at 71.0 s) and `mem1`, the safety monitor aborted with `L1 stale_telemetry` while the MicoAir WiFi link was completely healthy (`rx_age_s = 0.016 s`, `wifi_signal = 100%`). The abort event logged:
`ingest_lag_s: 0.518` (and up to `1.344 s` in `1017-E1`, `ingest_queue: 132`).
Root cause: `wifi_bridge._ingest_loop` calls `core.ingest_decoded` synchronously on the real-time ingest thread. `ingest_decoded` calls `TelemetryStore.append_telemetry` (`ground_station/service/storage.py:L124-L171`), which acquires `self._lock` and runs SQLite `executemany` + `self._conn.commit()` + `_prune()` (`DELETE FROM ...`) directly on the ingest thread. Whenever Windows/OS flushes the SQLite journal or an HTTP reader calls `iter_records()` under `self._lock`, the ingest thread blocks for 0.5–1.34 s, starving `latest_values` updates and tripping `stale_telemetry`.

## Required Changes (`ground_station/service/storage.py`)
1. **SQLite WAL & synchronous mode in `_init_db`:**
   - Execute `PRAGMA journal_mode=WAL;` and `PRAGMA synchronous=NORMAL;` during `_init_db()` so SQLite commits do not block on full disk barriers.
2. **Non-blocking `append_telemetry` via background writer queue/thread (or decoupled lock):**
   - Offload the SQLite `INSERT INTO telemetry_records` + `commit()` + `_prune()` work for `append_telemetry` onto a dedicated background daemon thread reading from a `queue.SimpleQueue` (or `queue.Queue`), so `append_telemetry()` called from the real-time ingest thread only enqueues the batch (taking `< 1 ms`, never waiting on SQLite I/O or `self._lock`).
   - Add a `flush(self, timeout: float = 2.0) -> None` method on `TelemetryStore` that drains/waits for any queued telemetry batches to be committed to SQLite, and call `self.flush()` at the start of `iter_records()`, `latest()`, and `close()` so all synchronous callers and tests always observe every appended record immediately.
   - Ensure `close()` stops the background writer thread cleanly and flushes remaining items before closing `self._conn`.
3. **Regression Tests (`ground_station/service/tests/test_storage_ingest_lag.py`):**
   - Test that `append_telemetry` returns in `< 5 ms` per call even across `> 600` batches (triggering `_prune`) and while `iter_records()` is concurrently reading.
   - Test that `iter_records("telemetry")` and `latest("telemetry")` immediately return all records appended just before the call (via automatic `flush()`).
   - Test that `close()` flushes pending records and shuts down the writer thread cleanly.

## Verification
Run and report in the PR:
- `python -m pytest ground_station/service/tests/test_storage_ingest_lag.py ground_station/service/tests/test_service_api.py -q`
- `bash tools/check.sh doc-paths`
