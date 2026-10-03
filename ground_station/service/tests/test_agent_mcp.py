"""End-to-end tests for the stdio MCP server (ground_station.service.agent_mcp).

Runs the server as a subprocess against a live in-process ApiServer, exactly
as a real MCP client would: write one JSON-RPC request per line to stdin and
read the response line from stdout.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ground_station.livewatch.stream import StreamRange, StreamSchema
from ground_station.service.core import GroundStationService
from ground_station.service.storage import SessionStore
from ground_station.service.api import ApiServer


@pytest.fixture
def service():
    svc = GroundStationService(
        store=SessionStore(),
        schemas=[StreamSchema(1, 1, 4,
                              (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)],
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

SERVER_NAMES = [
    "get_state", "list_actions", "run_plan", "get_plan", "cancel_plan",
    "say", "wait_for_operator", "get_recording", "list_sessions",
    "analyze_session", "explain_symbol", "ui_navigate", "ui_highlight",
    "file_finding", "campaign_state", "campaign_preflight", "campaign_go", "campaign_pause", "campaign_land",
    "campaign_abort"
]


def _connect(api_port, repo_root):
    env = dict(os.environ)
    env["GS_URL"] = "http://127.0.0.1:%d" % api_port
    env["PYTHONPATH"] = str(repo_root) + os.pathsep + env.get("PYTHONPATH", "")
    env["GS_SHELL_WATCH"] = "0"
    return subprocess.Popen(
        [sys.executable, "-m", "ground_station.service.agent_mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=str(repo_root),
    )


def _request(proc, req_id, method, params=None):
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method,
           "params": params or {}}
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        raise AssertionError("MCP server produced no response (stderr: "
                             + (proc.stderr.read() if proc.stderr else ""))
    return json.loads(line)


def test_mcp_initialize_tools_and_run_plan(service, api):
    from ground_station.service.api import ApiServer
    # ``api`` fixture yields (server, base_url); reuse a fresh server here so
    # we control the port for the subprocess. Simpler: close fixture's and
    # build our own on a known port via port=0.
    server, base = api
    api_port = server.address[1]

    repo_root = Path(__file__).resolve().parents[3]
    proc = _connect(api_port, repo_root)
    try:
        # initialize
        resp = _request(proc, 1, "initialize", {"protocolVersion": "2025-06-18",
                                                "capabilities": {},
                                                "clientInfo": {"name": "test"}})
        assert resp["result"]["protocolVersion"] in ("2025-06-18", "2025-11-25")
        assert set(resp["result"]["supportedProtocolVersions"]) == {
            "2025-06-18", "2025-11-25"}

        # tools/list -> exact names
        resp = _request(proc, 2, "tools/list")
        names = [t["name"] for t in resp["result"]["tools"]]
        assert names == SERVER_NAMES

        # ping
        resp = _request(proc, 3, "ping")
        assert "result" in resp

        # run_plan end to end (safe plan -> done)
        resp = _request(proc, 4, "tools/call", {
            "name": "run_plan",
            "arguments": {
                "title": "mcp-safe",
                "goal": "verify mcp",
                "steps": [{"action": "wait_ms", "args": {"ms": 5}},
                          {"action": "say", "args": {"text": "mcp hello"}}],
                "wait_s": 6,
            },
        })
        text = resp["result"]["content"][0]["text"]
        plan = json.loads(text)
        assert plan.get("status") == "done", text
        assert plan["step_count"] == 2
    finally:
        proc.stdin.close() if proc.stdin else None
        try:
            proc.terminate()
        except Exception:
            pass


def test_mcp_get_state_and_say(service, api):
    server, base = api
    api_port = server.address[1]
    repo_root = Path(__file__).resolve().parents[3]
    proc = _connect(api_port, repo_root)
    try:
        resp = _request(proc, 10, "tools/call",
                        {"name": "get_state", "arguments": {}})
        text = resp["result"]["content"][0]["text"]
        state = json.loads(text)
        assert state["control"]["mode"] == "supervised"
        assert "last_messages" in state

        resp = _request(proc, 11, "tools/call",
                        {"name": "say", "arguments": {"text": "via mcp"}})
        assert "result" in resp
    finally:
        proc.stdin.close() if proc.stdin else None
        try:
            proc.terminate()
        except Exception:
            pass


def test_mcp_explain_symbol(service, api):
    import struct
    from ground_station.livewatch.transport import crc16_ccitt

    def data_frame(slot: int, sequence: int, source_ms: int, value: float) -> bytes:
        payload = struct.pack("<If", source_ms, value)
        frame_type = 0x09 + slot
        header = bytes((0xAA, 0xBB, frame_type, 0, len(payload), sequence))
        return header + payload + crc16_ccitt(header[2:] + payload).to_bytes(2, "big")

    server, base = api
    api_port = server.address[1]
    repo_root = Path(__file__).resolve().parents[3]
    proc = _connect(api_port, repo_root)
    try:
        # 1. explain symbol with streaming value (from fixture schema: 'altitude')
        service.ingest(data_frame(0, 1, 100, 42.5), time_ns=1000)
        resp = _request(proc, 20, "tools/call",
                        {"name": "explain_symbol", "arguments": {"name": "altitude"}})
        assert "result" in resp, resp
        text = resp["result"]["content"][0]["text"]
        assert "live value: 42.5" in text

        # 2. explain symbol not streaming
        resp = _request(proc, 21, "tools/call",
                        {"name": "explain_symbol", "arguments": {"name": "s_ekf"}})
        assert "result" in resp, resp
        text = resp["result"]["content"][0]["text"]
        assert "s_ekf" in text
        assert "live value: (not streaming / unavailable)" in text
    finally:
        proc.stdin.close() if proc.stdin else None
        try:
            proc.terminate()
        except Exception:
            pass

def test_mcp_campaign_tools(service, api):
    server, base = api
    api_port = server.address[1]
    repo_root = Path(__file__).resolve().parents[3]
    proc = _connect(api_port, repo_root)
    try:
        resp = _request(proc, 30, "tools/call", {"name": "campaign_state", "arguments": {}})
        state = json.loads(resp["result"]["content"][0]["text"])
        assert state["status"] == "idle"
        assert state["flights"] == []
        # No run is active, so each control tool reaches the route and gets its 409.
        for req_id, name in ((31, "campaign_pause"), (32, "campaign_land"), (33, "campaign_abort")):
            resp = _request(proc, req_id, "tools/call", {"name": name, "arguments": {}})
            assert json.loads(resp["result"]["content"][0]["text"]) == {"error": "no active run"}
        # campaign_go without the operator's quote is refused by the route (agent:mcp source)
        resp = _request(proc, 34, "tools/call", {"name": "campaign_go", "arguments": {
            "campaign_path": "x.yaml", "pack_id": "P", "checklist": {"ok": True}, "confirmation": ""}})
        assert "confirmation" in resp["result"]["content"][0]["text"]
        # campaign_preflight: one call, every row named, ok false without a launch copy
        resp = _request(proc, 35, "tools/call", {"name": "campaign_preflight", "arguments": {
            "campaign_path": "", "pack_id": "P4000-1"}})
        pre = json.loads(resp["result"]["content"][0]["text"])
        assert [c["name"] for c in pre["checks"]] == [
            "service", "link", "firmware", "wfb_status", "rc_link", "arm_state", "position", "battery", "runner",
            "log_plan"]
        assert pre["ok"] is False and pre["checks"][-1]["fix"].startswith("pass campaign=")
    finally:
        proc.stdin.close() if proc.stdin else None
        try:
            proc.terminate()
        except Exception:
            pass


def test_http_reports_unreachable_8081_and_restart(monkeypatch):
    """WP-23: a stopped / restarting 8081 is a plain 503 "retry" answer, and a new instance id is flagged."""
    import socket
    from ground_station.service import agent_mcp

    with socket.socket() as s:  # a port nobody listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(agent_mcp, "GS_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(agent_mcp, "RESTART_RETRY_S", 0.0)
    code, payload = agent_mcp._http("GET", "/api/campaign/state")
    assert code == 503 and payload["retry"] is True and "8081 unreachable" in payload["error"]

    monkeypatch.setattr(agent_mcp, "_last_instance", None)
    assert "notice" not in agent_mcp._note_instance({"X-GS-Instance": "1-1"}, {"status": "idle"})
    assert "notice" not in agent_mcp._note_instance({"X-GS-Instance": "1-1"}, {"status": "idle"})
    out = agent_mcp._note_instance({"X-GS-Instance": "2-2"}, {"status": "idle"})
    assert "8081 restarted" in out["notice"] and out["status"] == "idle"