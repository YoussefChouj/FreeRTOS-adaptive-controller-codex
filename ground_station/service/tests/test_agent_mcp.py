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
    "analyze_session",
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