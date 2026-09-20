"""Tests for GET /api/symbols (DWARF symbol-name enumeration, C1).

Offline: the endpoint reads the firmware ELF's DWARF info (OBJ/JX_FLY.axf),
sends no frames and needs no bridge or network beyond localhost.
"""
import json

from ground_station.service.api import (
    SYMBOLS_DEFAULT_LIMIT,
    SYMBOLS_MAX_LIMIT,
    _symbols_payload,
)
# Importing the sibling test module installs the localhost proxy bypass and
# gives us the same HTTP/fixture helpers the rest of the service tests use.
from ground_station.service.tests.test_service import _get_json, service_fixture
from ground_station.service.api import ApiServer


def _start_api():
    service = service_fixture()
    service.start()
    api = ApiServer(service)
    api.start()
    return api, "http://127.0.0.1:%d" % api.address[1]


def test_symbols_unfiltered_returns_names_only():
    api, base = _start_api()
    try:
        status, body = _get_json(base + "/api/symbols")
        assert status == 200, body
        assert body["source"] == "dwarf"
        assert body["prefix"] == ""
        assert body["parent"] is None
        names = body["names"]
        assert isinstance(names, list) and names
        assert all(isinstance(n, str) for n in names)
        assert body["count"] == len(names)
        assert body["count"] == min(body["total"], SYMBOLS_DEFAULT_LIMIT)
        assert body["limit"] == SYMBOLS_DEFAULT_LIMIT
        assert body["truncated"] == (body["total"] > len(names))
        assert set(body["drillable"]).issubset(names)
        # Names only: addresses are intentionally never serialized.
        assert "address" not in json.dumps(body)
    finally:
        api.stop()


def test_symbols_prefix_filter():
    api, base = _start_api()
    try:
        status, body = _get_json(base + "/api/symbols?prefix=mrac_state")
        assert status == 200, body
        assert body["prefix"] == "mrac_state"
        assert names_all_startswith(body["names"], "mrac_state")
        assert "mrac_state" in body["names"]
        # A prefix matching nothing is an honest empty list, not an error.
        status, none_body = _get_json(base + "/api/symbols?prefix=zzz_nope")
        assert status == 200
        assert none_body["names"] == []
        assert none_body["total"] == 0
    finally:
        api.stop()


def names_all_startswith(names, prefix):
    return all(n.startswith(prefix) for n in names)


def test_symbols_limit_bounds():
    api, base = _start_api()
    try:
        status, body = _get_json(base + "/api/symbols?limit=3")
        assert status == 200, body
        assert body["limit"] == 3
        assert len(body["names"]) == 3
        assert body["count"] == 3
        assert body["truncated"] is True
        assert body["total"] > 3

        # Asking for more than the hard cap clamps to SYMBOLS_MAX_LIMIT.
        status, capped = _get_json(base + "/api/symbols?limit=999999")
        assert status == 200
        assert capped["limit"] == SYMBOLS_MAX_LIMIT
        assert len(capped["names"]) <= SYMBOLS_MAX_LIMIT

        # A non-numeric limit falls back to the default.
        status, bad = _get_json(base + "/api/symbols?limit=abc")
        assert status == 200
        assert bad["limit"] == SYMBOLS_DEFAULT_LIMIT
    finally:
        api.stop()


def test_symbols_parent_drill_struct_and_array():
    api, base = _start_api()
    try:
        status, body = _get_json(base + "/api/symbols?parent=s_ekf")
        assert status == 200, body
        assert body["parent"] == "s_ekf"
        assert body["parent_kind"] == "struct"
        assert "s_ekf.x" in body["names"]
        assert names_all_startswith(body["names"], "s_ekf.")

        status, arr = _get_json(base + "/api/symbols?parent=s_ekf.x")
        assert status == 200, arr
        assert arr["parent_kind"] == "array"
        assert arr["names"] == ["s_ekf.x[%d]" % i for i in range(9)]
        # Scalar leaves are not themselves drillable.
        assert not any(arr["drillable"].values())
    finally:
        api.stop()


def test_symbols_unknown_parent_returns_404():
    api, base = _start_api()
    try:
        status, body = _get_json(base + "/api/symbols?parent=no_such_global_xyz")
        assert status == 404, body
        assert "error" in body
    finally:
        api.stop()


def test_symbols_stale_json_fallback(monkeypatch):
    # Force the live resolver to be unavailable; the endpoint must fall
    # back to the offline JSON catalog, names only, flagged as potentially
    # stale. Parent drill-down is unsupported in that mode.
    import ground_station.service.api as api_mod

    monkeypatch.setattr(
        api_mod, "_service_symbol_resolver",
        lambda service: (None, "simulated ELF unavailable"),
    )
    service = service_fixture()
    status, body = _symbols_payload(service, "", "", SYMBOLS_DEFAULT_LIMIT)
    assert status == 200, body
    assert body["source"] == "stale_json_catalog"
    assert body["generated_at"]
    assert body["warning"]
    assert "may not match the running build" in body["warning"]
    assert body["names"] and all(isinstance(n, str) for n in body["names"])
    assert "address" not in json.dumps(body)

    status, blocked = _symbols_payload(service, "", "some.path",
                                       SYMBOLS_DEFAULT_LIMIT)
    assert status == 503
    assert blocked["source"] == "unavailable"
