"""The bridge re-sends its boot subscribe when the FC forgets it.

A powered-off FC comes back with no subscribe slots and only sends the
16-byte attitude fallback frame. The bridge then kept showing 113 of 116
slot-0 values frozen, because it subscribed once at start(). The watchdog
re-sends the subscribe when frames arrive but no subscribe data does.
"""
from __future__ import annotations

import pytest

from ground_station.comm import wifi_bridge as wb
from ground_station.comm.wifi_bridge import WifiBridge


@pytest.fixture
def bridge(monkeypatch):
    b = WifiBridge(vofa_enabled=False)
    sent = []

    class _SyncThread:
        def __init__(self, target, args=(), **_kw):
            self._run = lambda: target(*args)

        def start(self):
            self._run()

    monkeypatch.setattr(wb.threading, "Thread", _SyncThread)
    monkeypatch.setattr(b, "_request_slot0_schema", lambda layout: sent.append(layout))
    clock = [1000.0]
    monkeypatch.setattr(wb.time, "monotonic", lambda: clock[0])
    b._resubscribe_layout = "dashboard"
    b._last_resubscribe = clock[0]
    return b, sent, clock


def test_no_resend_without_a_boot_subscribe(bridge):
    b, sent, clock = bridge
    b._resubscribe_layout = None
    clock[0] += 100
    b._check_resubscribe()
    assert sent == []


def test_no_resend_while_subscribe_data_is_fresh(bridge):
    b, sent, clock = bridge
    clock[0] += 100
    b._last_stream_rx = clock[0] - 1.0
    b._check_resubscribe()
    assert sent == []


def test_stale_stream_resends_once_then_waits(bridge):
    b, sent, clock = bridge
    b._last_stream_rx = 0.0
    clock[0] += WifiBridge._RESUBSCRIBE_EVERY_S
    b._check_resubscribe()
    b._check_resubscribe()
    assert sent == ["dashboard"]
    clock[0] += WifiBridge._RESUBSCRIBE_EVERY_S - 0.1
    b._check_resubscribe()
    assert sent == ["dashboard"]
    clock[0] += 0.2
    b._check_resubscribe()
    assert sent == ["dashboard", "dashboard"]


def test_frame_a_triggers_resubscribe_check(monkeypatch):
    """Frame A (0xAA 0xAA) must call _check_resubscribe so the watchdog
    fires even when the FC sends only Frame A and the initial subscribe
    request is lost.  Without this, the dashboard shows streams=empty
    while the sidebar has stale Frame A data.
    """
    b = WifiBridge(vofa_enabled=False)
    sent = []

    class _SyncThread:
        def __init__(self, target, args=(), **kw):
            self._run = lambda: target(*args)

        def start(self):
            self._run()

    monkeypatch.setattr(wb.threading, "Thread", _SyncThread)
    monkeypatch.setattr(b, "_request_slot0_schema", lambda layout: sent.append(layout))
    clock = [1000.0]
    monkeypatch.setattr(wb.time, "monotonic", lambda: clock[0])
    b._resubscribe_layout = "dashboard"
    b._last_resubscribe = 0.0  # force watchdog past rate-limit

    # Feed a 68-byte Frame A buffer.
    buf = bytearray(68)
    buf[0] = 0xAA
    buf[1] = 0xAA
    buf[2] = 0x01
    result = b._parse_one(buf)
    assert result[0] == "a"
    assert sent == ["dashboard"], "Frame A must trigger resubscribe when stream is stale"


def test_subscribe_data_stops_watchdog(monkeypatch):
    """Once subscribe data arrives the watchdog must stop firing."""
    b = WifiBridge(vofa_enabled=False)
    sent = []

    class _SyncThread:
        def __init__(self, target, args=(), **kw):
            self._run = lambda: target(*args)

        def start(self):
            self._run()

    monkeypatch.setattr(wb.threading, "Thread", _SyncThread)
    monkeypatch.setattr(b, "_request_slot0_schema", lambda layout: sent.append(layout))
    clock = [1000.0]
    monkeypatch.setattr(wb.time, "monotonic", lambda: clock[0])
    b._resubscribe_layout = "dashboard"
    b._last_resubscribe = 0.0
    b._last_stream_rx = 0.0

    # First call: watchdog fires because _last_stream_rx is stale.
    b._check_resubscribe()
    assert sent == ["dashboard"]

    # Simulate subscribe data arrival.
    clock[0] += 5.0
    b._last_stream_rx = clock[0]

    # Second call: no resubscribe because subscribe data is fresh.
    b._check_resubscribe()
    assert sent == ["dashboard"]
