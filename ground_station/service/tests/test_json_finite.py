"""Responses must be parseable by a browser, not just by Python.

``json.dumps`` emits bare ``NaN`` / ``Infinity`` tokens by default.  Python
round-trips them happily, so a server-side assertion on the decoded body
passes; ``JSON.parse`` rejects the document outright.  One uninitialised
float channel in one telemetry slot was therefore enough to make the whole
``/state`` response unreadable to the shell, which is why the sidebar showed
"NOT PUBLISHED" while the drone was connected and streaming at <1% loss.

These tests decode with ``parse_constant``, which is the only way to make
Python as strict as a browser.
"""
import json

import pytest

from ground_station.service.api import _json_safe


def _strict_loads(raw):
    """Decode like a browser: any NaN/Infinity token is a hard error."""
    def reject(token):
        raise ValueError("non-JSON constant in response: %s" % token)
    return json.loads(raw, parse_constant=reject)


def test_bare_json_dumps_is_what_the_browser_rejects():
    """Establishes the premise: this is the bug, before any of our code runs."""
    raw = json.dumps({"ch1.5": float("nan")})
    assert raw == '{"ch1.5": NaN}'
    with pytest.raises(ValueError):
        _strict_loads(raw)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_floats_become_null(bad):
    assert _json_safe(bad) is None
    assert _strict_loads(json.dumps(_json_safe({"v": bad}))) == {"v": None}


def test_nested_structures_are_cleaned_throughout():
    """The real payload is streams -> values -> float, several levels down."""
    payload = {
        "streams": {
            "0": {"values": {"ch0.1": float("nan"), "ch0.2": 1.5},
                  "loss_pct": 0.405},
            "1": {"values": {"c.gyro_x": float("inf")},
                  "history": [1.0, float("nan"), 3.0]},
        },
        "schema_id": "r1-s1-9F32E2EA",
        "samples": 32974,
    }
    out = _strict_loads(json.dumps(_json_safe(payload)))
    assert out["streams"]["0"]["values"] == {"ch0.1": None, "ch0.2": 1.5}
    assert out["streams"]["1"]["values"] == {"c.gyro_x": None}
    assert out["streams"]["1"]["history"] == [1.0, None, 3.0]
    # Everything finite survives untouched -- the fix must not flatten data.
    assert out["schema_id"] == "r1-s1-9F32E2EA"
    assert out["samples"] == 32974
    assert out["streams"]["0"]["loss_pct"] == 0.405


def test_booleans_and_ints_are_not_mangled():
    """``bool`` is not ``float``; ``int`` must not be coerced either."""
    out = _json_safe({"connected": True, "off": False, "n": 7, "s": "x",
                      "none": None})
    assert out == {"connected": True, "off": False, "n": 7, "s": "x",
                   "none": None}
    assert out["n"] == 7 and isinstance(out["n"], int)


def test_live_state_shape_survives_the_round_trip():
    """A slot whose every value is NaN still yields a parseable document.

    Before the fix this response parsed in Python and threw in the browser,
    so no amount of server-side testing could have caught it.
    """
    payload = {"streams": {"1": {"values": {
        "ch1.%d" % i: float("nan") for i in range(87)}}}}
    out = _strict_loads(json.dumps(_json_safe(payload)))
    assert len(out["streams"]["1"]["values"]) == 87
    assert set(out["streams"]["1"]["values"].values()) == {None}


# ── through the real HTTP route ──────────────────────────────────────

def test_a_real_response_is_browser_parseable_only_because_of_the_guard(
        monkeypatch):
    """Drive the actual ApiServer, with and without ``_json_safe``.

    The helper tests above prove the mapping; this one proves the mapping is
    actually wired into the response path, and -- by disabling it -- that the
    route really would emit a bare ``NaN`` without it.  That is the red half
    of the proof, which no server-side ``json.loads`` could have shown.
    """
    import time
    import urllib.request

    from ground_station.livewatch.stream import StreamRange, StreamSchema
    from ground_station.service import api as api_mod
    from ground_station.service.core import GroundStationService
    from ground_station.service.storage import SessionStore

    schema = StreamSchema(1, 1, 4,
                          (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)
    service = GroundStationService(store=SessionStore(), schemas=[schema],
                                   source="sim")
    srv = api_mod.ApiServer(service, host="127.0.0.1", port=0)
    srv.start()
    time.sleep(0.2)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = "http://127.0.0.1:%d/state" % srv.address[1]
    try:
        with service._state_lock:
            service._streams[0] = {"values": {"ch0.1": float("nan")}}

        # Red: restore the original serialiser exactly -- no sanitising pass
        # and json.dumps' default allow_nan=True -- and the route emits a
        # token no browser accepts.  (Disabling only _json_safe is not the
        # old behaviour: allow_nan=False then raises server-side, which is
        # the second half of the guard doing its job.)
        real_dumps = json.dumps
        monkeypatch.setattr(api_mod, "_json_safe", lambda v: v)
        monkeypatch.setattr(api_mod.json, "dumps", lambda o, **kw: real_dumps(
            o, **{k: v for k, v in kw.items() if k != "allow_nan"}))
        raw = opener.open(url, timeout=5).read().decode()
        assert "NaN" in raw
        with pytest.raises(ValueError):
            _strict_loads(raw)

        # Green: with it, the same state parses under browser rules.
        monkeypatch.undo()
        raw = opener.open(url, timeout=5).read().decode()
        assert "NaN" not in raw
        assert _strict_loads(raw)["streams"]["0"]["values"]["ch0.1"] is None
    finally:
        srv.stop()
