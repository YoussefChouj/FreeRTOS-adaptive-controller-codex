"""Durable session, event, telemetry, and raw-frame storage.

SQLite is deliberately used here instead of a dataframe format: it is local,
transactional, replayable, and available in the Python standard library.
"""
from __future__ import annotations

import csv
import json
import os
import queue
import re
import sqlite3
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# Maximum number of operator notes kept in memory while recording is STOPPED,
# so they can be flushed into events.jsonl at the next recording start.
NOTES_BUFFER_MAX = 50

# An in-memory SessionStore keeps only the newest rows per high-rate table.
# Unbounded, every live frame stayed in RAM: ~20 MB/min, 1.7 GB after 2 h.
# Durable recording is CsvRecorder's job; this store only backs paged reads.
MEMORY_MAX_ROWS = 10_000


class SessionStore:
    """Thread-safe SQLite store for one or more ground-station sessions."""

    def __init__(self, path: str | Path = ":memory:",
                 max_rows: int | None = None) -> None:
        self.path = str(path)
        # Rows kept per table (telemetry, raw_frames); None keeps everything.
        self.max_rows = max_rows if max_rows is not None else (
            MEMORY_MAX_ROWS if self.path == ":memory:" else None)
        self._prune_every = min(1000, self.max_rows) if self.max_rows else 0
        self._inserts = {"telemetry": 0, "raw_frames": 0}
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
            self._prune("telemetry", cur.lastrowid)
            self._db.commit()
            return int(cur.lastrowid)

    def append_raw_frame(self, session_id: str, direction: str, data: bytes,
                         time_ns: int | None = None) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO raw_frames(session_id,time_ns,direction,data) VALUES(?,?,?,?)",
                (session_id, time_ns or time.time_ns(), direction, sqlite3.Binary(data)),
            )
            self._prune("raw_frames", cur.lastrowid)
            self._db.commit()
            return int(cur.lastrowid)

    def _prune(self, table: str, last_id: int) -> None:
        """Drop rows older than the newest ``max_rows`` (caller holds the lock)."""
        if not self._prune_every:
            return
        self._inserts[table] += 1
        if self._inserts[table] % self._prune_every == 0:
            self._db.execute(f"DELETE FROM {table} WHERE id <= ?",
                             (last_id - self.max_rows,))

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
    """Opt-in per-session recorder writing telemetry CSV plus a manifest and
    an event log (events.jsonl) into one fresh directory per recording.

    Long format — one row per ``(received_ns, slot, key, value)`` — is
    robust to the per-slot key set changing between frames: a new channel
    needs no schema change and old keys simply stop appearing.

    Recording is OFF by default: ``start()`` must be called explicitly
    (from the dashboard or the HTTP API) before anything is written, and
    no session directory exists until then. ``enabled=False`` (``GS_RECORD=0``)
    forbids starting at all; ``GS_RECORD=1`` auto-starts at service boot.

    Rows are pushed onto a thread-safe queue by callers and written to disk
    from a single daemon thread, which flushes the buffer at most every
    ``flush_interval_s`` (default 1 s). ``note()`` never raises into the
    ingest path — a failed put increments ``errors`` and is dropped.

    Each directory also carries:

      * ``manifest.json``  — written at start, rewritten (with stopped_at,
        final row count and file list) at stop.
      * ``events.jsonl``   — rare session events (command lifecycle,
        arm-state changes, stream stalls, notes) as one JSON object per line.

    Status lives on the instance (``recording``, ``path``, ``rows``) and is
    surfaced by ``GET /health`` (``recorder`` block) and ``GET /api/recording``.
    """

    _STOP = object()

    def __init__(self, root: str | Path = "logs/sessions", *,
                 enabled: bool = True, flush_interval_s: float = 1.0) -> None:
        self.enabled = bool(enabled)
        self.root = Path(root)
        self.flush_interval_s = float(flush_interval_s)
        # Public status (read by /health and /api/recording; written by the
        # background thread for ``rows``).
        self.session_dir: Path | None = None
        self.path: Path | None = None
        self.recording = False
        self.rows = 0
        self.errors = 0
        self.started_at: float | None = None
        self.stopped_at: float | None = None
        self.requested_by: str = "operator"
        self.reason: str | None = None
        self.label: str | None = None
        # Subscribe layout captured at start (mirrors what the manifest records).
        self.subscribe_layout: dict[str, Any] = {}
        # Extra manifest context passed at start (service commit, firmware ELF).
        self.context: dict[str, Any] = {}
        # Operator notes buffered while recording is STOPPED (bounded), so they
        # are not lost and can be flushed into events.jsonl at the next start.
        self._buffered_notes: deque[dict[str, Any]] = deque(maxlen=NOTES_BUFFER_MAX)
        self._q: queue.Queue[tuple[Any, int, dict[str, Any]] | object] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._events_lock = threading.Lock()
        self._events_fh = None
        self._closed = False

    # -- recording lifecycle -------------------------------------------------

    def start(self, *, label: str | None = None,
              requested_by: str = "operator", reason: str | None = None,
              subscribe_layout: dict[str, Any] | None = None,
              context: dict[str, Any] | None = None) -> bool:
        """Begin a fresh recording: create a new directory and writer thread.

        Creates ``<root>/<YYYYmmdd-HHMMSS>[-label]/`` with ``telemetry.csv``,
        ``manifest.json`` (start fields) and ``events.jsonl`` (recording_start
        + any notes buffered while stopped). No-op when already recording.
        Returns True when a new recording actually began.
        """
        if not self.enabled or self._closed or self.recording:
            return False
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        # The label comes from HTTP; keep it a single safe path component.
        label = re.sub(r"[^A-Za-z0-9_-]+", "_", label or "").strip("_")[:40] or None
        if label:
            stamp = stamp + "-" + label
        self.session_dir = self.root / stamp
        self.path = self.session_dir / "telemetry.csv"
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            with self._lock:
                self.errors += 1
            self.path = None
            return False
        self.requested_by = requested_by or "operator"
        self.reason = reason
        self.label = label
        self.subscribe_layout = dict(subscribe_layout or {})
        self.context = dict(context or {})
        self.started_at = time.time()
        self.stopped_at = None
        self.rows = 0
        self._open_events()
        self.recording = True
        self._write_manifest()
        # Recording-start event, then any notes buffered while stopped.
        self.add_event("recording_start", {
            "requested_by": self.requested_by, "reason": self.reason,
            "label": self.label,
            "subscribe_layout": self.subscribe_layout,
        })
        buffered_to_flush = list(self._buffered_notes)
        self._buffered_notes.clear()
        for buffered in buffered_to_flush:
            self._write_event_line(buffered.get("kind") or "note", buffered)
        self._thread = threading.Thread(target=self._run, name="gs-csv-recorder",
                                        daemon=True)
        self._thread.start()
        return True

    def stop(self) -> bool:
        """Flush + join the writer thread, finalise the manifest, stop recording.

        Returns True when recording was active and has now stopped.
        """
        was_recording = self.recording
        if was_recording:
            self._q.put(self._STOP)
            if self._thread is not None:
                self._thread.join(timeout=5.0)
            self.add_event("recording_stop", {
                "requested_by": self.requested_by, "reason": self.reason,
                "rows": self.rows,
            })
            self.stopped_at = time.time()
            self.recording = False
            self._write_manifest()
            self._close_events()
        return was_recording

    def start_telemetry_csv(self) -> bool:
        """Back-compat alias that begins a bare recording with no metadata."""
        return self.start()

    # -- ingest paths ---------------------------------------------------------

    def note(self, slot: Any, values: dict[str, Any], received_ns: int) -> None:
        """Enqueue one sample's key/value pairs (never blocks, never raises)."""
        if not self.recording or self._closed:
            return
        try:
            self._q.put((slot, received_ns, values))
        except Exception:
            with self._lock:
                self.errors += 1

    def add_event(self, kind: str, data: dict[str, Any], *,
                  source: str = "service") -> None:
        """Append a rare session event to events.jsonl (no-op when stopped)."""
        if not self.recording or self._closed:
            return
        self._write_event_line(kind, data, source=source)

    def add_note(self, text: str, kind: str = "note", *,
                 source: str | None = None) -> int:
        """Record an operator note. Never raises.

        Buffers in memory when recording is STOPPED (bounded to the last 50);
        immediately writes an event when recording. Return value is the count
        of buffered notes currently held (0 when written straight through).
        """
        entry = {"text": text, "kind": kind, "source": source or "operator"}
        if not self.recording:
            self._buffered_notes.append(entry)
            return len(self._buffered_notes)
        self._write_event_line(kind, entry, source=source or "operator")
        return 0

    def buffered_notes(self) -> list[dict[str, Any]]:
        return list(self._buffered_notes)

    # -- internals ------------------------------------------------------------

    def _open_events(self) -> None:
        try:
            self._events_fh = open(self.events_path, "a", encoding="utf-8")
        except OSError:
            with self._lock:
                self.errors += 1
            self._events_fh = None

    def _close_events(self) -> None:
        fh = self._events_fh
        self._events_fh = None
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass

    @property
    def events_path(self) -> Path:
        return self.session_dir / "events.jsonl"

    @property
    def manifest_path(self) -> Path:
        return self.session_dir / "manifest.json"

    @property
    def bytes_written(self) -> int:
        """Total bytes on disk for this recording (CSV + events + manifest)."""
        total = 0
        if self.session_dir is not None and self.session_dir.exists():
            try:
                for f in self.session_dir.iterdir():
                    if f.is_file():
                        total += os.path.getsize(f)
            except OSError:
                pass
        return total

    @property
    def files(self) -> list[str]:
        if self.session_dir is None or not self.session_dir.exists():
            return []
        try:
            return sorted(f.name for f in self.session_dir.iterdir() if f.is_file())
        except OSError:
            return []

    def _write_event_line(self, kind: str, data: dict[str, Any], *,
                          source: str = "service", ts: float | None = None) -> None:
        t = ts if ts is not None else time.time()
        line = {
            "t": round(t, 3),
            "iso": datetime.fromtimestamp(t, tz=timezone.utc).isoformat(),
            "kind": kind,
            "source": source,
            "data": data,
        }
        with self._events_lock:
            if self._events_fh is None:
                return
            try:
                self._events_fh.write(
                    json.dumps(line, separators=(",", ":")) + "\n")
                self._events_fh.flush()
            except OSError:
                with self._lock:
                    self.errors += 1

    def _write_manifest(self) -> None:
        manifest = {
            "schema_version": 1,
            "started_at": (datetime.fromtimestamp(
                self.started_at or time.time(), tz=timezone.utc).isoformat()
                if self.started_at else None),
            "started_at_epoch": self.started_at,
            "stopped_at": (datetime.fromtimestamp(
                self.stopped_at, tz=timezone.utc).isoformat()
                if self.stopped_at else None),
            "stopped_at_epoch": self.stopped_at,
            "requested_by": self.requested_by,
            "reason": self.reason,
            "label": self.label,
            "subscribe_layout": self.subscribe_layout,
            "context": self.context,
            "rows": self.rows,
            "files": self.files,
            "errors": self.errors,
        }
        try:
            (self.session_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        except OSError:
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
            self.recording = False
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
                elif item is not None:
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