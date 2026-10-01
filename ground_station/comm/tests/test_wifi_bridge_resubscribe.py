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


def test_slot_watchdog_stale_resend_and_recovery(monkeypatch):
    """Per-slot watchdog: stale detection, rate-limited re-send, frame recovery,
    and divider-0 exclusion."""
    b = WifiBridge(vofa_enabled=False)
    sendto_log = []

    class _SyncThread:
        def __init__(self, target, args=(), **kw):
            self._run = lambda: target(*args)

        def start(self):
            self._run()

    monkeypatch.setattr(wb.threading, "Thread", _SyncThread)
    monkeypatch.setattr(b, "_request_slot0_schema", lambda layout: None)
    clock = [1000.0]
    monkeypatch.setattr(wb.time, "monotonic", lambda: clock[0])

    # Fake socket recording sendto calls
    class _FakeSock:
        def sendto(self, data, addr):
            sendto_log.append((data, addr))
    b._wifi_send = _FakeSock()
    b._wifi_host = "192.168.4.1"
    b._wifi_port = 14550

    # Register requests for slots 0-3 at 20 Hz
    for slot in range(4):
        req_bytes = b"REQ_SLOT_%d" % slot
        b._slot_requests[slot] = (20.0, req_bytes, (f"var{slot}",))
        b._last_slot_rx[slot] = clock[0]

    # Only slots 1-3 receive frames over the next 4 seconds
    clock[0] += 4.0
    for slot in (1, 2, 3):
        b._last_slot_rx[slot] = clock[0]
    # Slot 0 is stale (last_rx = 1000.0, now = 1004.0, threshold = max(3.0, 5/20) = 3.0)

    # Set resubscribe layout so _check_resubscribe's link-wide path runs
    b._resubscribe_layout = "dashboard"
    b._last_resubscribe = clock[0]  # prevent link-wide re-send
    b._last_stream_rx = clock[0]

    sendto_log.clear()
    b._check_resubscribe()
    # Should have re-sent slot 0's bytes exactly once
    assert len(sendto_log) == 1
    assert sendto_log[0][0] == b"REQ_SLOT_0"
    # Slot 0 is stale, 1-3 are not
    with b._stream_lock:
        assert b._slot_states.get(0) == "stale"
        for s in (1, 2, 3):
            assert b._slot_states.get(s) != "stale"

    # Within 5 s of slot 0's last resend: no more re-sends for slot 0
    clock[0] += 4.0
    # Keep slots 1-3 fresh
    for s in (1, 2, 3):
        b._last_slot_rx[s] = clock[0]
    sendto_log.clear()
    b._check_resubscribe()
    assert len(sendto_log) == 0

    # After 5 s since slot 0's last resend: re-sends again
    clock[0] += 2.0  # total 6s since last resend of slot 0
    for s in (1, 2, 3):
        b._last_slot_rx[s] = clock[0]
    sendto_log.clear()
    b._check_resubscribe()
    assert len(sendto_log) == 1
    assert sendto_log[0][0] == b"REQ_SLOT_0"

    # Decoded slot-0 frame returns it to "streaming"
    b._last_slot_rx[0] = clock[0]
    with b._stream_lock:
        b._slot_states[0] = "streaming"
    sendto_log.clear()
    clock[0] += 10.0  # wait past both watchdog intervals
    # Keep all slots fresh
    for s in range(4):
        b._last_slot_rx[s] = clock[0]
    b._last_stream_rx = clock[0]  # keep link-wide fresh
    b._last_resubscribe = clock[0]
    b._check_resubscribe()
    assert len(sendto_log) == 0  # no re-sends, all slots are fresh

    # A divider-0 slot is never re-sent
    b._slot_requests[0] = (0.0, b"REQ_SLOT_0", ("var0",))
    b._last_slot_rx[0] = 0.0
    clock[0] += 100.0
    for s in (1, 2, 3):
        b._last_slot_rx[s] = clock[0]
    b._last_stream_rx = clock[0]
    b._last_resubscribe = clock[0]
    sendto_log.clear()
    b._check_resubscribe()
    assert len(sendto_log) == 0

