"""Durable session, event, telemetry, and raw-frame storage.

SQLite is deliberately used here instead of a dataframe format: it is local,
transactional, replayable, and available in the Python standard library.
"""
from __future__ import annotations

import csv
import json
import queue
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


class SessionStore:
    """Thread-safe SQLite store for one or more ground-station sessions."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.RLock()
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        # WAL + NORMAL: no fsync per commit; live telemetry commits ~100/s.
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, started_ns INTEGER NOT NULL,
                ended_ns INTEGER, schema_id TEXT NOT NULL, source TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                time_ns INTEGER NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                time_ns INTEGER NOT NULL, stream_id INTEGER NOT NULL,
                sequence INTEGER NOT NULL, source_time_ms INTEGER,
                values_json TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );
            CREATE TABLE IF NOT EXISTS raw_frames (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                time_ns INTEGER NOT NULL, direction TEXT NOT NULL, data BLOB NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );
            CREATE INDEX IF NOT EXISTS telemetry_session_time
                ON telemetry(session_id, time_ns, id);
            CREATE INDEX IF NOT EXISTS events_session_time
                ON events(session_id, time_ns, id);
            """
        )
        self._db.commit()

    def start_session(self, schema_id: str, source: str = "wifi",
                      metadata: dict[str, Any] | None = None) -> str:
        with self._lock:
            sid = uuid.uuid4().hex
            self._db.execute(
                "INSERT INTO sessions VALUES (?, ?, NULL, ?, ?, ?)",
                (sid, time.time_ns(), schema_id, source,
                 json.dumps(metadata or {}, sort_keys=True)),
            )
            self._db.commit()
            return sid

    def end_session(self, session_id: str) -> None:
        with self._lock:
            self._db.execute("UPDATE sessions SET ended_ns=? WHERE id=?",
                             (time.time_ns(), session_id))
            self._db.commit()

    def append_event(self, session_id: str, kind: str, payload: dict[str, Any],
                     time_ns: int | None = None) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events(session_id,time_ns,kind,payload_json) VALUES(?,?,?,?)",
                (session_id, time_ns or time.time_ns(), kind,
                 json.dumps(payload, sort_keys=True, separators=(",", ":"))),
            )
            self._db.commit()
            return int(cur.lastrowid)

    def append_telemetry(self, session_id: str, stream_id: int, sequence: int,
                         values: dict[str, Any], source_time_ms: int | None = None,
                         time_ns: int | None = None) -> int:
        with self._lock:
            cur = self._db.execute(
                """INSERT INTO telemetry(session_id,time_ns,stream_id,sequence,
                   source_time_ms,values_json) VALUES(?,?,?,?,?,?)""",
                (session_id, time_ns or time.time_ns(), stream_id, sequence,
                 source_time_ms, json.dumps(values, sort_keys=True,
                                            separators=(",", ":"))),
            )
            self._db.commit()
            return int(cur.lastrowid)

    def append_raw_frame(self, session_id: str, direction: str, data: bytes,
                         time_ns: int | None = None) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO raw_frames(session_id,time_ns,direction,data) VALUES(?,?,?,?)",
                (session_id, time_ns or time.time_ns(), direction, sqlite3.Binary(data)),
            )
            self._db.commit()
            return int(cur.lastrowid)

    def session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise KeyError(session_id)
            out = dict(row)
            out["metadata"] = json.loads(out.pop("metadata_json"))
            return out

    def iter_records(self, session_id: str, limit: int | None = None,
                     offset: int = 0) -> Iterator[dict[str, Any]]:
        """Yield telemetry and events in deterministic timestamp/id order.

        ``limit`` caps the row count and is pushed into SQL, so a long
        session is never materialised in full; ``None`` means every row.
        """
        sql = """SELECT time_ns,id,'event' AS type,kind,payload_json,NULL AS stream_id,
                      NULL AS sequence,NULL AS source_time_ms,NULL AS values_json
                 FROM events WHERE session_id=?
               UNION ALL
               SELECT time_ns,id,'telemetry',NULL,NULL,stream_id,sequence,
                      source_time_ms,values_json FROM telemetry WHERE session_id=?
               ORDER BY time_ns,id"""
        params: list[Any] = [session_id, session_id]
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset)])
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            params.append(int(offset))
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        for row in rows:
            item = dict(row)
            if item["type"] == "event":
                item["payload"] = json.loads(item.pop("payload_json"))
            else:
                item["values"] = json.loads(item.pop("values_json"))
            yield item

    def close(self) -> None:
        with self._lock:
            self._db.close()


class CsvRecorder:
    """Background per-session telemetry recorder writing long-format CSV.

    Long format — one row per ``(received_ns, slot, key, value)`` — is
    robust to the per-slot key set changing between frames: a new channel
    needs no schema change and old keys simply stop appearing.

    Rows are pushed onto a thread-safe queue by callers and written to disk
    from a single daemon thread, which flushes the buffer at most every
    ``flush_interval_s`` (default 1 s). Callers never block on disk I/O and
    ``note()`` never raises into the ingest path — a failed put increments
    ``errors`` and is dropped. Mirrors ``SessionStore``'s deploy/disable
    contract: enabled by default, disabled via the ``GS_RECORD=0`` env var
    (see ``GroundStationService.__init__``).

    Status lives on the instance (``path``, ``rows``, ``errors``) and is
    surfaced by ``GET /health`` under a small ``recorder`` block.
    """

    _STOP = object()

    def __init__(self, root: str | Path = "logs/sessions", *,
                 enabled: bool = True, flush_interval_s: float = 1.0) -> None:
        self.enabled = bool(enabled)
        self.root = Path(root)
        self.flush_interval_s = float(flush_interval_s)
        # Public status (read by /health; written by the background thread).
        self.session_dir: Path | None = None
        self.path: Path | None = None
        self.started = False
        self.rows = 0
        self.errors = 0
        self._q: queue.Queue[tuple[Any, int, dict[str, Any]] | object] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False

    def start(self) -> None:
        """Create the per-run session dir and spawn the writer thread."""
        if not self.enabled or self._closed:
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.session_dir = self.root / stamp
        self.path = self.session_dir / "telemetry.csv"
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.errors += 1
            self.path = None
            return
        self.started = True
        self._thread = threading.Thread(target=self._run, name="gs-csv-recorder",
                                        daemon=True)
        self._thread.start()

    def note(self, slot: Any, values: dict[str, Any], received_ns: int) -> None:
        """Enqueue one sample's key/value pairs (never blocks, never raises)."""
        if not self.started or self._closed:
            return
        try:
            self._q.put((slot, received_ns, values))
        except Exception:
            with self._lock:
                self.errors += 1

    def _write_rows(self, writer, f, pending: list[tuple[Any, int, dict[str, Any]]]) -> None:
        header = ("received_ns", "slot", "key", "value")
        with self._lock:
            start = self.rows
            rows_written = start
            for slot, received_ns, values in pending:
                if rows_written == 0:
                    writer.writerow(header)
                for key, value in values.items():
                    writer.writerow((received_ns, slot, key, value))
                    rows_written += 1
            added = rows_written - start
            if added:
                self.rows = rows_written
        # Called only from the writer thread; flush the file after each batch
        # so the CSV stays readable if the process is killed between flushes.
        f.flush()

    def _run(self) -> None:
        try:
            f = open(self.path, "w", newline="", encoding="utf-8")
        except OSError:
            with self._lock:
                self.errors += 1
            self.started = False
            return
        writer = csv.writer(f)
        pending: list[tuple[Any, int, dict[str, Any]]] = []
        last_flush = time.monotonic()
        try:
            while True:
                try:
                    item = self._q.get(timeout=0.25)
                except queue.Empty:
                    item = None
                if item is self._STOP:
                    # Drain whatever arrived after the stop sentinel, then
                    # flush everything and exit (stop() joins us first).
                    try:
                        while True:
                            pending.append(self._q.get_nowait())
                    except queue.Empty:
                        pass
                    self._write_rows(writer, f, pending)
                    break
                if item is not None:
                    pending.append(item)
                if pending and time.monotonic() - last_flush >= self.flush_interval_s:
                    self._write_rows(writer, f, pending)
                    pending = []
                    last_flush = time.monotonic()
        finally:
            try:
                f.close()
            except OSError:
                pass

    def stop(self) -> None:
        """Flush remaining rows and stop the writer thread."""
        if not self.started or self._closed:
            return
        self._closed = True
        self._q.put(self._STOP)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
