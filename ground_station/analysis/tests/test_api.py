"""Tests for ground_station.service.api experiment and session endpoints."""
from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import http.client
import socket

# Patch socket.getaddrinfo to bypass Windows proxy for localhost
_orig_gai = socket.getaddrinfo
def _bypass_gai(host, port, *args, **kwargs):
    if host in ("127.0.0.1", "localhost"):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (host, port))]
    return _orig_gai(host, port, *args, **kwargs)
socket.getaddrinfo = _bypass_gai


def _http_get(url: str) -> tuple[int, dict]:
    """Perform a direct HTTP GET over a raw socket; bypasses system proxy."""
    import io
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host, port = parsed.hostname, parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    conn = http.client.HTTPConnection(host, port)
    conn.connect()
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    try:
        body_json = json.loads(body)
    except Exception:
        body_json = {"raw": body.decode(errors="replace")}
    if 200 <= resp.status < 300:
        return resp.status, body_json
    raise urllib.error.HTTPError(url, resp.status, resp.reason,
                                dict(resp.getheaders()),
                                io.BytesIO(body))


def _http_post(url: str, body: dict) -> tuple[int, dict]:
    """Perform a direct HTTP POST over a raw socket; bypasses system proxy."""
    import io
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host, port = parsed.hostname, parsed.port or 80
    path = parsed.path or "/"
    data = json.dumps(body).encode()
    conn = http.client.HTTPConnection(host, port)
    conn.connect()
    conn.request("POST", path, data, {"Content-Type": "application/json"})
    resp = conn.getresponse()
    body = resp.read()
    try:
        body_json = json.loads(body)
    except Exception:
        body_json = {"raw": body.decode(errors="replace")}
    if 200 <= resp.status < 300:
        return resp.status, body_json
    raise urllib.error.HTTPError(url, resp.status, resp.reason,
                                dict(resp.getheaders()),
                                io.BytesIO(body))


# Alias for backward compatibility with existing test methods
def _get(url: str) -> tuple[int, dict]:
    """Perform a GET request; returns (status_code, body_dict)."""
    try:
        return _http_get(url)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _post(url: str, body: dict) -> tuple[int, dict]:
    """Perform a POST request; returns (status_code, body_dict)."""
    try:
        return _http_post(url, body)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


from ground_station.analysis.session import export_session_csv
from ground_station.platform.experiments import ExperimentRuntime
from ground_station.service.api import ApiServer
from ground_station.service.core import GroundStationService
from ground_station.service.storage import SessionStore


def service_fixture():
    from ground_station.livewatch.stream import StreamRange, StreamSchema
    schema = StreamSchema(1, 1, 4,
                          (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)
    service = GroundStationService(store=SessionStore(), schemas=[schema], source="sim")
    return service


class TestSessionsEndpoints:
    def test_get_sessions_lists_all_sessions(self):
        service = service_fixture()
        service.start()
        runtime = ExperimentRuntime({"kp": 1.0})
        api = ApiServer(service, experiment_runtime=runtime)
        api.start()
        try:
            base = "http://127.0.0.1:%d" % api.address[1]
            status, body = _get(base + "/sessions")
            assert status == 200
            assert isinstance(body, list)
            assert any(s["id"] == service.session_id for s in body)
        finally:
            api.stop()

    def test_get_session_detail(self):
        service = service_fixture()
        service.start()
        api = ApiServer(service)
        api.start()
        try:
            base = "http://127.0.0.1:%d" % api.address[1]
            status, body = _get(base + "/sessions/" + service.session_id)
            assert status == 200
            assert body["id"] == service.session_id
        finally:
            api.stop()

    def test_get_session_not_found(self):
        service = service_fixture()
        service.start()
        api = ApiServer(service)
        api.start()
        try:
            base = "http://127.0.0.1:%d" % api.address[1]
            status, _ = _get(base + "/sessions/nonexistent")
            assert status == 404
        finally:
            api.stop()

    def test_get_session_records(self):
        service = service_fixture()
        service.start()
        api = ApiServer(service)
        api.start()
        try:
            base = "http://127.0.0.1:%d" % api.address[1]
            status, body = _get(base + "/sessions/" + service.session_id + "/records")
            assert status == 200
            assert body["session_id"] == service.session_id
            assert "records" in body
        finally:
            api.stop()


class TestExperimentsEndpoints:
    def test_get_experiments_empty(self):
        service = service_fixture()
        service.start()
        runtime = ExperimentRuntime({"kp": 1.0})
        api = ApiServer(service, experiment_runtime=runtime)
        api.start()
        try:
            base = "http://127.0.0.1:%d" % api.address[1]
            status, body = _get(base + "/experiments")
            assert status == 200
            assert body == []
        finally:
            api.stop()

    def test_start_experiment(self):
        service = service_fixture()
        service.start()
        runtime = ExperimentRuntime({"kp": 1.0, "ki": 0.5})
        api = ApiServer(service, experiment_runtime=runtime)
        api.start()
        try:
            base = "http://127.0.0.1:%d" % api.address[1]
            status, body = _post(base + "/experiments", {
                "name": "test_run",
                "settle_ticks": 5,
                "measure_ticks": 10,
                "parameters": {"kp": 2.0},
            })
            assert status == 201
            assert body["run_id"] == "test_run"
        finally:
            api.stop()

    def test_get_active_experiment(self):
        service = service_fixture()
        service.start()
        runtime = ExperimentRuntime({"kp": 1.0})
        runtime.start("my_run", {"kp": 3.0}, settle_ticks=5, measure_ticks=10)
        api = ApiServer(service, experiment_runtime=runtime)
        api.start()
        try:
            base = "http://127.0.0.1:%d" % api.address[1]
            status, body = _get(base + "/experiments/my_run")
            assert status == 200
            assert body["name"] == "my_run"
            assert body["state"] == "settling"
        finally:
            api.stop()

    def test_get_experiment_not_found(self):
        service = service_fixture()
        service.start()
        runtime = ExperimentRuntime({"kp": 1.0})
        api = ApiServer(service, experiment_runtime=runtime)
        api.start()
        try:
            base = "http://127.0.0.1:%d" % api.address[1]
            status, _ = _get(base + "/experiments/nonexistent")
            assert status == 404
        finally:
            api.stop()

    def test_abort_experiment(self):
        service = service_fixture()
        service.start()
        runtime = ExperimentRuntime({"kp": 1.0})
        runtime.start("my_run", {"kp": 3.0}, settle_ticks=5, measure_ticks=10)
        api = ApiServer(service, experiment_runtime=runtime)
        api.start()
        try:
            base = "http://127.0.0.1:%d" % api.address[1]
            status, body = _post(base + "/experiments/my_run/abort", {})
            assert status == 200
            assert body["aborted"] == "my_run"
            # after abort the active reference is cleared; GET /experiments returns []
            status2, body2 = _get(base + "/experiments")
            assert body2 == []
        finally:
            api.stop()


class TestArtifactsEndpoint:
    def test_get_artifacts_empty(self):
        service = service_fixture()
        service.start()
        api = ApiServer(service)
        api.start()
        try:
            base = "http://127.0.0.1:%d" % api.address[1]
            status, body = _get(base + "/artifacts")
            assert status == 200
            assert "artifacts" in body
        finally:
            api.stop()


class TestAnalysisCompareEndpoint:
    def test_compare_missing_params(self):
        service = service_fixture()
        service.start()
        api = ApiServer(service)
        api.start()
        try:
            base = "http://127.0.0.1:%d" % api.address[1]
            status, _ = _get(base + "/analysis/compare")
            assert status == 400
        finally:
            api.stop()


class TestExportEndpoint:
    def test_export_session(self):
        service = service_fixture()
        service.start()
        # Seed some telemetry
        service.store.append_telemetry(service.session_id, 1, 0,
                                      {"altitude": 1.0}, source_time_ms=100)
        api = ApiServer(service)
        api.start()
        try:
            base = "http://127.0.0.1:%d" % api.address[1]
            with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
                path = f.name
            status, body = _post(base + "/sessions/" + service.session_id + "/export", {
                "output_path": path,
            })
            assert status == 200
            assert body["exported"] == path
        finally:
            api.stop()

    def test_export_missing_session(self):
        service = service_fixture()
        service.start()
        api = ApiServer(service)
        api.start()
        try:
            base = "http://127.0.0.1:%d" % api.address[1]
            with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
                path = f.name
            status, body = _post(base + "/sessions/bad/export", {
                "output_path": path,
            })
            assert status == 404
        finally:
            api.stop()
