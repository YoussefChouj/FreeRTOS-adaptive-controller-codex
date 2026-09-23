"""Run record dataclass.

One schema for ``kind in {experiment, debug, validation, flight}``.  Fields:
intent, hypothesis, phase, firmware_hash, git_commit, full parameter snapshot,
controller variant, trajectory, captures, Simplex events, notes (operator and
agent), outcome, metrics, tags, created_at.

All fields that carry structured data use JSON-serialisable types so a single
``json.dumps / json.loads`` round-trips cleanly (the dataclass is frozen so
``asdict`` is safe).
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ulid() -> str:
    """Generate a sort-friendly ID from the current timestamp + uuid4."""
    ts = int(time.time() * 1000)  # milliseconds
    return f"{ts:013x}{uuid.uuid4().hex[:16]}"


# ---------------------------------------------------------------------------
# Event / note schemas
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Event:
    """A single event inside a Run (e.g. a Simplex trip)."""
    t: float  # seconds from run start
    kind: str  # e.g. "simplex_trip", "start", "stop"
    detail: str = ""


@dataclass(frozen=True)
class Note:
    """Operator or agent note attached to a Run."""
    t: float  # seconds from run start
    author: str  # "operator" or "agent"
    text: str


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

Kind = str  # experiment | debug | validation | flight


@dataclass(frozen=True)
class Run:
    """A single research run (armed flight, debug session, or feature check)."""
    id: str = field(default_factory=_ulid)
    kind: Kind = "experiment"
    intent: str = ""
    hypothesis: str = ""
    phase: str = ""
    firmware_hash: str = ""
    git_commit: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    variant: str = ""
    trajectory: str = ""
    captures: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, str]] = field(default_factory=list)
    outcome: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            object.__setattr__(self, "created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    # -- construction helpers ------------------------------------------------

    @staticmethod
    def from_event(evt: Event) -> dict[str, Any]:
        return {"t": evt.t, "kind": evt.kind, "detail": evt.detail}

    @staticmethod
    def from_note(note: Note) -> dict[str, str]:
        return {"t": str(note.t), "author": note.author, "text": note.text}

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, raw: str) -> Run:
        data = json.loads(raw)
        return cls(**data)

    def save_json(self, path: Any) -> None:
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load_json(cls, path: Any) -> Run:
        return cls.from_json(path.read_text(encoding="utf-8"))
