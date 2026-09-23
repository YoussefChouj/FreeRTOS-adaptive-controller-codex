"""Tests for ground_station.research.run."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ground_station.research.run import Run, Event, Note


class TestJSONRoundTrip:
    """JSON serialisation must round-trip with the same fields and values."""

    def _make(self) -> Run:
        return Run(
            kind="experiment",
            intent="test thrust",
            hypothesis="MRAC improves tracking",
            phase="alpha",
            firmware_hash="abc123",
            git_commit="deadbeef",
            params={"Kp": 1.0, "Ki": 0.5},
            variant="mrac_v2",
            trajectory="hover",
            captures=["logs/test.csv"],
            events=[Event(t=1.0, kind="simplex_trip", detail="gain bound").__dict__],
            notes=[Note(t=5.0, author="operator", text="all good").__dict__],
            outcome="pass",
            metrics={"rmse": 0.123},
            tags=["test", "bench"],
        )

    def test_round_trip(self, tmp_path: Path) -> None:
        run = self._make()
        assert run.kind == "experiment"
        assert run.metrics == {"rmse": 0.123}

        path = tmp_path / "run.json"
        run.save_json(path)
        loaded = Run.load_json(path)

        assert loaded.id == run.id
        assert loaded.kind == run.kind
        assert loaded.intent == run.intent
        assert loaded.hypothesis == run.hypothesis
        assert loaded.phase == run.phase
        assert loaded.firmware_hash == run.firmware_hash
        assert loaded.git_commit == run.git_commit
        assert loaded.params == run.params
        assert loaded.variant == run.variant
        assert loaded.trajectory == run.trajectory
        assert loaded.captures == run.captures
        assert len(loaded.events) == len(run.events)
        assert loaded.events[0]["t"] == 1.0
        assert loaded.events[0]["kind"] == "simplex_trip"
        assert loaded.notes[0]["author"] == "operator"
        assert loaded.outcome == run.outcome
        assert loaded.metrics == run.metrics
        assert loaded.tags == run.tags

    def test_to_from_json_string(self) -> None:
        run = self._make()
        raw = run.to_json()
        loaded = Run.from_json(raw)
        assert loaded.kind == run.kind
        assert loaded.params == run.params

    def test_created_at_is_set(self) -> None:
        run = Run()
        assert run.created_at != ""
        assert "T" in run.created_at

    def test_id_is_non_empty(self) -> None:
        run = Run()
        assert run.id != ""
        assert len(run.id) > 10  # timestamp hex + uuid hex


class TestEventNoteHelpers:
    def test_event_to_dict(self) -> None:
        evt = Event(t=42.0, kind="simplex_trip", detail="test")
        d = Run.from_event(evt)
        assert d["t"] == 42.0
        assert d["kind"] == "simplex_trip"
        assert d["detail"] == "test"

    def test_note_to_dict(self) -> None:
        note = Note(t=10.0, author="agent", text="checkpoint")
        d = Run.from_note(note)
        assert d["author"] == "agent"
        assert d["text"] == "checkpoint"
