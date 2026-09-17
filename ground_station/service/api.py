"""Small JSON API and WebSocket-style publication hub.

The HTTP server intentionally uses only the standard library so the service can
run on the flight-test laptop without adding a web framework dependency.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any




# MIME type mapping for static file serving
_MIME_TYPES: dict[str, str] = {
    ".html": "text/html",
    ".js": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".eot": "application/vnd.ms-fontobject",
}


def _mime_type(path: str) -> str:
    ext = Path(path).suffix.lower()
    return _MIME_TYPES.get(ext, "application/octet-stream")


class StateHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[Any] = []

    def subscribe(self, callback) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def publish(self, state) -> None:
        payload = state if isinstance(state, dict) else state.__dict__
        with self._lock:
            subscribers = tuple(self._subscribers)
        for callback in subscribers:
            callback(payload)


def make_handler(service, hub: StateHub | None = None, static_root: Path | None = None,
                experiment_runtime=None):
    hub = hub or StateHub()

    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, sort_keys=True, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _static(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            from urllib.parse import parse_qs

            if self.path == "/health":
                self._json(200, {"ok": True, "schema_id": service.schema.schema_id})
            elif self.path == "/state":
                self._json(200, service.snapshot().__dict__)
            # GET /sessions — list all sessions
            elif self.path == "/sessions":
                try:
                    rows = service.store._db.execute(
                        "SELECT id,started_ns,ended_ns,schema_id,source FROM sessions"
                    ).fetchall()
                    self._json(200, [{"id": r[0], "started_ns": r[1],
                                      "ended_ns": r[2], "schema_id": r[3],
                                      "source": r[4]} for r in rows])
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
            # GET /sessions/<id> — session detail
            elif self.path.startswith("/sessions/") and "/records" not in self.path:
                parts = self.path.split("/")
                session_id = parts[2] if len(parts) >= 3 else None
                if not session_id:
                    self._json(404, {"error": "missing session id"})
                    return
                try:
                    detail = service.store.session(session_id)
                    self._json(200, detail)
                except KeyError:
                    self._json(404, {"error": "session not found"})
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
            # GET /sessions/<id>/records — all records for a session
            elif self.path.startswith("/sessions/") and self.path.endswith("/records"):
                parts = self.path.split("/")
                session_id = parts[2] if len(parts) >= 3 else None
                if not session_id:
                    self._json(404, {"error": "missing session id"})
                    return
                try:
                    records = list(service.store.iter_records(session_id))
                    self._json(200, {"session_id": session_id, "records": records})
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
            # GET /experiments — list active experiment runs
            elif self.path == "/experiments":
                if experiment_runtime is None:
                    self._json(503, {"error": "experiment runtime not available"})
                    return
                active = experiment_runtime.active
                # Only return runs that are actively settling/measuring
                if active is None or active.state.value in ("complete", "aborted"):
                    self._json(200, [])
                    return
                self._json(200, [{
                    "name": active.name,
                    "state": active.state.value,
                    "tick": active.tick,
                    "samples": len(active.samples),
                }])
            # GET /experiments/<name> — experiment detail
            elif self.path.startswith("/experiments/"):
                if experiment_runtime is None:
                    self._json(503, {"error": "experiment runtime not available"})
                    return
                parts = self.path.split("/")
                name = parts[2] if len(parts) >= 3 else None
                active = experiment_runtime.active
                if active is None or active.name != name:
                    self._json(404, {"error": "experiment not found"})
                    return
                self._json(200, {
                    "name": active.name,
                    "state": active.state.value,
                    "tick": active.tick,
                    "settle_ticks": active.settle_ticks,
                    "measure_ticks": active.measure_ticks,
                    "samples": active.samples,
                    "events": [(e.tick, e.name, e.detail) for e in active.events],
                    "parameters_before": active.parameters_before,
                    "parameters_after": active.parameters_after,
                })
            # GET /artifacts — indexed artifacts
            elif self.path == "/artifacts":
                from ground_station.analysis.artifacts import index_artifacts
                try:
                    artifacts = index_artifacts()
                    self._json(200, {"artifacts": artifacts})
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
            # GET /analysis/compare — compare two sessions
            elif self.path.startswith("/analysis/compare"):
                from ground_station.analysis.session import compare_sessions
                try:
                    qs = parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
                    a = qs.get("a", [None])[0]
                    b = qs.get("b", [None])[0]
                    stream_str = qs.get("stream", [None])[0]
                    key = qs.get("key", [None])[0]
                    if not a or not b or not stream_str or not key:
                        self._json(400, {"error": "missing a, b, stream, or key query params"})
                        return
                    result = compare_sessions(service.store, a, b, int(stream_str), key)
                    self._json(200, result)
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
            elif static_root is not None:
                # Static file serving
                path = self.path
                # Map / to index.html
                if path == "/":
                    path = "/index.html"
                # Strip /static/ prefix if present
                prefix = "/static/"
                if path.startswith(prefix):
                    path = path[len(prefix):]
                file_path = static_root / path.lstrip("/")
                if file_path.is_file():
                    try:
                        body = file_path.read_bytes()
                        self._static(200, body, _mime_type(str(file_path)))
                    except Exception:
                        self._json(500, {"error": "failed to read file"})
                else:
                    self._json(404, {"error": "file not found"})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            from urllib.parse import parse_qs

            if self.path == "/commands":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = json.loads(self.rfile.read(length) or b"{}")
                    txid = service.submit_command(int(body["command_id"]),
                                                  int(body.get("index", 0)),
                                                  float(body.get("value", 0.0)),
                                                  int(body.get("flags", 0)))
                except Exception as exc:
                    self._json(400, {"error": str(exc)})
                    return
                self._json(202, {"transaction_id": txid})
            # POST /experiments — start an experiment
            elif self.path == "/experiments":
                if experiment_runtime is None:
                    self._json(503, {"error": "experiment runtime not available"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = json.loads(self.rfile.read(length) or b"{}")
                    name = body["name"]
                    settle_ticks = int(body.get("settle_ticks", 0))
                    measure_ticks = int(body.get("measure_ticks", 100))
                    parameters = dict(body.get("parameters", {}))
                    run = experiment_runtime.start(name, parameters,
                                                  settle_ticks, measure_ticks)
                    self._json(201, {"run_id": run.name})
                except Exception as exc:
                    self._json(400, {"error": str(exc)})
            # POST /experiments/<name>/abort — abort experiment
            elif self.path.startswith("/experiments/") and self.path.endswith("/abort"):
                if experiment_runtime is None:
                    self._json(503, {"error": "experiment runtime not available"})
                    return
                parts = self.path.split("/")
                name = parts[2] if len(parts) >= 3 else None
                active = experiment_runtime.active
                if active is None or active.name != name:
                    self._json(404, {"error": "experiment not found"})
                    return
                try:
                    experiment_runtime.abort("http_abort")
                    self._json(200, {"aborted": name})
                except Exception as exc:
                    self._json(400, {"error": str(exc)})
            # POST /sessions/<id>/export — export session to CSV
            elif self.path.startswith("/sessions/") and self.path.endswith("/export"):
                from ground_station.analysis.session import export_session_csv
                parts = self.path.split("/")
                session_id = parts[2] if len(parts) >= 3 else None
                if not session_id:
                    self._json(404, {"error": "missing session id"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = json.loads(self.rfile.read(length) or b"{}")
                    output_path = body["output_path"]
                    stream_id = body.get("stream_id")
                    export_session_csv(service.store, session_id, output_path,
                                       stream_id if stream_id is not None else None)
                    self._json(200, {"exported": output_path})
                except KeyError:
                    self._json(404, {"error": "session not found"})
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
            else:
                self._json(404, {"error": "not found"})

        def log_message(self, *_args):
            return

    return Handler


class ApiServer:
    def __init__(self, service, host: str = "127.0.0.1", port: int = 0,
                 static_root: Path | None = None, experiment_runtime=None):
        self.service = service
        self.static_root = static_root
        self.experiment_runtime = experiment_runtime
        self.server = ThreadingHTTPServer(
            (host, port),
            make_handler(service, static_root=static_root,
                        experiment_runtime=experiment_runtime),
        )
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       name="ground_station_api", daemon=True)

    @property
    def address(self):
        return self.server.server_address

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
