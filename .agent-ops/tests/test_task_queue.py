"""Tests for the queue runner.

Every one of these drives the real `run`/`run_one` loop. Only `Ops` is faked,
which is the point of putting the seam there: the spawn/wait/verify/review
sequencing, the stop-on-failure rule, the persistence and the friction records
are all real code here, exercised without a worker or a quota.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import task_queue as q  # noqa: E402


class FakeOps:
    """Scripted Ops. Each verdict in `verdicts` answers one task, in order."""

    def __init__(self, verdicts, spawn_ids=None, review="VERDICT: benign"):
        self.verdicts = list(verdicts)
        self.spawn_ids = list(spawn_ids) if spawn_ids else None
        self.review_text = review
        self.calls = []
        self._n = 0

    def spawn(self, entry):
        self.calls.append(("spawn", entry.file, entry.worker))
        if self.spawn_ids is not None:
            return self.spawn_ids.pop(0)
        self._n += 1
        return "20260923-00000%d" % self._n

    def wait(self, task_id, timeout_min):
        self.calls.append(("wait", task_id))
        return "OUTCOME: success (rc=0)"

    def verify(self, task_id):
        self.calls.append(("verify", task_id))
        verdict = self.verdicts.pop(0)
        return verdict, "VERDICT: %s\nWARN: something odd\nFAIL: broke\n" % verdict

    def review(self, prompt, model="qwen"):
        self.calls.append(("review", model))
        self.last_prompt = prompt
        return self.review_text


@pytest.fixture
def qf(tmp_path, monkeypatch):
    """An empty queue and a friction log, both in tmp."""
    monkeypatch.setattr(q, "FRICTIONS", str(tmp_path / "frictions.jsonl"))
    return q.Queue(str(tmp_path / "queue.json"))


def _add(queue, name="task.md", **kw):
    e = q.Entry(file=name, **kw)
    queue.add(e)
    return e


def frictions(tmp_path):
    p = tmp_path / "frictions.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


# ── the loop ─────────────────────────────────────────────────────────

def test_a_clean_task_runs_the_full_sequence(qf, tmp_path):
    _add(qf)
    ops = FakeOps(["PASS"])
    done = q.run(qf, ops, out=lambda *a: None)
    assert [c[0] for c in ops.calls] == ["spawn", "wait", "verify"]
    assert done[0].state == q.DONE
    assert frictions(tmp_path) == [], "a PASS must not log friction"


def test_a_failing_task_stops_the_queue(qf, tmp_path):
    """Queued tasks build on each other; carrying on past an unconfirmed
    result is how a bad edit ends up underneath four more."""
    _add(qf, "a.md")
    _add(qf, "b.md")
    ops = FakeOps(["FAIL", "PASS"])
    done = q.run(qf, ops, out=lambda *a: None)
    assert len(done) == 1
    assert [c[1] for c in ops.calls if c[0] == "spawn"] == ["a.md"]
    assert qf.entries[1].state == q.PENDING, "b must be left for later"


def test_keep_going_does_not_stop(qf):
    _add(qf, "a.md")
    _add(qf, "b.md")
    ops = FakeOps(["FAIL", "PASS"])
    done = q.run(qf, ops, keep_going=True, out=lambda *a: None)
    assert len(done) == 2


def test_max_caps_the_run(qf):
    for n in "abc":
        _add(qf, n + ".md")
    ops = FakeOps(["PASS", "PASS", "PASS"])
    assert len(q.run(qf, ops, max_tasks=2, out=lambda *a: None)) == 2
    assert qf.entries[2].state == q.PENDING


def test_a_spawn_that_never_started_is_a_failure_not_a_hang(qf, tmp_path):
    """A worker that dies on quota returns no task id. Waiting on it would
    block for the full timeout on a task that does not exist."""
    _add(qf)
    ops = FakeOps(["PASS"], spawn_ids=[None])
    done = q.run(qf, ops, out=lambda *a: None)
    assert done[0].verdict == "FAIL"
    assert [c[0] for c in ops.calls] == ["spawn"], "must not wait or verify"
    assert len(frictions(tmp_path)) == 1


# ── WARN goes to a reviewer ──────────────────────────────────────────

def test_warn_is_reviewed_and_the_verdict_is_kept(qf, tmp_path):
    _add(qf)
    ops = FakeOps(["WARN"], review="VERDICT: likely-bug\nWHY: the drain")
    done = q.run(qf, ops, out=lambda *a: None)
    assert ("review", "qwen") in ops.calls
    assert "likely-bug" in done[0].review
    assert frictions(tmp_path)[0]["review"] == done[0].review


def test_pass_and_fail_are_not_reviewed(qf):
    """Review is for the ambiguous verdict. A PASS needs no triage and a FAIL
    needs a human, not a second opinion."""
    _add(qf, "a.md")
    _add(qf, "b.md")
    ops = FakeOps(["PASS", "FAIL"])
    q.run(qf, ops, keep_going=True, out=lambda *a: None)
    assert not any(c[0] == "review" for c in ops.calls)


def test_the_reviewer_is_handed_the_findings(qf):
    _add(qf)
    ops = FakeOps(["WARN"])
    q.run(qf, ops, out=lambda *a: None)
    assert "something odd" in ops.last_prompt
    assert "VERDICT: benign | needs-review | likely-bug" in ops.last_prompt


# ── frictions ────────────────────────────────────────────────────────

def test_friction_is_logged_for_every_non_pass(qf, tmp_path):
    _add(qf, "a.md")
    _add(qf, "b.md")
    ops = FakeOps(["WARN", "FAIL"])
    q.run(qf, ops, keep_going=True, out=lambda *a: None)
    recs = frictions(tmp_path)
    assert len(recs) == 2
    assert [r["kind"] for r in recs] == ["knowledge", "tool"]
    assert [r["severity"] for r in recs] == ["medium", "high"]


def test_friction_carries_the_raw_finding_lines(qf, tmp_path):
    """Verbatim, not summarised: a summary written now is a guess."""
    _add(qf)
    q.run(qf, FakeOps(["FAIL"]), out=lambda *a: None)
    rec = frictions(tmp_path)[0]
    assert "FAIL: broke" in rec["findings"]
    assert "WARN: something odd" in rec["findings"]
    assert rec["source"] == "queue.py (auto)"
    assert rec["task"] == "20260923-000001"


def test_a_friction_log_that_cannot_be_written_does_not_fail_the_run(
        qf, monkeypatch):
    """Bookkeeping must never take down the work it is recording."""
    monkeypatch.setattr(q, "FRICTIONS", os.path.join("Z:", "nope", "f.jsonl"))
    _add(qf)
    done = q.run(qf, FakeOps(["FAIL"]), out=lambda *a: None)
    assert done[0].verdict == "FAIL"


def test_friction_lines_are_valid_jsonl(qf, tmp_path):
    _add(qf, "a.md")
    _add(qf, "b.md")
    q.run(qf, FakeOps(["FAIL", "FAIL"]), keep_going=True, out=lambda *a: None)
    lines = (tmp_path / "frictions.jsonl").read_text().splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)  # one object per line, no trailing junk


# ── persistence ──────────────────────────────────────────────────────

def test_state_survives_an_interrupted_run(qf, tmp_path):
    """A long run will be interrupted; the task in flight is the one that
    matters, so the file must already know about it."""
    _add(qf, "a.md")

    class Boom(FakeOps):
        def wait(self, task_id, timeout_min):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        q.run(qf, Boom(["PASS"]), out=lambda *a: None)

    reloaded = q.Queue(str(tmp_path / "queue.json"))
    assert reloaded.entries[0].state == q.RUNNING
    assert reloaded.entries[0].task_id == "20260923-000001"


def test_a_reloaded_queue_only_reruns_what_is_pending(qf, tmp_path):
    _add(qf, "a.md")
    _add(qf, "b.md")
    q.run(qf, FakeOps(["PASS"]), max_tasks=1, out=lambda *a: None)

    reloaded = q.Queue(str(tmp_path / "queue.json"))
    ops = FakeOps(["PASS"])
    q.run(reloaded, ops, out=lambda *a: None)
    assert [c[1] for c in ops.calls if c[0] == "spawn"] == ["b.md"]


def test_entry_round_trips_through_json(qf, tmp_path):
    _add(qf, "a.md", worker="ark", model="cheap", timeout_min=45)
    reloaded = q.Queue(str(tmp_path / "queue.json"))
    e = reloaded.entries[0]
    assert (e.worker, e.model, e.timeout_min) == ("ark", "cheap", 45)
    assert e.notes == []
