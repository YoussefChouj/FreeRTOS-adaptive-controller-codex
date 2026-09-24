"""Tests for GET /api/debug/memory endpoint."""
from __future__ import annotations

import json
import tracemalloc
import time

import pytest

from ground_station.livewatch.stream import StreamRange, StreamSchema
from ground_station.service import api as api_module
from ground_station.service.core import GroundStationService
from ground_station.service.storage import CsvRecorder, SessionStore


def _get_json(url: str):
    """GET a URL and return (status_code, parsed_json_or_dict)."""
    import http.client
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
    return resp.status, body_json


@pytest.fixture
def api_server():
    """Minimal API server on an ephemeral port with no tracing enabled."""
    schema = StreamSchema(1, 1, 4,
                          (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)
    service = GroundStationService(
        store=SessionStore(),
        schemas=[schema],
        source="sim",
        recorder=CsvRecorder(enabled=False),
    )
    service.start()
    api = api_module.ApiServer(service)
    api.start()
    try:
        yield api
    finally:
        api.stop()
        service.stop()


class TestDebugMemoryWithoutTracing:
    """Endpoint works when tracemalloc is not tracing."""

    def test_returns_200_without_tracing(self, api_server):
        base = "http://127.0.0.1:%d" % api_server.address[1]
        status, body = _get_json(base + "/api/debug/memory")
        assert status == 200

    def test_tracing_false_when_not_started(self, api_server):
        base = "http://127.0.0.1:%d" % api_server.address[1]
        status, body = _get_json(base + "/api/debug/memory")
        assert status == 200
        assert body.get("tracing") is False
        # always-on fields present
        assert "process" in body
        assert "gc_counts" in body
        assert "top_types" in body
        # traced/peak keys must not appear when not tracing
        assert "traced_mb" not in body
        assert "peak_mb" not in body
        assert "top" not in body
        assert "diff_top" not in body

    def test_process_field_is_null_or_dict(self, api_server):
        """process is null when psutil is not installed, dict when it is."""
        base = "http://127.0.0.1:%d" % api_server.address[1]
        _, body = _get_json(base + "/api/debug/memory")
        proc = body["process"]
        assert proc is None or isinstance(proc, dict)

    def test_gc_counts_present(self, api_server):
        base = "http://127.0.0.1:%d" % api_server.address[1]
        _, body = _get_json(base + "/api/debug/memory")
        assert isinstance(body["gc_counts"], list)

    def test_top_types_has_entries(self, api_server):
        base = "http://127.0.0.1:%d" % api_server.address[1]
        _, body = _get_json(base + "/api/debug/memory")
        tt = body["top_types"]
        assert isinstance(tt, dict)
        assert len(tt) > 0


class TestDebugMemoryWithTracing:
    """Endpoint returns memory data when tracemalloc is tracing."""

    def test_tracing_true_returns_traced_data(self):
        """Start tracing, hit the endpoint, get traced/peak/top/diff_top."""
        tracemalloc.start(25)  # capture up to 25 frames
        try:
            schema = StreamSchema(1, 1, 4,
                                  (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)
            service = GroundStationService(
                store=SessionStore(),
                schemas=[schema],
                source="sim",
                recorder=CsvRecorder(enabled=False),
            )
            service.start()
            api = api_module.ApiServer(service)
            api.start()
            try:
                base = "http://127.0.0.1:%d" % api.address[1]

                # First call with tracing on
                status, body = _get_json(base + "/api/debug/memory")
                assert status == 200
                assert body["tracing"] is True
                assert "traced_mb" in body
                assert isinstance(body["traced_mb"], (int, float))
                assert "peak_mb" in body
                assert isinstance(body["peak_mb"], (int, float))
                assert "top" in body
                assert isinstance(body["top"], list)
                assert len(body["top"]) <= 25
                for entry in body["top"]:
                    assert "size_kb" in entry
                    assert "count" in entry
                    assert "traceback" in entry
                    assert len(entry["traceback"]) <= 6

                # diff_top appears only after a second call
                _, body2 = _get_json(base + "/api/debug/memory")
                assert "diff_top" in body2
                assert isinstance(body2["diff_top"], list)
                assert len(body2["diff_top"]) <= 25
                for d in body2["diff_top"]:
                    assert "size_kb" in d
                    assert "count_diff" in d
                    assert "traceback" in d
            finally:
                api.stop()
                service.stop()
        finally:
            tracemalloc.stop()
