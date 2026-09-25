"""Tests for active_preset / preset_loaded_at exposure on GET /health and /state.

Covers:
  - Default (no preset) -> null in both fields
  - After set_active_preset -> name + timestamp surface in snapshot + HTTP
  - Runtime switch -> values update to the new preset
"""
import json
import os
import socket
import time
import http.client
from urllib.parse import urlparse

import pytest
from ground_station.livewatch.stream import StreamRange, StreamSchema
from ground_station.service.api import ApiServer
from ground_station.service.core import GroundStationService
from ground_station.service.storage import SessionStore


# Bypass Windows proxy for localhost
_orig_gai = socket.getaddrinfo


def _bypass_gai(host, port, *args, **kwargs):
    if host in ("127.0.0.1", "localhost"):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port))]
    return _orig_gai(host, port, *args, **kwargs)


socket.getaddrinfo = _bypass_gai


def _api_for(service):
    api = ApiServer(service)
    api.start()
    return api, "http://127.0.0.1:%d" % api.address[1]


def _get_json(url):
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
    return resp.status, json.loads(body)


def _schema():
    return StreamSchema(1, 1, 4,
                        (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)


def _svc():
    return GroundStationService(store=SessionStore(), schemas=[_schema()], source="sim")


# ── Unit tests on the service object ─────────────────────────────────────────


class TestServicePresetTracking:

    def test_default_no_preset(self):
        service = _svc()
        assert service.active_preset is None
        assert service.preset_loaded_at is None

    def test_set_active_preset_stores_name_and_time(self):
        service = _svc()
        ts = time.time()
        service.set_active_preset("flight_comprehensive", ts)
        assert service.active_preset == "flight_comprehensive"
        assert service.preset_loaded_at == ts

    def test_set_active_preset_defaults_timestamp(self):
        service = _svc()
        before = time.time()
        service.set_active_preset("mrac_characterization")
        after = time.time()
        assert service.active_preset == "mrac_characterization"
        assert before <= service.preset_loaded_at <= after

    def test_set_active_preset_clears_on_none(self):
        service = _svc()
        service.set_active_preset("flight_comprehensive", 100.0)
        assert service.active_preset == "flight_comprehensive"
        service.set_active_preset(None)
        assert service.active_preset is None
        assert service.preset_loaded_at is not None  # timestamp still set

    def test_set_active_preset_clears_name_only(self):
        """Setting name=None clears active_preset; timestamp updates to now."""
        service = _svc()
        service.set_active_preset("foo", 99.0)
        service.set_active_preset(None)
        assert service.active_preset is None
        assert service.preset_loaded_at is not None  # timestamp updated to now


# ── HTTP endpoint tests ──────────────────────────────────────────────────────


class TestHealthPreset:
    """GET /health reports active_preset and preset_loaded_at."""

    def test_no_preset_returns_null(self):
        service = _svc()
        service.start()
        api, base = _api_for(service)
        try:
            status, body = _get_json(base + "/health")
            assert status == 200
            assert body["active_preset"] is None
            assert body["preset_loaded_at"] is None
        finally:
            api.stop()

    def test_with_preset_returns_name_and_timestamp(self):
        service = _svc()
        service.start()
        ts = time.time()
        service.set_active_preset("flight_comprehensive", ts)
        api, base = _api_for(service)
        try:
            status, body = _get_json(base + "/health")
            assert status == 200
            assert body["active_preset"] == "flight_comprehensive"
            assert body["preset_loaded_at"] == ts
        finally:
            api.stop()

    def test_runtime_switch_updates_health(self):
        service = _svc()
        service.start()
        service.set_active_preset("initial", 1000.0)
        api, base = _api_for(service)
        try:
            _, body = _get_json(base + "/health")
            assert body["active_preset"] == "initial"
            # Switch preset
            service.set_active_preset("switched", 2000.0)
            _, body = _get_json(base + "/health")
            assert body["active_preset"] == "switched"
            assert body["preset_loaded_at"] == 2000.0
        finally:
            api.stop()


class TestStatePreset:
    """GET /state reports active_preset and preset_loaded_at at top level."""

    def test_no_preset_returns_null(self):
        service = _svc()
        service.start()
        api, base = _api_for(service)
        try:
            status, body = _get_json(base + "/state")
            assert status == 200
            assert body["active_preset"] is None
            assert body["preset_loaded_at"] is None
            # Must still have the standard fields
            assert "session_id" in body
        finally:
            api.stop()

    def test_with_preset_returns_name_and_timestamp(self):
        service = _svc()
        service.start()
        ts = time.time()
        service.set_active_preset("thrust_validation", ts)
        api, base = _api_for(service)
        try:
            status, body = _get_json(base + "/state")
            assert status == 200
            assert body["active_preset"] == "thrust_validation"
            assert body["preset_loaded_at"] == ts
        finally:
            api.stop()

    def test_runtime_switch_updates_state(self):
        service = _svc()
        service.start()
        service.set_active_preset("alpha", 100.0)
        api, base = _api_for(service)
        try:
            _, body = _get_json(base + "/state")
            assert body["active_preset"] == "alpha"
            # Switch preset
            service.set_active_preset("beta", 200.0)
            _, body = _get_json(base + "/state")
            assert body["active_preset"] == "beta"
            assert body["preset_loaded_at"] == 200.0
        finally:
            api.stop()
