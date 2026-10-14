import json

import pytest

from ground_station.lab import events


def test_log_and_timed_write_one_line_each(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LAB_EVENTS_DIR", str(tmp_path))
    events.log("preflight", ok=False, why="slot 1 slow")
    with events.timed("debrief", card="c"):
        pass
    with pytest.raises(ValueError):
        with events.timed("lab_up"):
            raise ValueError("boom")
    lines = [json.loads(s) for f in tmp_path.glob("*.jsonl") for s in f.read_text().splitlines()]
    assert [r["event"] for r in lines] == ["preflight", "debrief", "lab_up"]
    assert lines[1]["card"] == "c" and lines[1]["ms"] >= 0 and "boom" in lines[2]["error"]
    events.main([])
    assert "debrief" in capsys.readouterr().out
    with next(tmp_path.glob("*.jsonl")).open("a", encoding="utf-8") as f:
        f.write('{"t": "2026-10-14T23:1\n')   # torn by a crash mid-write
    events.main([])
    assert "unreadable line" in capsys.readouterr().out


def test_log_never_raises_when_it_cannot_write(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("LAB_EVENTS_DIR", str(blocker / "sub"))    # a file where the folder should be
    events.log("x")
