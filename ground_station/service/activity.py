"""Always-on daily-rotated activity journal for the agent UI.

Unlike the session recorder's ``events.jsonl`` (which only flushes while
telemetry recording ``REC`` is on), this journal is ALWAYS on: every agent,
operator, plan, approval, control and UI event is appended here regardless of
recording state. It rotates once per UTC day to
``logs/activity/<YYYY-MM-DD>.jsonl`` and survives service restarts — the
monotonic ``seq`` is resumed from the largest seq already on disk so history
queries are stable across crashes.

Each line is one JSON object::

    {"seq": 1, "t": 123456.78, "iso": "2026-09-22T03:00:00.000+00:00",
     "kind": "control", "source": "agent", "actor": "operator", "data": {...}}

``seq`` is global across the whole day file tree (never reset per file), which
lets a client page through history with ``since=<seq>`` and never miss an
entry even across a day boundary.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

ACTIVITY_RING_MAX = 2000
ACTIVITY_FIELD_ORDER = ("seq", "t", "iso", "kind", "source", "actor", "data")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="milliseconds")


def _day_str(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


class ActivityJournal:
    """Thread-safe always-on activity journal (in-memory ring + JSONL files)."""

    def __init__(self, root: str | Path, ring_max: int = ACTIVITY_RING_MAX) -> None:
        self._lock = threading.RLock()
        self._root = Path(root)
        self._ring: deque[dict[str, Any]] = deque(maxlen=int(ring_max))
        self._fh = None  # open append handle for the current day
        self._fh_day: str | None = None
        self.pending_write_fail: str | None = None
        self._seq = self._load_seq()

    # -- file I/O -----------------------------------------------------------
    @property
    def day_file(self) -> Path:
        return self._root / f"{_day_str(time.time())}.jsonl"

    def _load_seq(self) -> int:
        """Resume ``seq`` from the largest seq already persisted on disk."""
        seq = 0
        try:
            if not self._root.is_dir():
                return 0
            for jsonl in sorted(self._root.glob("*.jsonl")):
                try:
                    with jsonl.open("r", encoding="utf-8") as fh:
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                entry = json.loads(line)
                            except Exception:
                                continue
                            s = entry.get("seq")
                            if isinstance(s, int) and s > seq:
                                seq = s
                except Exception:
                    continue
        except Exception:
            pass
        return seq

    def _day_handle(self):
        path = self.day_file
        day = path.stem
        if self._fh is None or self._fh_day != day:
            if self._fh is not None:
                try:
                    self._fh.close()
                except Exception:
                    pass
            self._root.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("a", encoding="utf-8")
            self._fh_day = day
        return self._fh

    # -- write --------------------------------------------------------------
    def record(self, kind: str, source: str = "system",
               actor: str = "service", data: dict | None = None
               ) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            now = time.time()
            entry = {
                "seq": self._seq,
                "t": now,
                "iso": _iso(now),
                "kind": str(kind),
                "source": str(source),
                "actor": str(actor),
                "data": dict(data or {}),
            }
            try:
                fh = self._day_handle()
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                fh.flush()
                self.pending_write_fail = None
            except Exception as exc:  # never let logging break a control path
                self.pending_write_fail = f"{exc.__class__.__name__}: {exc}"
            self._ring.append(entry)
            return entry

    # -- read ---------------------------------------------------------------
    def history(self, since: int = 0, limit: int = 100,
                kind: str | None = None, source: str | None = None
                ) -> list[dict[str, Any]]:
        """Return journal entries with ``seq > since`` matching optional filters.

        Merges the on-disk day file (past entries) with the in-memory ring
        (recent entries still buffered), dedupes by ``seq`` and returns the
        newest ``limit`` in ascending ``seq`` order, oldest first (the wire
        order a timeline appends naturally).
        """
        with self._lock:
            merged: dict[int, dict[str, Any]] = {}
            try:
                path = self.day_file
                if path.is_file():
                    with path.open("r", encoding="utf-8") as fh:
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                e = json.loads(line)
                            except Exception:
                                continue
                            seq = e.get("seq")
                            if isinstance(seq, int) and seq > since:
                                merged[seq] = e
            except Exception:
                pass
            for e in self._ring:
                seq = e.get("seq")
                if isinstance(seq, int) and seq > since:
                    merged[seq] = e
            rows = [merged[s] for s in sorted(merged)]

            def _match(e: dict) -> bool:
                if kind is not None and e.get("kind") != kind:
                    return False
                if source is not None and e.get("source") != source:
                    return False
                return True

            rows = [e for e in rows if _match(e)]
            limit = int(limit)
            if limit and limit > 0:
                rows = rows[-limit:]
            return rows

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None
                self._fh_day = None