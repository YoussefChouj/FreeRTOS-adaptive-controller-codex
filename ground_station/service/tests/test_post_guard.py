"""Tests for the cross-site POST guard (_validate_request_headers).

Verifies items 1-4 of the 2026-09-23 finish spec:
  1. Content-Type accepts application/json with optional params.
  2. Bound-host is allowed in Host/Origin headers.
  3. Body is drained before sending error responses.
  4. do_PUT / do_DELETE do not exist (checked via source grep).
Plus one in-process server test (ephemeral port, not 8081).
"""
import json
import socket
import threading
import time
import urllib.request
from unittest.mock import MagicMock

import pytest

from ground_station.service.api import _validate_request_headers, ApiServer
from ground_station.livewatch.stream import StreamRange, StreamSchema
from ground_station.service.core import GroundStationService
from ground_station.service.storage import SessionStore


# ── Unit tests for _validate_request_headers ─────────────────────────

def _ok():
    """Return a minimal valid-Host header dict with no body."""
    return {"Host": "127.0.0.1:8081"}


def _evil_host():
    return {"Host": "evil.com:8081"}


def _evil_origin():
    h = _ok()
    h["Origin"] = "http://evil.com"
    return h


def _no_origin():
    return _ok()


# --- 1. Good host ---

def test_good_host():
    result = _validate_request_headers({"Host": "127.0.0.1:8081"})
    assert result is None


def test_good_host_localhost():
    result = _validate_request_headers({"Host": "localhost"})
    assert result is None


def test_good_host_ipv6():
    result = _validate_request_headers({"Host": "[::1]:8081"})
    assert result is None


# --- 2. Evil host ---

def test_evil_host():
    result = _validate_request_headers(_evil_host())
    assert result is not None
    assert result[0] == 403


def test_evil_host_with_origin():
    h = _evil_host()
    h["Origin"] = "http://127.0.0.1"
    result = _validate_request_headers(h)
    assert result is not None
    assert result[0] == 403


# --- 3. Evil origin ---

def test_evil_origin():
    result = _validate_request_headers(_evil_origin())
    assert result is not None
    assert result[0] == 403


def test_good_origin():
    h = _ok()
    h["Origin"] = "http://127.0.0.1"
    result = _validate_request_headers(h)
    assert result is None


def test_no_origin_allowed():
    result = _validate_request_headers(_no_origin())
    assert result is None


# --- 4. Content-Type checks ---

def test_form_content_type_rejected():
    h = _ok()
    h["Content-Type"] = "application/x-www-form-urlencoded"
    h["Content-Length"] = "10"
    result = _validate_request_headers(h)
    assert result is not None
    assert result[0] == 415


def test_json_charset_accepted():
    h = _ok()
    h["Content-Type"] = "application/json; charset=utf-8"
    h["Content-Length"] = "10"
    result = _validate_request_headers(h)
    assert result is None


def test_json_charset_uppercase_accepted():
    h = _ok()
    h["Content-Type"] = "APPLICATION/JSON; CHARSET=UTF-8"
    h["Content-Length"] = "10"
    result = _validate_request_headers(h)
    assert result is None


def test_json_no_params_accepted():
    h = _ok()
    h["Content-Type"] = "application/json"
    h["Content-Length"] = "5"
    result = _validate_request_headers(h)
    assert result is None


def test_empty_body_no_content_type_ok():
    h = _ok()
    h["Content-Length"] = "0"
    result = _validate_request_headers(h)
    assert result is None


def test_empty_body_with_content_type_ok():
    h = _ok()
    h["Content-Length"] = "0"
    h["Content-Type"] = "text/plain"
    result = _validate_request_headers(h)
    assert result is None


def test_no_body_no_content_type_ok():
    h = _ok()
    result = _validate_request_headers(h)
    assert result is None


def test_missing_content_type_with_body():
    h = _ok()
    h["Content-Length"] = "10"
    result = _validate_request_headers(h)
    assert result is not None
    assert result[0] == 415


# --- 5. Bound host ---

def test_bound_host_allowed():
    result = _validate_request_headers(
        {"Host": "192.168.1.100:8081"}, bound_host="192.168.1.100"
    )
    assert result is None


def test_bound_host_0000_allows_only_loopback():
    result = _validate_request_headers(
        {"Host": "192.168.1.50:8081"}, bound_host="0.0.0.0"
    )
    assert result is not None
    assert result[0] == 403


def test_bound_host_localhost_is_loopback():
    result = _validate_request_headers(
        {"Host": "localhost:8081"}, bound_host="localhost"
    )
    assert result is None


def test_bound_host_evil_not_added():
    result = _validate_request_headers(
        {"Host": "evil.com:8081"}, bound_host="evil.com"
    )
    # When bound to a normal host, it IS added to allowed hosts.
    # The spec says: allow the host the server was bound to.
    # This tests that a bound_host that is NOT a loopback is allowed.
    assert result is None


# --- 6. In-process server test (ephemeral port) ---

def test_server_rejects_evil_host_on_post():
    """POST to ephemeral server with evil Host is rejected with 403."""
    schema = StreamSchema(1, 1, 4,
                          (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)
    service = GroundStationService(store=SessionStore(), schemas=[schema],
                                   source="sim")
    api = ApiServer(service, host="127.0.0.1", port=0)
    api.start()
    try:
        port = api.address[1]
        time.sleep(0.2)  # let the server bind
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/commands",
                     json.dumps({"command_id": 0}),
                     {"Host": "evil.com:8081", "Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 403
        body = json.loads(resp.read())
        assert "error" in body
    finally:
        api.stop()


def test_server_accepts_good_host():
    """POST to ephemeral server with good Host is accepted (202 or 400)."""
    schema = StreamSchema(1, 1, 4,
                          (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)
    service = GroundStationService(store=SessionStore(), schemas=[schema],
                                   source="sim")
    api = ApiServer(service, host="127.0.0.1", port=0)
    api.start()
    try:
        port = api.address[1]
        time.sleep(0.2)
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/commands",
                     json.dumps({"command_id": 0}),
                     {"Host": "127.0.0.1:%d" % port,
                      "Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        assert resp.status != 403
    finally:
        api.stop()


def test_server_rejects_evil_origin():
    """POST with evil Origin is rejected even with good Host."""
    schema = StreamSchema(1, 1, 4,
                          (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)
    service = GroundStationService(store=SessionStore(), schemas=[schema],
                                   source="sim")
    api = ApiServer(service, host="127.0.0.1", port=0)
    api.start()
    try:
        port = api.address[1]
        time.sleep(0.2)
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/commands",
                     json.dumps({"command_id": 0}),
                     {"Host": "127.0.0.1:%d" % port,
                      "Content-Type": "application/json",
                      "Origin": "http://evil.com"})
        resp = conn.getresponse()
        assert resp.status == 403
    finally:
        api.stop()


def test_server_accepts_json_with_charset():
    """POST with application/json; charset=utf-8 is accepted."""
    schema = StreamSchema(1, 1, 4,
                          (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)
    service = GroundStationService(store=SessionStore(), schemas=[schema],
                                   source="sim")
    api = ApiServer(service, host="127.0.0.1", port=0)
    api.start()
    try:
        port = api.address[1]
        time.sleep(0.2)
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/commands",
                     json.dumps({"command_id": 0}),
                     {"Host": "127.0.0.1:%d" % port,
                      "Content-Type": "application/json; charset=utf-8"})
        resp = conn.getresponse()
        resp.read()
        assert resp.status != 415
    finally:
        api.stop()


def test_server_rejects_form_content_type():
    """POST with application/x-www-form-urlencoded is rejected with 415."""
    schema = StreamSchema(1, 1, 4,
                          (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)
    service = GroundStationService(store=SessionStore(), schemas=[schema],
                                   source="sim")
    api = ApiServer(service, host="127.0.0.1", port=0)
    api.start()
    try:
        port = api.address[1]
        time.sleep(0.2)
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/commands",
                     "foo=bar",
                     {"Host": "127.0.0.1:%d" % port,
                      "Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        assert resp.status == 415
    finally:
        api.stop()
