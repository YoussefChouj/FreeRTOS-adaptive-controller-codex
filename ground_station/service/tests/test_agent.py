"""Tests for the agent control layer (phase 1A).

Covers plan validation / execution, approvals, the operator master switch,
command classification + the reused arm gate, the SSE bus, the notes
log/long-poll and the shell-change watcher.
"""
import json
import http.client
import os
import queue
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ground_station.livewatch.stream import StreamRange, StreamSchema
from ground_station.service.core import GroundStationService
from ground_station.service.storage import SessionStore
from ground_station.service.api import ApiServer
from ground_station.service.agent import (
    AgentManager,
    classify_command,
    WHY_CRITICAL_ARM,
    WHY_CRITICAL_PARAM,
)

# Most agent tests don't need the shell watcher thread; enable it explicitly
# in the one test that exercises it.
os.environ.setdefault("GS_SHELL_WATCH", "0")


def _get(url: str, timeout: float = 5.0) -> tuple[int, dict]:
    from urllib.parse import urlparse
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
    from urllib.parse import urlparse
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


def _schema():
    return StreamSchema(1, 1, 4,
                        (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)


@pytest.fixture
def service():
    svc = GroundStationService(store=SessionStore(), schemas=[_schema()],
                               source="sim")
    svc.start()
    return svc


@pytest.fixture
def api(service):
    server = ApiServer(service)
    server.start()
    try:
        yield server, "http://127.0.0.1:%d" % server.address[1]
    finally:
        server.stop()


def _new_plan(plan_id=None):
    return {"plan_id": plan_id, "title": "t", "goal": None,
            "source": "agent:test", "status": "pending", "steps": []}


def _wait_detail(base, plan_id, pred, timeout=7.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, detail = _get(base + "/api/agent/plans/" + plan_id)
        if status == 200 and pred(detail):
            return detail
        time.sleep(interval)
    return None


# ── validation ────────────────────────────────────────────────────────────
def test_plan_validation_400(api):
    server, base = api
    # bad step index 1: unknown action
    status, body = _post(base + "/api/agent/plans", {
        "title": "bad", "source": "agent:test",
        "steps": [
            {"action": "wait_ms", "args": {"ms": 10}},
            {"action": "does_not_exist", "args": {}},
        ],
    })
    assert status == 400
    assert "step[1]" in body["error"]
    # missing source
    status, body = _post(base + "/api/agent/plans", {
        "title": "bad", "steps": [{"action": "wait_ms", "args": {"ms": 1}}],
    })
    assert status == 400
    # wait_ms beyond bound
    status, body = _post(base + "/api/agent/plans", {
        "title": "bad", "source": "agent:test",
        "steps": [{"action": "wait_ms", "args": {"ms": 999999999}}],
    })
    # bound is clamped, not rejected; but the route must still be 201
    assert status == 201


def test_safe_only_plan_runs_to_done(api):
    _, base = api
    status, created = _post(base + "/api/agent/plans", {
        "title": "safe", "goal": "assemble", "source": "agent:test",
        "steps": [
            {"action": "wait_ms", "args": {"ms": 5}},
            {"action": "say", "args": {"text": "ready"}},
        ],
    })
    assert status == 201
    pid = created["plan_id"]
    detail = _wait_detail(base, pid, lambda d: d["status"] in ("done", "failed"))
    assert detail is not None and detail["status"] == "done"
    states = [s["status"] for s in detail["steps"]]
    assert states == ["done", "done"]
    # last_messages carries the say text
    _, st = _get(base + "/api/agent/state")
    texts = [m["text"] for m in st["last_messages"]]
    assert "ready" in texts


# ── UI steps ───────────────────────────────────────────────────────────────
def test_ui_step_times_out_without_ack(api):
    _, base = api
    status, created = _post(base + "/api/agent/plans", {
        "title": "ui", "source": "agent:test",
        "steps": [{"action": "switch_tab", "args": {"tab": "replay"}}],
    })
    pid = created["plan_id"]
    detail = _wait_detail(base, pid, lambda d: d["status"] in ("done", "failed"),
                          timeout=10)
    assert detail is not None
    assert detail["steps"][0]["status"] == "failed"
    assert "ui_timeout" in (detail["steps"][0]["error"] or "")


def test_ui_step_succeeds_with_ack(api):
    _, base = api
    status, created = _post(base + "/api/agent/plans", {
        "title": "ui-ok", "source": "agent:test",
        "steps": [{"action": "switch_tab", "args": {"tab": "overview"}}],
    })
    pid = created["plan_id"]
    # ack promptly (before the 5 s timeout)
    time.sleep(0.2)
    _post(base + "/api/agent/ui-ack",
          {"plan_id": pid, "step_id": created["steps"][0]["step_id"], "ok": True})
    detail = _wait_detail(base, pid, lambda d: d["status"] in ("done", "failed"))
    assert detail is not None and detail["status"] == "done"
    assert detail["steps"][0]["status"] == "done"


# ── approvals ──────────────────────────────────────────────────────────────
def _mock_gateway(service):
    m = MagicMock()
    m.submit.return_value = 42
    service.gateway = m
    return m


def test_critical_step_blocks_until_approved_then_runs(service, api):
    gateway = _mock_gateway(service)
    _, base = api
    status, created = _post(base + "/api/agent/plans", {
        "title": "crit", "source": "agent:test",
        "steps": [
            {"action": "wait_ms", "args": {"ms": 5}},
            {"action": "command", "args": {"command_id": 0x01},  # param write
             "label": "set pid"},
            {"action": "wait_ms", "args": {"ms": 5}},
        ],
    })
    assert status == 201
    pid = created["plan_id"]
    # The command step is queued for approval.
    _, ap = _get(base + "/api/agent/approvals")
    assert len(ap["approvals"]) == 1
    it = ap["approvals"][0]
    assert it["plan_id"] == pid and it["why_critical"] == WHY_CRITICAL_PARAM
    # Give the runner time to reach the critical step.
    _wait_detail(base, pid, lambda d: any(s["status"] == "awaiting_approval"
                                          for s in d["steps"]))
    status, _ = _post(base + "/api/agent/approvals/%s/%s/approve" % (pid, it["step_id"]),
                      {"source": "operator"})
    assert status == 200
    detail = _wait_detail(base, pid, lambda d: d["status"] in ("done", "failed"))
    assert detail is not None and detail["status"] == "done"
    assert gateway.submit.call_count >= 1


def test_rejection_stops_plan(service, api):
    gateway = _mock_gateway(service)
    _, base = api
    status, created = _post(base + "/api/agent/plans", {
        "title": "rej", "source": "agent:test",
        "steps": [
            {"action": "command", "args": {"command_id": 0x01}},
            {"action": "wait_ms", "args": {"ms": 5}},
        ],
    })
    pid = created["plan_id"]
    it = created["approvals"][0]
    _post(base + "/api/agent/approvals/%s/%s/reject" % (pid, it["step_id"]),
          {"source": "operator"})
    detail = _wait_detail(base, pid, lambda d: d["status"] in ("done", "failed", "cancelled"))
    assert detail is not None and detail["status"] == "failed"
    assert detail["steps"][0]["status"] == "failed"
    assert gateway.submit.call_count == 0


def test_early_approval_of_later_step(service, api):
    _mock_gateway(service)
    _, base = api
    status, created = _post(base + "/api/agent/plans", {
        "title": "early", "source": "agent:test",
        "steps": [
            {"action": "command", "args": {"command_id": 0x01}, "label": "a"},
            {"action": "command", "args": {"command_id": 0x02}, "label": "b"},
        ],
    })
    pid = created["plan_id"]
    approvals = created["approvals"]
    assert [a["why_critical"] for a in approvals] == [WHY_CRITICAL_PARAM,
                                                     WHY_CRITICAL_PARAM]
    # approve the LATER one first, then the earlier one
    _post(base + "/api/agent/approvals/%s/%s/approve" % (pid, approvals[1]["step_id"]),
          {"source": "operator"})
    _post(base + "/api/agent/approvals/%s/%s/approve" % (pid, approvals[0]["step_id"]),
          {"source": "operator"})
    detail = _wait_detail(base, pid, lambda d: d["status"] in ("done", "failed"))
    assert detail is not None and detail["status"] == "done"
    assert [s["status"] for s in detail["steps"]] == ["done", "done"]


# ── master switch ──────────────────────────────────────────────────────────
def test_mode_off_cancels_running_plan_and_returns_423(service, api):
    gateway = _mock_gateway(service)
    _, base = api
    status, created = _post(base + "/api/agent/plans", {
        "title": "arm-wait", "source": "agent:test",
        "steps": [
            {"action": "command", "args": {"command_id": 0x06}},  # arm-ish
        ],
    })
    pid = created["plan_id"]
    # running -> set mode off
    status, body = _post(base + "/api/agent/control",
                         {"mode": "off", "source": "operator"})
    assert status == 200 and body["mode"] == "off"
    detail = _wait_detail(base, pid, lambda d: d["status"] == "cancelled")
    assert detail is not None and detail["status"] == "cancelled"
    # while off, agent-mutating routes return 423
    status, body = _post(base + "/api/agent/plans", {
        "title": "x", "source": "agent:test",
        "steps": [{"action": "wait_ms", "args": {"ms": 1}}],
    })
    assert status == 423 and body["error"]["code"] == "agent_disabled"
    status, body = _post(base + "/api/agent/control",
                         {"mode": "supervised", "source": "operator"})
    assert status == 423
    assert gateway.submit.call_count == 0


def test_agent_cannot_approve_its_own_critical_step(service, api):
    """An ``agent:`` source is refused at the approval queue.

    The approval exists to put a human in front of a critical step, so an
    agent approving one removes the only gate.  ``set_control`` already
    refused an ``agent:`` source for the operator-only switches; the approval
    route did not.

    This covers the honest path.  An agent that lies and sends
    ``source: "operator"`` is still indistinguishable -- see the trust-model
    note in ``AgentManager.decide_approval``.
    """
    gateway = _mock_gateway(service)
    _, base = api
    status, created = _post(base + "/api/agent/plans", {
        "title": "self-approve", "source": "agent:test",
        "steps": [{"action": "command", "args": {"command_id": 0x01}}],
    })
    assert status == 201
    pid = created["plan_id"]
    _, ap = _get(base + "/api/agent/approvals")
    assert len(ap["approvals"]) == 1
    step_id = ap["approvals"][0]["step_id"]
    url = base + "/api/agent/approvals/%s/%s/approve" % (pid, step_id)

    status, body = _post(url, {"source": "agent:test"})
    assert status == 403, body
    assert gateway.submit.call_count == 0, "critical step ran on a self-approval"

    # The operator can still approve the very same item.
    status, body = _post(url, {"source": "operator"})
    assert status == 200, body


def test_mode_off_cancel_with_a_body_does_not_break_the_connection(service, api):
    """The cancel route must drain the request body before answering 423.

    Five sibling 423 paths call ``_drain_body`` first; the cancel route did
    not, so the server closed the socket with the client's body still in
    flight.  On Windows that surfaces in the *caller* as
    ``ConnectionAbortedError [WinError 10053]`` rather than a clean 423, which
    is why it reads as a flake instead of a failure.

    It is a race, so one POST is not a test: with the drain removed a single
    attempt still passed 4 times in 5.  Repeating it turns a 20% detection rate
    into ~96%, and with the drain in place every attempt passes.
    """
    _, base = api
    status, _ = _post(base + "/api/agent/control",
                      {"mode": "off", "source": "operator"})
    assert status == 200
    # A body far larger than the socket send buffer, so the client is still
    # writing when the server answers -- the condition that aborts the send.
    payload = {"reason": "operator cancelled", "pad": "x" * 512000}
    for attempt in range(15):
        status, body = _post(base + "/api/agent/plans/no-such-plan/cancel",
                             payload)
        assert status == 423, "attempt %d: %r" % (attempt, body)
        assert body["error"]["code"] == "agent_disabled"


def test_autonomous_tier0_param_write_still_waits(service, api):
    gateway = _mock_gateway(service)
    _, base = api
    _post(base + "/api/agent/control", {"mode": "autonomous", "source": "operator"})
    # PID gains are tier-0 state: approval is required even in autonomous
    status, created = _post(base + "/api/agent/plans", {
        "title": "auto-param", "source": "agent:test",
        "steps": [{"action": "command", "args": {"command_id": 0x01}}],
    })
    assert status == 201
    pid = created["plan_id"]
    assert created["approvals"] and created["approvals"][0]["tier"] == 0
    _wait_detail(base, pid, lambda d: any(s["status"] == "awaiting_approval"
                                          for s in d["steps"]))
    assert gateway.submit.call_count == 0
    # allow_agent_arm does not release a tier-0 param write
    _post(base + "/api/agent/control",
          {"allow_agent_arm": True, "source": "operator"})
    _wait_detail(base, pid, lambda d: False, timeout=0.5)
    assert gateway.submit.call_count == 0


def test_full_tier0_access_releases_param_write(service, api):
    gateway = _mock_gateway(service)
    _, base = api
    _post(base + "/api/agent/control", {"mode": "autonomous", "source": "operator"})
    status, created = _post(base + "/api/agent/plans", {
        "title": "auto-param", "source": "agent:test",
        "steps": [{"action": "command", "args": {"command_id": 0x01}}],
    })
    pid = created["plan_id"]
    _wait_detail(base, pid, lambda d: any(s["status"] == "awaiting_approval"
                                          for s in d["steps"]))
    assert gateway.submit.call_count == 0
    status, ctl = _post(base + "/api/agent/control",
                        {"tier0_access": "full", "source": "operator"})
    assert status == 200 and ctl["tier0_access"] == "full"
    detail = _wait_detail(base, pid, lambda d: d["status"] in ("done", "failed"))
    assert detail is not None and detail["status"] == "done"
    assert gateway.submit.call_count >= 1
    # later tier-0 writes run without queueing
    status, created = _post(base + "/api/agent/plans?queue=true", {
        "title": "auto-param-2", "source": "agent:test",
        "steps": [{"action": "command", "args": {"command_id": 0x15}}],
    })
    assert created["approvals"] == []


def test_tier0_access_is_operator_only(service, api):
    _mock_gateway(service)
    _, base = api
    status, _ = _post(base + "/api/agent/control",
                      {"tier0_access": "full", "source": "agent:test"})
    assert status == 403
    status, _ = _post(base + "/api/agent/control",
                      {"allow_agent_arm": True, "source": "agent:test"})
    assert status == 403
    status, _ = _post(base + "/api/agent/control",
                      {"tier0_access": "everything", "source": "operator"})
    assert status == 400
    status, ctl = _get(base + "/api/agent/control")
    assert ctl["tier0_access"] == "partial" and ctl["allow_agent_arm"] is False


def test_tier1_to_tier0_flow_released_with_full_access(service, api):
    _mock_gateway(service)
    _, base = api
    _post(base + "/api/agent/control", {"mode": "autonomous", "source": "operator"})
    _post(base + "/api/agent/control", {"tier0_access": "full", "source": "operator"})
    status, created = _post(base + "/api/agent/plans", {
        "title": "ekf-of bias", "source": "agent:test",
        "steps": [{"action": "command",
                   "args": {"command_id": 0x1E, "index": 0, "value": 2}}],
    })
    assert status == 201
    assert created["approvals"] == []


def test_full_access_grants_arm_and_partial_revokes_it(service, api):
    _, base = api
    _post(base + "/api/agent/control", {"mode": "autonomous", "source": "operator"})
    _, full = _post(base + "/api/agent/control",
                    {"tier0_access": "full", "source": "operator"})
    assert full["allow_agent_arm"] is True
    _, partial = _post(base + "/api/agent/control",
                       {"tier0_access": "partial", "source": "operator"})
    assert partial["allow_agent_arm"] is False


def test_tier1_to_tier0_flow_is_flagged(service, api):
    _mock_gateway(service)
    _, base = api
    status, created = _post(base + "/api/agent/plans", {
        "title": "ekf-of bias", "source": "agent:test",
        "steps": [
            {"action": "command",
             "args": {"command_id": 0x1E, "index": 0, "value": 2}},
            {"action": "command",
             "args": {"command_id": 0x1E, "index": 0, "value": 1}},
        ],
    })
    assert status == 201
    assert created["approvals"][0]["flags"] == ["tier1_to_tier0"]
    assert created["approvals"][1]["flags"] == []


def test_autonomous_arm_still_waits_unless_allow_agent_arm(service, api):
    gateway = _mock_gateway(service)
    _, base = api
    _post(base + "/api/agent/control",
          {"mode": "autonomous", "source": "operator"})
    status, created = _post(base + "/api/agent/plans", {
        "title": "auto-arm", "source": "agent:test",
        "steps": [{"action": "command", "args": {"command_id": 0x06}}],
    })
    pid = created["plan_id"]
    # arm-ish still queued + blocked in autonomous (allow_agent_arm off)
    assert created["approvals"] and \
        created["approvals"][0]["why_critical"] == WHY_CRITICAL_ARM
    _wait_detail(base, pid, lambda d: any(s["status"] == "awaiting_approval"
                                          for s in d["steps"]))
    assert gateway.submit.call_count == 0  # still blocked
    # flip allow_agent_arm -> auto proceeds
    _post(base + "/api/agent/control",
          {"allow_agent_arm": True, "source": "operator"})
    detail = _wait_detail(base, pid, lambda d: d["status"] in ("done", "cancelled", "failed"))
    assert detail is not None and detail["status"] == "done"
    assert gateway.submit.call_count >= 1


# ── command classification ────────────────────────────────────────────────
def test_disarm_is_never_critical():
    # 0x0D ABORT_ALL_PATHS and 0x04 FLIGHT_MODE_ABORT (disarm/estop family).
    assert classify_command(0x0D) is None
    assert classify_command(0x04) is None
    # arm/motor/throttle and param writes are critical
    assert classify_command(0x06) == WHY_CRITICAL_ARM
    assert classify_command(0x01) == WHY_CRITICAL_PARAM
    assert classify_command(0x16) == WHY_CRITICAL_ARM


def test_command_step_reuses_arm_gate(service, api):
    # No gateway and arm unknown: MOTOR_BENCH (0x16) must fail through the
    # same SAFETY_INTERLOCK path as POST /commands, NOT an agent bypass.
    _mock_gateway(service)
    _, base = api
    status, created = _post(base + "/api/agent/plans", {
        "title": "gate", "source": "agent:test",
        "steps": [{"action": "command", "args": {"command_id": 0x16}}],
    })
    pid = created["plan_id"]
    it = created["approvals"][0]
    _post(base + "/api/agent/approvals/%s/%s/approve" % (pid, it["step_id"]),
          {"source": "operator"})
    detail = _wait_detail(base, pid, lambda d: d["status"] in ("failed", "done"))
    assert detail is not None and detail["status"] == "failed"
    err = detail["steps"][0]["error"] or ""
    # arm state is unknown (no telemetry) -> the interlock fires.
    assert "arm state" in err or "SAFETY_INTERLOCK" in err


# ── SSE bus ────────────────────────────────────────────────────────────────
def test_sse_delivers_plan_step_and_message(service, api):
    _, base = api
    frames = []
    stop = threading.Event()

    def reader():
        conn = http.client.HTTPConnection("127.0.0.1", api[0].address[1],
                                          timeout=5)
        conn.request("GET", "/api/agent/stream")
        resp = conn.getresponse()
        while not stop.is_set():
            line = resp.fp.readline()
            if not line:
                break
            s = line.decode(errors="replace").strip()
            if s.startswith(("event: plan", "event: step", "event: message")):
                frames.append(s)
            if len(frames) >= 4:
                break

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    time.sleep(0.3)
    _post(base + "/api/agent/plans", {
        "title": "sse", "source": "agent:test",
        "steps": [{"action": "wait_ms", "args": {"ms": 2}}],
    })
    _post(base + "/api/agent/message", {"text": "hello via sse", "source": "agent:test"})
    t.join(timeout=6)
    stop.set()
    assert any(e.startswith("event: plan") for e in frames)
    assert any(e.startswith("event: step") for e in frames)
    assert any(e.startswith("event: message") for e in frames)


def test_slow_subscriber_does_not_block(service):
    mgr = AgentManager(service, shell_root=None)
    qq = queue.Queue(maxsize=2)
    qq.put_nowait(b"old1")
    qq.put_nowait(b"old2")
    mgr._subscribers.append(qq)
    # Broadcasting to a full queue must drop-oldest, not block.
    mgr._broadcast("control", {"mode": "off"})
    got = []
    while not qq.empty():
        got.append(qq.get_nowait())
    # drop-oldest: old1 evicted, newest control event landed alongside old2
    assert any(b"event: control" in f for f in got)
    assert not any(b"old1" in f for f in got)


# ── notes / long-poll ──────────────────────────────────────────────────────
def test_notes_since_and_long_poll(service, api):
    _, base = api
    # operator note
    _post(base + "/api/session/note", {"text": "note one", "kind": "note",
                                       "source": "test"})
    status, notes = _get(base + "/api/session/notes?since=0")
    # Without a key the co-pilot answers the first note with an "is off" line.
    mine = [n for n in notes["notes"] if n["source"] == "test"]
    assert status == 200 and len(mine) == 1
    seq = notes["notes"][-1]["seq"]
    # long-poll blocks until a *new* note arrives
    result = {}
    def poll():
        result["resp"] = _get(base +
                              "/api/agent/messages/wait?since=%d&timeout=4" % seq)
    t = threading.Thread(target=poll, daemon=True)
    t.start()
    time.sleep(0.3)
    _post(base + "/api/agent/message", {"text": "operator answer", "source": "agent:x"})
    t.join(timeout=6)
    assert "resp" in result
    status, notes = result["resp"]
    texts = [n["text"] for n in notes["notes"]]
    assert "operator answer" in texts


# ── shell watcher ──────────────────────────────────────────────────────────
def test_shell_updated_watcher(tmp_path, service):
    mgr = AgentManager(service, shell_root=None)
    root = tmp_path / "shell"
    root.mkdir()
    (root / "index.html").write_text("v1")
    mgr.shell_root = str(root)
    mgr.shell_watch_enabled = True
    sub = queue.Queue(maxsize=16)
    mgr._subscribers.append(sub)
    mgr.start()
    try:
        time.sleep(0.4)
        (root / "index.html").write_text("v2")
        got = False
        deadline = time.time() + 5
        while time.time() < deadline and not got:
            while not sub.empty():
                f = sub.get_nowait()
                if b"shell_updated" in f:
                    got = True
            time.sleep(0.2)
        assert got
    finally:
        mgr.stop()


# ── wait_for ───────────────────────────────────────────────────────────────
def test_wait_for_injected_telemetry(service, api):
    _, base = api
    status, created = _post(base + "/api/agent/plans", {
        "title": "wait", "source": "agent:test",
        "steps": [{"action": "wait_for",
                   "args": {"key": "altitude", "op": ">", "value": 10.0,
                            "timeout_s": 4}},
                  {"action": "say", "args": {"text": "high"}}],
    })
    pid = created["plan_id"]

    def inject():
        time.sleep(0.5)
        service.inject_external_stream(0, {"altitude": 12.0})
    threading.Thread(target=inject, daemon=True).start()
    detail = _wait_detail(base, pid, lambda d: d["status"] in ("done", "failed"),
                          timeout=8)
    assert detail is not None and detail["status"] == "done"
    assert detail["steps"][0]["status"] == "done"