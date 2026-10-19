"""Histogram of ingest_lag_ns: receive stamp to ingest done, for every telemetry frame.

The bridge's ``ingest_lag_ns`` keeps only the last frame, and the ingest-behind print only shows a 1 s maximum, so
nothing could say how often a frame waits, or for how long, over a flight. This keeps two bounded things:

- cumulative counts per fixed bucket (an edge sits exactly at 200 ms, so "any frame over 200 ms" is an exact count);
- the exact maximum of each wall-clock second, for the last ``SECONDS_KEPT`` seconds, so a reader can take the
  exact maximum over any window inside that span (a flight).

A reader takes two snapshots and subtracts them (see ``window``): there is no reset, so several readers never
disturb each other. Memory is fixed: one counter per bucket plus a ring of ``SECONDS_KEPT`` pairs.
"""
from __future__ import annotations

import threading
from bisect import bisect_left
from collections import deque

# Upper bucket edges in ms; a frame with lag L falls in the first bucket whose edge is >= L, or the last (inf) one.
EDGES_MS = (0.25, 0.5, 1, 2, 3, 5, 7.5, 10, 15, 20, 30, 50, 75, 100, 150, 200, 300, 500, 1000, 2000, 5000)
_EDGES_NS = tuple(int(e * 1_000_000) for e in EDGES_MS)
LIMIT_MS = 200          # the soak's bar: a frame older than this at ingest done is a block
SECONDS_KEPT = 3600     # one hour of per-second maxima, about 0.3 MB


class LagHistogram:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts = [0] * (len(_EDGES_NS) + 1)
        self._max_ns = 0
        self._seconds: deque[list[int]] = deque(maxlen=SECONDS_KEPT)   # [unix second, max lag ns in it]

    def add(self, lag_ns: int, now_ns: int) -> None:
        """One frame; ``now_ns`` is time.time_ns() at ingest done (the second it is filed under)."""
        i = bisect_left(_EDGES_NS, lag_ns)
        sec = now_ns // 1_000_000_000
        with self._lock:
            self._counts[i] += 1
            if lag_ns > self._max_ns:
                self._max_ns = lag_ns
            last = self._seconds[-1] if self._seconds else None
            if last is not None and last[0] == sec:
                if lag_ns > last[1]:
                    last[1] = lag_ns
            else:
                self._seconds.append([sec, lag_ns])

    def snapshot(self, since_s: float | None = None) -> dict:
        """Cumulative counts and maximum; with ``since_s`` also the per-second maxima from that unix second on."""
        with self._lock:
            out = {"edges_ms": list(EDGES_MS), "counts": list(self._counts), "total": sum(self._counts),
                   "max_ms": round(self._max_ns / 1e6, 3)}
            if since_s is not None:
                out["seconds"] = [[s, round(m / 1e6, 3)] for s, m in self._seconds if s >= int(since_s)]
        out["over_limit"] = over_limit(out["counts"])
        return out


def over_limit(counts: list[int], limit_ms: float = LIMIT_MS) -> int:
    """Frames in the buckets above ``limit_ms`` (exact when the limit is one of EDGES_MS)."""
    i = EDGES_MS.index(limit_ms)
    return sum(counts[i + 1:])


def percentile_ms(counts: list[int], q: float) -> float | None:
    """Upper edge of the bucket holding the q-th fraction of the frames: the true value is at most this.
    Frames in the open last bucket report inf."""
    total = sum(counts)
    if not total:
        return None
    rank = q * total
    seen = 0
    for i, c in enumerate(counts):
        seen += c
        if seen >= rank and c:
            return float(EDGES_MS[i]) if i < len(EDGES_MS) else float("inf")
    return float("inf")


def window(before: dict, after: dict, start_s: float, end_s: float) -> dict:
    """Frames between two snapshots: counts by subtraction, maximum from the per-second maxima of ``after``
    within [start_s, end_s]. p50/p99 are bucket upper edges; max is exact."""
    counts = [a - b for a, b in zip(after["counts"], before["counts"])]
    secs = [m for s, m in after.get("seconds", []) if int(start_s) <= s <= int(end_s)]
    return {"frames": sum(counts), "counts": counts, "p50_ms": percentile_ms(counts, 0.50),
            "p99_ms": percentile_ms(counts, 0.99), "max_ms": max(secs) if secs else None,
            "over_200ms": over_limit(counts)}
