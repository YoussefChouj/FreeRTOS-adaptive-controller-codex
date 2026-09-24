"""Minimal MCP (Model Context Protocol) server for the dashboard agent layer.

Run as ``python -m ground_station.service.agent_mcp`` over stdio.

Because the official ``mcp`` SDK is not a guaranteed dependency on the
flight-test laptop, this server implements the small JSON-RPC-2.0-over-stdio
subset the spec requires by hand (stdlib only):

  * ``initialize`` / ``notifications/initialized``
  * ``tools/list`` / ``tools/call``
  * ``ping`` / ``server/discover``
  * protocol versions ``2025-06-18`` and ``2025-11-25``

It talks to the running dashboard service on ``GS_URL`` (default
``http://127.0.0.1:8081``) through ``urllib`` with ``ProxyHandler({})`` (never
proxying to a corporate proxy). Every mutation it performs goes *through the
plan* — this server exposes no tool that changes the control mode or
``allow_agent_arm``, approves anything, or sends a raw command outside a plan.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

GS_URL = os.environ.get("GS_URL", "http://127.0.0.1:8081")
_PROTOCOL_VERSIONS = ["2025-06-18", "2025-11-25"]

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_state",
        "description": "One agent-facing snapshot of the dashboard: control "
                       "mode, ui state, recording, arm state, stream health, "
                       "the running plan, pending approvals and the last "
                       "messages.",
        "inputSchema": {"type": "object"},
    },
    {
        "name": "list_actions",
        "description": "The agent action registry: every UI and service "
                       "action with its risk (safe|crtial) and args schema.",
        "inputSchema": {"type": "object"},
    },
    {
        "name": "run_plan",
        "description": "Submit a deterministic multi-step plan. Steps validate "
                       "up-front. Execution is asynchronous; critical steps "
                       "need operator approval. Optionally wait_s to poll the "
                       "final plan. Returns the full plan.",
        "inputSchema": {"type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "goal": {"type": "string"},
                            "steps": {"type": "array",
                                      "items": {"type": "object"}},
                            "wait_s": {"type": "integer"},
                        },
                        "required": ["title", "goal", "steps"]},
    },
    {
        "name": "get_plan",
        "description": "Fetch one plan with per-step status, result and error.",
        "inputSchema": {"type": "object",
                        "properties": {"id": {"type": "string"}},
                        "required": ["id"]},
    },
    {
        "name": "cancel_plan",
        "description": "Cancel a running/pending plan",
        "inputSchema": {"type": "object",
                        "properties": {"id": {"type": "string"}},
                        "required": ["id"]},
    },
    {
        "name": "say",
        "description": "Send a free-text message from the agent to the operator.",
        "inputSchema": {"type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"]},
    },
    {
        "name": "wait_for_operator",
        "description": "Long-poll for NEW operator notes. Tracks its own last "
                       "seq across calls so old messages are not replayed.",
        "inputSchema": {"type": "object",
                        "properties": {"timeout_s": {"type": "integer"}}},
    },
    {
        "name": "get_recording",
        "description": "Active recording state.",
        "inputSchema": {"type": "object"},
    },
    {
        "name": "list_sessions",
        "description": "List stored session directories/rows.",
        "inputSchema": {"type": "object"},
    },
    {
        "name": "analyze_session",
        "description": "Read-only analysis of a session directory: the "
                       "manifest, event counts by kind, duration, rows and "
                       "the first and last notes from events.jsonl.",
        "inputSchema": {"type": "object",
                        "properties": {"session_dir": {"type": "string"}},
                        "required": ["session_dir"]},
    },
    {
        "name": "explain_symbol",
        "description": "Deterministic explanation of a firmware symbol from the "
                       "agent map combined with its live telemetry value if active.",
        "inputSchema": {"type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"]},
    },
    {
        "name": "ui_navigate",
        "description": "Switch the dashboard to a named workspace tab "
                       "(broadcasts a 'ui' SSE event).",
        "inputSchema": {"type": "object",
                        "properties": {"tab": {"type": "string"}},
                        "required": ["tab"]},
    },
    {
        "name": "ui_highlight",
        "description": "Visually highlight a dashboard panel by slug "
                       "(broadcasts a 'ui' SSE event).",
        "inputSchema": {"type": "object",
                        "properties": {"panel": {"type": "string"}},
                        "required": ["panel"]},
    },
    {
        "name": "file_finding",
        "description": "Create or list findings in the research findings channel.",
        "inputSchema": {"type": "object",
                        "properties": {
                            "action": {"type": "string",
                                       "enum": ["new", "list"]},
                            "id": {"type": "string"},
                            "date": {"type": "string"},
                            "severity": {"type": "string"},
                            "status": {"type": "string",
                                       "enum": ["open", "in-progress", "resolved"]},
                            "runs": {"type": "array",
                                     "items": {"type": "string"}},
                            "summary": {"type": "string"},
                            "evidence": {"type": "string"},
                            "suggested_action": {"type": "string"},
                        },
                        "required": ["action"]},
    },
]

SERVER_IDENTITY = {"name": "dashboard", "version": "1.0.0"}


def _http(method: str, route: str, body: dict | None = None,
          params: dict | None = None) -> tuple[int, Any]:
    """One urllib call to the dashboard service (never proxied)."""
    import urllib.request
    from urllib.parse import urlencode

    url = GS_URL.rstrip("/") + route
    if params:
        url += "?" + urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        resp = opener.open(req, timeout=70)
        raw = resp.read()
    except urllib.error.HTTPError as e:  # noqa: F821
        try:
            raw = e.read()
        except Exception:
            raw = b"{}"
        try:
            return e.code, json.loads(raw or b"{}")
        except Exception:
            return e.code, {"raw": raw.decode(errors="replace")}
    try:
        payload = json.loads(raw or b"{}")
    except Exception:
        payload = {"raw": raw.decode(errors="replace")}
    return resp.status, payload


class McpServer:
    def __init__(self) -> None:
        self._op_seq: int | None = None

    # -- JSON-RPC dispatch --------------------------------------------------
    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method = msg.get("method")
        params = msg.get("params") or {}
        req_id = msg.get("id")
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": _PROTOCOL_VERSIONS[-1],
                    "supportedProtocolVersions": _PROTOCOL_VERSIONS,
                    "capabilities": {
                        "tools": {"listChanged": True},
                        "compute": {},
                    },
                    "serverInfo": SERVER_IDENTITY,
                }
                return self._response(req_id, result)
            if method == "notifications/initialized":
                return None  # notification: no response
            if method == "ping":
                return self._response(req_id, {})
            if method == "server/discover":
                return self._response(req_id, {
                    **SERVER_IDENTITY,
                    "tools": [t["name"] for t in TOOLS],
                    "protocolVersions": _PROTOCOL_VERSIONS,
                })
            if method == "tools/list":
                return self._response(req_id, {"tools": TOOLS})
            if method == "tools/call":
                result = self._call_tool(params.get("name"), params.get("arguments") or {})
                return self._response(req_id, result)
            return self._error(req_id, -32601,
                               f"method not found: {method}")
        except Exception as exc:  # noqa: BLE001
            return self._error(req_id, -32603, f"{type(exc).__name__}: {exc}")

    def _response(self, req_id, result) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def _error(self, req_id, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": code, "message": message}}

    # -- tools --------------------------------------------------------------
    def _call_tool(self, name: str, args: dict) -> dict[str, Any]:
        if name == "get_state":
            status, payload = _http("GET", "/api/agent/state")
            return self._text(self._wrap(payload, status))
        if name == "list_actions":
            status, payload = _http("GET", "/api/agent/actions")
            return self._text(self._wrap(payload, status))
        if name == "run_plan":
            status, payload = self._run_plan(args)
            return self._text(self._wrap(payload, status))
        if name == "get_plan":
            status, payload = _http("GET", f"/api/agent/plans/{args.get('id')}")
            return self._text(self._wrap(payload, status))
        if name == "cancel_plan":
            status, payload = _http("POST", f"/api/agent/plans/{args.get('id')}/cancel")
            return self._text(self._wrap(payload, status))
        if name == "say":
            status, payload = _http("POST", "/api/agent/message",
                                    {"text": str(args.get("text", "")),
                                     "source": "agent:mcp"})
            return self._text(self._wrap(payload, status))
        if name == "wait_for_operator":
            return self._text(self._wait_for_operator(args))
        if name == "get_recording":
            status, payload = _http("GET", "/api/recording")
            return self._text(self._wrap(payload, status))
        if name == "list_sessions":
            status, payload = _http("GET", "/sessions")
            return self._text(self._wrap(payload, status))
        if name == "analyze_session":
            return self._text(self._analyze_session(args))
        if name == "explain_symbol":
            return self._text(self._explain_symbol(args))
        if name == "ui_navigate":
            return self._text(self._ui_navigate(args))
        if name == "ui_highlight":
            return self._text(self._ui_highlight(args))
        if name == "file_finding":
            return self._text(self._file_finding(args))
        return {"content": [{"type": "text",
                             "text": json.dumps({"error": f"unknown tool {name}"})}]}

    def _run_plan(self, args: dict) -> tuple[int, Any]:
        # NO approval / control changes here. Only a plan submit.
        steps = args.get("steps") or []
        body = {
            "title": str(args.get("title", "")),
            "goal": args.get("goal"),
            "source": "agent:mcp",
            "steps": [
                {
                    "action": (s.get("action") if isinstance(s, dict) else ""),
                    "args": (s.get("args") or {}
                             if isinstance(s, dict) else {}),
                    "label": (s.get("label") if isinstance(s, dict) else None),
                    "on_error": (s.get("on_error") or "stop"
                                 if isinstance(s, dict) else "stop"),
                }
                for s in steps
            ],
        }
        status, created = _http("POST", "/api/agent/plans", body)
        if status not in (201, 200):
            return status, created
        plan_id = (created.get("plan_id")
                   or (created.get("plans", [{}])[0].get("plan_id")
                       if isinstance(created.get("plans"), list) else None))
        wait_s = int(args.get("wait_s") or 0)
        if plan_id and wait_s > 0:
            deadline = time.time() + min(wait_s, 60)
            while time.time() < deadline:
                gs, det = _http("GET", f"/api/agent/plans/{plan_id}")
                if gs == 200 and det.get("status") in (
                        "done", "failed", "cancelled"):
                    return gs, det
                time.sleep(0.5)
            # timed out: return current (still running) plan
            gs, det = _http("GET", f"/api/agent/plans/{plan_id}")
            if gs == 200:
                det["status"] = det.get("status") + " (wait_s elapsed)"
                return gs, det
        return status, created

    def _wait_for_operator(self, args: dict) -> dict[str, Any]:
        timeout_s = int(args.get("timeout_s") or 30)
        if self._op_seq is None:
            # set a baseline without blocking: current high-water mark
            _, notes = _http("GET", "/api/session/notes", params={"since": 0})
            self._op_seq = notes.get("last_seq", 0) if isinstance(notes, dict) else 0
            return {"notes": [], "baseline_seq": self._op_seq,
                    "note": "no new operator messages yet"}
        status, payload = _http(
            "GET", "/api/agent/messages/wait",
            params={"since": self._op_seq, "timeout": timeout_s})
        if isinstance(payload, dict) and isinstance(payload.get("last_seq"),
                                                    int):
            self._op_seq = payload["last_seq"]
        return payload

    def _analyze_session(self, args: dict) -> dict[str, Any]:
        import os as _os
        root = str(args.get("session_dir", ""))
        if not root or not _os.path.isdir(root):
            return {"error": f"session_dir not found: {root}"}
        out: dict[str, Any] = {"session_dir": root}
        manifest = _os.path.join(root, "manifest.json")
        if _os.path.exists(manifest):
            try:
                with open(manifest, encoding="utf-8") as fh:
                    out["manifest"] = json.load(fh)
            except Exception as exc:  # noqa: BLE001
                out["manifest_error"] = repr(exc)
        events_path = _os.path.join(root, "events.jsonl")
        counts: dict[str, int] = {}
        notes = []
        if _os.path.exists(events_path):
            with open(events_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except Exception:
                        continue
                    kind = ev.get("kind") or "?"
                    counts[kind] = counts.get(kind, 0) + 1
                    if ev.get("kind") in ("note", "goal", "marker", "agent"):
                        payload = ev.get("payload") or {}
                        if isinstance(payload, dict) and "text" in payload:
                            notes.append(payload["text"])
        out["event_counts_by_kind"] = counts
        out["first_note"] = notes[0] if notes else None
        out["last_note"] = notes[-1] if notes else None
        duration = None
        if isinstance(out.get("manifest"), dict):
            s = out["manifest"].get("started_at")
            e = out["manifest"].get("stopped_at")
            if s and e:
                try:
                    duration = float(e) - float(s)
                except (TypeError, ValueError):
                    duration = None
        out["duration_s"] = duration
        out["rows"] = (out.get("manifest") or {}).get("rows") or \
            self._count_rows(root)
        return out

    @staticmethod
    def _count_rows(root: str) -> int:
        import os as _os
        total = 0
        for name in _os.listdir(root):
            if name.endswith(".csv"):
                try:
                    with open(_os.path.join(root, name), encoding="utf-8") as fh:
                        total += sum(1 for _ in fh)  # rows (incl. header)
                except Exception:
                    pass
        return total

    def _explain_symbol(self, args: dict) -> dict[str, Any]:
        name = str(args.get("name", "")).strip()
        if not name:
            return {"error": "missing 'name' argument"}

        # Step 1: explain logic
        explanation = ""
        found = False
        try:
            from ground_station.agent_map.explain import format_explain
            explanation, found = format_explain(name)
        except Exception as exc:
            explanation = f"explain unavailable: {exc}"

        # Step 2: latest streamed value from GET /state. Exact key, or a
        # dotted key whose tail is exactly `name`; ambiguity returns nothing
        # rather than a value that may belong to another variable.
        live_val = None
        has_live = False
        matches: dict[str, Any] = {}
        status, state = _http("GET", "/state")
        streams = state.get("streams") if status == 200 and isinstance(state, dict) else None
        for data in (streams or {}).values():
            vals = data.get("values") if isinstance(data, dict) else None
            for k, v in (vals or {}).items():
                if k == name or k.endswith("." + name):
                    matches[k] = v[-1] if isinstance(v, (list, tuple)) and v else v
        if name in matches:
            matches = {name: matches[name]}
        if len(matches) == 1:
            live_val = next(iter(matches.values()))
            has_live = True

        # Step 3: combine into result text
        if has_live:
            live_str = f"live value: {live_val}"
        else:
            live_str = "live value: (not streaming / unavailable)"

        combined_text = f"{explanation}\n  {live_str}"

        return {
            "name": name,
            "explanation": explanation,
            "live_value": live_val if has_live else None,
            "text": combined_text,
        }

    def _ui_navigate(self, args: dict) -> dict[str, Any]:
        tab = str(args.get("tab", ""))
        if not tab:
            return {"error": "missing 'tab' argument"}
        status, payload = _http("POST", "/api/agent/plans", {
            "title": f"ui_navigate:{tab}",
            "goal": f"Navigate to tab {tab}",
            "source": "agent:mcp",
            "steps": [{
                "action": "ui_navigate",
                "args": {"tab": tab},
                "on_error": "stop",
            }],
        })
        return {"navigated_to": tab, "status": status}

    def _ui_highlight(self, args: dict) -> dict[str, Any]:
        panel = str(args.get("panel", ""))
        if not panel:
            return {"error": "missing 'panel' argument"}
        status, payload = _http("POST", "/api/agent/plans", {
            "title": f"ui_highlight:{panel}",
            "goal": f"Highlight panel {panel}",
            "source": "agent:mcp",
            "steps": [{
                "action": "ui_highlight",
                "args": {"panel": panel},
                "on_error": "stop",
            }],
        })
        return {"highlighted": panel, "status": status}

    def _file_finding(self, args: dict) -> dict[str, Any]:
        """Create or list a research finding."""
        action = str(args.get("action", ""))
        if action == "list":
            return self._list_findings()
        if action == "new":
            return self._create_finding(args)
        return {"error": f"unknown file_finding action: {action}"}

    def _list_findings(self) -> dict[str, Any]:
        from pathlib import Path
        findings_dir = Path(__file__).resolve().parents[2] / "docs" / "research-platform" / "findings"
        results = []
        if findings_dir.exists():
            for f in sorted(findings_dir.glob("*.md")):
                results.append(f.name)
        return {"findings": results}

    def _create_finding(self, args: dict) -> dict[str, Any]:
        """Create a finding markdown file from frontmatter + body."""
        from pathlib import Path
        findings_dir = Path(__file__).resolve().parents[2] / "docs" / "research-platform" / "findings"
        findings_dir.mkdir(parents=True, exist_ok=True)
        fid = str(args.get("id", "F-UNKNOWN"))
        date = str(args.get("date", ""))
        severity = str(args.get("severity", "medium"))
        status = str(args.get("status", "open"))
        runs_list = args.get("runs", [])
        summary = str(args.get("summary", ""))
        evidence = str(args.get("evidence", ""))
        suggested_action = str(args.get("suggested_action", ""))

        # Build frontmatter
        frontmatter = [
            f"id: {fid}",
            f"date: {date or 'TBD'}",
            f"severity: {severity}",
            f"status: {status}",
            f"runs: {json.dumps(runs_list)}",
            f"summary: {summary}",
            "",
            "---",
            "",
        ]
        body_lines = []
        if evidence:
            body_lines.append("## Evidence")
            body_lines.append(evidence)
            body_lines.append("")
        if suggested_action:
            body_lines.append("## Suggested Action")
            body_lines.append(suggested_action)
            body_lines.append("")

        content = "\n".join(frontmatter) + "\n".join(body_lines)
        fpath = findings_dir / f"{fid}.md"
        fpath.write_text(content, encoding="utf-8")
        return {"created": str(fpath), "id": fid}

    @staticmethod
    def _text(result: Any) -> dict[str, Any]:
        if isinstance(result, dict) and "text" in result:
            return {"content": [{"type": "text", "text": str(result["text"])}]}
        return {"content": [{"type": "text",
                             "text": json.dumps(result, default=str)}]}

    @staticmethod
    def _wrap(payload: Any, status: int) -> Any:
        return payload


def main() -> None:
    server = McpServer()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        if not isinstance(msg, dict):
            continue
        resp = server.handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    import time
    main()