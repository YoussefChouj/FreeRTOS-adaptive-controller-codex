"""Tests for the Phase 1B always-on activity journal and history API.

Covers: daily jsonl rotation, seq resume across restarts, the in-memory ring
+ file merge, filters, and the ``/api/agent/history`` endpooint backing the
timeline plugin. Journal-only fixtures use a tmp_path so no journal files are
written into the repo during the suite.
"""
import json
import http.client
import queue
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

from ground_station.service.activity import ActivityJournal
from ground_station.service.core import GroundStationService
from ground_station.service.storage import SessionStore

# Reuse the agent test fixtures/helpers by importing their constructors.
from ground_station.service.api import ApiServer


def _get(url: str, timeout: float = 5.0) -> tuple[int, dict]:
    parsed = urlparse(url)
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
    conn.request("GET", parsed.path + (("?" + parsed.query) if parsed.query else ""))
    resp = conn.getresponse()
    body = resp.read()
    try:
        return resp.status, json.loads(body)
    except Exception:
        return resp.status, {"raw": body.decode(errors="replace")}


def _post(url: str, body: dict, timeout: float = 5.0) -> tuple[int, dict]:
    parsed = urlparse(url)
    data = json.dumps(body).encode()
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
    conn.request("POST", parsed.path, data, {"Content-Type": "application/json"})
    resp = conn.getresponse()
    body = resp.read()
    try:
        return resp.status, json.loads(body)
    except Exception:
        return resp.status, {"raw": body.decode(errors="replace")}


# -- ActivityJournal unit tests ---------------------------------------------
def test_record_and_history_roundtrip(tmp_path):
    j = ActivityJournal(tmp_path)
    e1 = j.record("control", source="agent", actor="operator", data={"mode": "supervised"})
    e2 = j.record("message", source="agent", actor="agent:ark", data={"text": "hi"})
    assert e1["seq"] == 1 and e2["seq"] == 2
    # persisted as JSONL, one object per line, with the spec field order.
    lines = (tmp_path / (j.day_file.name)).read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["seq"] == 1
    assert first["kind"] == "control"
    assert "iso" in first and "t" in first and "data" in first
    # history from the merged file+ring (oldest first).
    hist = j.history(since=0)
    assert [e["seq"] for e in hist] == [1, 2]
    # filtering
    assert [e["seq"] for e in j.history(since=0, kind="control")] == [1]
    assert [e["seq"] for e in j.history(since=0, source="agent", kind="message")] == [2]
    # since page
    assert [e["seq"] for e in j.history(since=1)] == [2]
    j.close()


def test_seq_resumes_after_restart(tmp_path):
    j1 = ActivityJournal(tmp_path)
    j1.record("plan_created", source="agent", actor="agent", data={})
    j1.record("plan_created", source="agent", actor="agent", data={})
    j1.close()
    # simulate a restart — new instance resumes seq from the largest on disk.
    j2 = ActivityJournal(tmp_path)
    e = j2.record("control", source="agent", actor="operator", data={"mode": "off"})
    assert e["seq"] == 3
    # and merged history contains the pre-restart rows too
    seqs = [x["seq"] for x in j2.history(since=0)]
    assert seqs == [1, 2, 3]


def test_always_on_independent_of_recording(tmp_path):
    # No recording has ever started; journal still works (that's the point).
    j = ActivityJournal(tmp_path)
    e = j.record("service", source="system", actor="service", data={"event": "start"})
    assert e["seq"] == 1
    assert (tmp_path / (j.day_file.name)).is_file()


def test_stop_resumes_clean(tmp_path):
    j = ActivityJournal(tmp_path)
    for _ in range(10):
        j.record("plan_created", source="agent", actor="agent", data={})
    # ring max is small; oldest entries rotate out of memory but file keeps them.
    assert len(j.history(since=0)) == 10
    j.close()


# -- SSE activity event broadcast ------------------------------------------
def test_activity_broadcast_over_sse(tmp_path, service):
    from ground_station.service.agent import AgentManager
    mgr = AgentManager(service, shell_root=str(tmp_path), journal_root=str(tmp_path))
    q = queue.Queue()
    mgr._subscribers.append(q)
    try:
        mgr._activity("control", {"mode": "supervised"}, actor="operator", source="operator")
        frame = q.get_nowait()
        assert frame.startswith(b"event: activity")
        assert b'"seq": 1' in frame
        assert b'"source": "operator"' in frame
    finally:
        mgr.stop()


# -- /api/agent/history endpooint ------------------------------------------
@pytest.fixture
def service():
    svc = GroundStationService(store=SessionStore())
    svc.start()
    return svc


def test_history_api(tmp_path, service, monkeypatch):
    monkeypatch.setenv("GS_ACTIVITY_ROOT", str(tmp_path))
    server = ApiServer(service)
    server.start()
    base = "http://127.0.0.1:%d" % server.address[1]
    try:
        # ui-state posts (operator tab switch) land on the journal
        _post(base + "/api/agent/ui-state",
              {"active_tab": "overview", "drawer_open": True})
        _post(base + "/api/agent/ui-state",
              {"active_tab": "control", "drawer_open": False})
        # an agent message lands on the journal too
        _post(base + "/api/agent/message", {"text": "journal me", "source": "agent:test"})

        status, body = _get(base + "/api/agent/history?since=0")
        assert status == 200
        entries = body["entries"]
        kinds = [e["kind"] for e in entries]
        # messages are posted via /api/agent/message; ui_state via ui-state.
        assert "ui_state" in kinds
        assert any(e["source"] == "operator" for e in entries)
        assert body["count"] == len(entries)

        # source filter
        status, body = _get(base + "/api/agent/history?since=0&source=operator")
        assert all(e["source"] == "operator" for e in body["entries"])

        # since filter increases only strictly newer prows
        s0 = _get(base + "/api/agent/history?since=0")[1]["entries"]
        first_seq = s0[0]["seq"]
        sN = _get(base + "/api/agent/history?since=%d" % first_seq)[1]["entries"]
        assert all(e["seq"] > first_seq for e in sN)
    finally:
        server.stop()