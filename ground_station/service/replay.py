"""Deterministic replay over :class:`SessionStore` records."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator

from .storage import SessionStore


@dataclass(frozen=True)
class ReplayRecord:
    time_ns: int
    type: str
    data: dict


class SessionReplay:
    def __init__(self, store: SessionStore, session_id: str) -> None:
        self.store, self.session_id = store, session_id

    def records(self) -> Iterator[ReplayRecord]:
        for row in self.store.iter_records(self.session_id):
            kind = row["type"]
            data = (row["payload"] if kind == "event" else
                    {"stream_id": row["stream_id"], "sequence": row["sequence"],
                     "source_time_ms": row["source_time_ms"], "values": row["values"]})
            yield ReplayRecord(int(row["time_ns"]), kind, data)

    def play(self, callback: Callable[[ReplayRecord], None], *, realtime: bool = False) -> None:
        previous = None
        for record in self.records():
            if realtime and previous is not None:
                import time
                time.sleep(max(0.0, (record.time_ns - previous) / 1e9))
            callback(record)
            previous = record.time_ns
