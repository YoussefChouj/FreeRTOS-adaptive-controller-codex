"""The WiFi bridge decodes every subscribe slot the firmware has, and follows the slot count (WP-37).

Data frames are 0xAA 0xBB (0x09 + slot). The slot range comes from one constant,
ground_station/platform/firmware_contract.py SUBSCRIBE_MAX_SLOTS (= API/subscribe.h), through
SUBSCRIBE_DATA_FRAMES; these tests decode one frame per slot with the shipped value and again with 8 slots
patched in, the value API/subscribe.h is ready for (tools/host_tests.py runs the firmware harness with 8).
"""
from __future__ import annotations

import struct
from unittest.mock import MagicMock

import pytest

import ground_station.comm.wifi_bridge as wb
from ground_station.comm.wifi_bridge import WifiBridge
from ground_station.livewatch.stream import StreamRange
from ground_station.livewatch.transport import crc16_ccitt
from ground_station.platform.firmware_contract import SUBSCRIBE_DATA_FRAMES, SUBSCRIBE_MAX_SLOTS


def _frame(slot: int, values: list[float]) -> bytes:
    """A data frame as Subscribe_BuildStreamFrame emits it: header, SEQ, T_MS, values, CRC16 (BE)."""
    payload = struct.pack("<I", 1234) + b"".join(struct.pack("<f", v) for v in values)
    head = bytes([0x09 + slot, (len(payload) >> 8) & 0xFF, len(payload) & 0xFF, 7])
    return bytes([0xAA, 0xBB]) + head + payload + struct.pack(">H", crc16_ccitt(head + payload))


class _Schema:
    def __init__(self, ranges):
        self.ranges = ranges


def _bridge() -> WifiBridge:
    b = WifiBridge(vofa_enabled=False)
    b._wifi = MagicMock()
    b._cmd_udp = MagicMock()
    b._telem_udp = MagicMock()
    b._udp_send = MagicMock()
    return b


def _decodes(b: WifiBridge, slot: int) -> bool:
    b.apply_manifest_schema(slot, _Schema(ranges=(StreamRange(0x20000000, 4, 2, "w.x, w.y", "f"),)))
    buf = bytearray(_frame(slot, [1.0, 2.0]))
    b._parse_one(buf)
    return len(buf) == 0 and slot in b._last_slot_rx


def test_data_frames_follow_the_slot_count():
    assert list(SUBSCRIBE_DATA_FRAMES) == [0x09 + s for s in range(SUBSCRIBE_MAX_SLOTS)]


@pytest.mark.parametrize("slot", range(SUBSCRIBE_MAX_SLOTS))
def test_every_slot_decodes(slot):
    assert _decodes(_bridge(), slot)


def test_the_type_past_the_last_slot_is_not_a_slot():
    b = _bridge()
    buf = bytearray(_frame(SUBSCRIBE_MAX_SLOTS, [1.0]))
    b._parse_one(buf)
    assert SUBSCRIBE_MAX_SLOTS not in b._last_slot_rx


def test_eight_slots_decode_when_the_contract_says_eight(monkeypatch):
    monkeypatch.setattr(wb, "SUBSCRIBE_MAX_SLOTS", 8)
    monkeypatch.setattr(wb, "SUBSCRIBE_DATA_FRAMES", range(0x09, 0x09 + 8))
    b = _bridge()
    assert all(_decodes(b, slot) for slot in range(8))
    assert b.subscribe_preview(slot=7, divider=0)["slot"] == 7
    with pytest.raises(ValueError):
        b.subscribe_preview(slot=8, divider=0)
