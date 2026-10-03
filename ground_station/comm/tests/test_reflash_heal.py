"""WP-22: a reflash (or FC reboot) heals the streams without restarting 8081.

The fake resolver plays the axf: a "reflash" bumps the axf key and moves every variable by 12 B, the way a
firmware change shifts .bss. The FC is played by hand-built 0x08 schema replies.
"""
from __future__ import annotations

import struct
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import ground_station.comm.wifi_bridge as wb
from ground_station.comm.wifi_bridge import WifiBridge
from ground_station.livewatch.stream import build_stream_request
from ground_station.service.campaign_live import wait_streams_named

BASE = {"imu_data.rol": 0x20000100, "imu_data.pit": 0x20000200}
NAMES = list(BASE)
SLOT, DIV = 3, 5


class FakeResolver:
    def __init__(self):
        self.shift = 0

    def resolve(self, name):
        return SimpleNamespace(address=BASE[name] + self.shift, size=4, fmt="f")


def schema_frame(slot, divider, ranges, transport=1):
    """A 0x08 reply as Subscribe_BuildSchema lays it out; ``ranges`` = [(address, size, count)]."""
    body = b"".join(struct.pack("<IHH", a, s, c) for a, s, c in ranges)
    total = sum(s * c for _, s, c in ranges)
    payload = bytes([divider, transport, slot, total >> 8, total & 0xFF]) + body
    head = bytes([0xAA, 0xBB, 0x08, len(payload) >> 8, len(payload) & 0xFF, len(ranges)])
    xor = 0
    for b in head + payload:
        xor ^= b
    return head + payload + bytes([xor])


@pytest.fixture
def rig(monkeypatch):
    b = WifiBridge(vofa_enabled=False)

    class _SyncThread:
        def __init__(self, target, args=(), **_kw):
            self._run = lambda: target(*args)

        def start(self):
            self._run()

    monkeypatch.setattr(wb.threading, "Thread", _SyncThread)
    clock = [1000.0]
    monkeypatch.setattr(wb.time, "monotonic", lambda: clock[0])
    key = ["build-1"]
    monkeypatch.setattr(wb, "_axf_key", lambda: key[0])
    res = FakeResolver()
    monkeypatch.setattr(b, "_ensure_preset_resolver", lambda: None)
    b._preset_resolver = res
    b._wifi_send = MagicMock()

    def reflash():
        key[0] = "build-2"
        res.shift = 12

    def reply(slot=SLOT, divider=DIV, shift=None):
        s = res.shift if shift is None else shift
        return b._handle_schema_frame(schema_frame(slot, divider, [(BASE[n] + s, 4, 1) for n in NAMES]))

    return SimpleNamespace(b=b, clock=clock, reflash=reflash, reply=reply,
                           last_sent=lambda: b._wifi_send.sendto.call_args_list[-1][0][0])


def _has(req, address):
    return struct.pack("<I", address) in req


def test_unnamed_reply_after_reflash_resubscribes_by_name(rig):
    rig.b.subscribe_slot(SLOT, DIV, NAMES)
    rig.reply()
    assert rig.b.stream_naming_status() == {}
    rig.reflash()
    n_sent = rig.b._wifi_send.sendto.call_count
    rig.reply()                                   # new firmware answers with addresses the old request never had
    assert rig.b._wifi_send.sendto.call_count == n_sent + 1
    req = rig.last_sent()
    assert _has(req, BASE["imu_data.rol"] + 12) and not _has(req, BASE["imu_data.rol"])
    assert "unnamed" in rig.b.stream_naming_status()[SLOT]       # visible until the named reply lands
    rig.reply()                                   # reply to the healed request
    assert rig.b.stream_naming_status() == {}
    names = [r.name for r in rig.b._stream_schemas[SLOT].ranges]
    assert names == NAMES


def test_watchdog_rebuilds_instead_of_resending_stale_bytes(rig):
    rig.b.subscribe_slot(SLOT, DIV, NAMES)
    old_req = rig.last_sent()
    rig.reflash()
    rig.clock[0] += 60.0                          # FC rebooting: the slot goes silent
    assert rig.b._check_slot_watchdog() == [SLOT]
    req = rig.last_sent()
    assert req != old_req
    assert _has(req, BASE["imu_data.pit"] + 12) and not _has(req, BASE["imu_data.pit"])
    assert rig.b.resend_slot(SLOT)                # the fresh request is current again: plain re-send
    assert rig.last_sent() == req


def test_same_firmware_still_resends_cached_bytes(rig):
    rig.b.subscribe_slot(SLOT, DIV, NAMES)
    old_req = rig.last_sent()
    rig.clock[0] += 60.0
    assert rig.b._check_slot_watchdog() == [SLOT]
    assert rig.last_sent() == old_req


def test_cleared_slot_that_streams_again_is_stopped(rig):
    rig.b.subscribe_slot(2, 0, [])
    rig.reply(slot=2, divider=1)                  # FC reboot: boot default back on a slot the GS stopped
    assert rig.last_sent() == build_stream_request(ranges=[], divider=0, transport=1, slot=2)


def test_heal_is_rate_limited_and_gives_up_visibly(rig):
    rig.b.subscribe_slot(SLOT, DIV, NAMES)
    rig.reflash()
    sends = []
    for _ in range(5):
        before = rig.b._wifi_send.sendto.call_count
        rig.reply(shift=99)                       # an FC that never answers with the requested addresses
        rig.reply(shift=99)                       # second reply inside the 5 s window: no extra send
        sends.append(rig.b._wifi_send.sendto.call_count - before)
        rig.clock[0] += 6.0
    assert sends == [1, 1, 1, 0, 0]
    assert "unnamed" in rig.b.stream_naming_status()[SLOT]
    with pytest.raises(RuntimeError, match=r"stream not named: slot 3: 2 of 2 ranges unnamed"):
        wait_streams_named(rig.b, [SLOT], timeout_s=0)


def test_capture_slot_without_schema_blocks_the_campaign(rig):
    with pytest.raises(RuntimeError, match="slot 1: no schema yet"):
        wait_streams_named(rig.b, [1], timeout_s=0)


def test_raw_range_requests_are_not_rebuilt(rig):
    from ground_station.livewatch.stream import StreamRange
    rig.b.subscribe_slot(SLOT, DIV, [StreamRange(address=0x20000100, size=4, count=1)])
    rig.reflash()
    assert not rig.b._request_is_stale(SLOT)
    assert rig.b._heal_slot(SLOT, "test") is False
