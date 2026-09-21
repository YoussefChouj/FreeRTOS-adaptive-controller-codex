"""Unit tests for dash_verify helper logic.

dash_verify.py is a live-browser script, but its pure helpers are tested
offline here — nothing connects to port 8081.
"""
from ground_station.service import dash_verify


def _state(keys_by_stream):
    return {"streams": {sid: {"values": dict(keys)}
                        for sid, keys in keys_by_stream.items()}}


def test_position_keys_match_real_panel_bindings():
    state = _state({"0": [("c.earth_x", 1.0), ("c.earth_y", 2.0),
                          ("c.altitude", 3.0)]})
    assert dash_verify.position_keys_in_state(state) == [
        "c.earth_x", "c.earth_y"]


def test_position_keys_empty_when_absent():
    # Phantom keys the old heuristic hunted for must not count.
    state = _state({"0": [("ekf.pos_x", 1.0), ("pos_y", 2.0),
                          ("c.altitude", 3.0)]})
    assert dash_verify.position_keys_in_state(state) == []


def test_position_keys_handles_empty_state():
    assert dash_verify.position_keys_in_state({}) == []
    assert dash_verify.position_keys_in_state({"streams": {}}) == []
