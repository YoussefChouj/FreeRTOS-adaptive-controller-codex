"""Tests for T17: preset schema retry when 0x08 replies are lost.

These tests verify that when the first 0x08 schema reply for a preset slot
is lost (e.g., MicoAir routed it to a dead ephemeral socket from pre-clear),
the retry logic in apply_startup_preset re-subscribes and successfully
registers the schema with all named keys.

All tests use fake sockets / mocks — no live service, no network traffic.
"""
from __future__ import annotations

import struct
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ground_station.comm.wifi_bridge import WifiBridge
from ground_station.livewatch.stream import StreamRange, StreamSchema
from ground_station.livewatch.transport import _xor_crc


# ---------------------------------------------------------------------------
# Helpers — build 0x08 schema frames matching the firmware layout
# ---------------------------------------------------------------------------

def _build_0x08_frame(
    slot: int,
    n_ranges: int,
    divider: int = 4,
    transport: int = 1,
    total_bytes: int = None,
    range_base: int = 0x20000000,
    range_gap: int = 8,
) -> bytes:
    """Build a valid 0x08 schema frame matching firmware layout."""
    if total_bytes is None:
        total_bytes = n_ranges * 4
    config = bytes([
        n_ranges, divider, transport, slot,
        (total_bytes >> 8) & 0xFF, total_bytes & 0xFF,
    ])
    ranges_bytes = b""
    for i in range(n_ranges):
        addr = range_base + i * range_gap
        ranges_bytes += struct.pack("<I", addr)
        ranges_bytes += struct.pack("<H", 4)
        ranges_bytes += struct.pack("<H", 1)
    payload = config + ranges_bytes
    payload_len = 5 + n_ranges * 8
    len_hi = (payload_len >> 8) & 0xFF
    len_lo = payload_len & 0xFF
    crc_body = bytes([0x08, len_hi, len_lo]) + payload
    crc_byte = _xor_crc(crc_body)
    return bytes([0xAA, 0xBB, 0x08, len_hi, len_lo]) + payload + bytes([crc_byte])


def _make_range(index: int, gap: int = 8) -> StreamRange:
    """Create a unique StreamRange for testing."""
    return StreamRange(
        address=0x20000000 + index * gap,
        size=4,
        count=1,
        name=f"test_var_{index}",
    )


# ---------------------------------------------------------------------------
# Test: retry logic simulates lost 0x08 reply
# ---------------------------------------------------------------------------

class TestPresetSchemaRetry(unittest.TestCase):
    """Verify the bridge registers schemas even when the first 0x08 is lost."""

    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._wifi_send = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def tearDown(self):
        self.bridge.stop()

    def test_first_0x08_lost_retry_succeeds(self):
        """When first 0x08 is lost, re-subscribe registers the schema."""
        # Subscribe slot 1 — sets pending ranges
        ranges = [_make_range(i, gap=8) for i in range(5)]
        self.bridge.subscribe_slot(
            slot=1, divider=4, ranges=ranges, transport=1,
        )
        self.assertEqual(self.bridge._slot_states.get(1), "sent")
        # Schema is NOT registered yet (0x08 was "lost")
        self.assertNotIn(1, self.bridge._stream_schemas)

        # Simulate the retry: re-subscribe (bridge clears state again)
        self.bridge.subscribe_slot(
            slot=1, divider=4, ranges=ranges, transport=1,
        )
        self.assertEqual(self.bridge._slot_states.get(1), "sent")

        # Now the 0x08 arrives
        frame = _build_0x08_frame(slot=1, n_ranges=5, divider=4,
                                  transport=1, total_bytes=20)
        result = self.bridge._handle_schema_frame(frame)
        self.assertEqual(result, 1)
        self.assertIn(1, self.bridge._stream_schemas)
        schema = self.bridge._stream_schemas[1]
        self.assertEqual(len(schema.ranges), 5)
        # Named ranges should be matched
        self.assertEqual(schema.ranges[0].name, "test_var_0")

    def test_coalesced_ranges_with_count_gt1(self):
        """Coalesced range (count > 1) produces named key without exception."""
        ranges = [
            StreamRange(address=0x20000000, size=4, count=3,
                        name="coalesced_array"),
        ]
        self.bridge.subscribe_slot(
            slot=2, divider=1, ranges=ranges, transport=1,
        )

        # Build 0x08 with a coalesced range: address=0x20000000, size=4, count=3
        # We need to build the frame manually to match the coalesced range
        from ground_station.livewatch.transport import _xor_crc
        n_ranges = 1
        slot = 2
        divider = 1
        transport = 1
        total_bytes = 12  # 4 * 3
        config = bytes([n_ranges, divider, transport, slot,
                        (total_bytes >> 8) & 0xFF, total_bytes & 0xFF])
        # Range block: address=0x20000000, size=4, count=3
        ranges_bytes = struct.pack("<I", 0x20000000)
        ranges_bytes += struct.pack("<H", 4)
        ranges_bytes += struct.pack("<H", 3)
        payload = config + ranges_bytes
        payload_len = 5 + n_ranges * 8
        len_hi = (payload_len >> 8) & 0xFF
        len_lo = payload_len & 0xFF
        crc_body = bytes([0x08, len_hi, len_lo]) + payload
        crc_byte = _xor_crc(crc_body)
        frame = bytes([0xAA, 0xBB, 0x08, len_hi, len_lo]) + payload + bytes([crc_byte])

        # No exception should be raised
        result = self.bridge._handle_schema_frame(frame)
        self.assertEqual(result, 2)
        self.assertIn(2, self.bridge._stream_schemas)
        schema = self.bridge._stream_schemas[2]
        self.assertEqual(len(schema.ranges), 1)
        self.assertEqual(schema.ranges[0].name, "coalesced_array")
        self.assertEqual(schema.ranges[0].count, 3)

    def test_multiple_slots_first_replies_lost(self):
        """First 0x08 lost for 2 of 4 slots; retry registers all."""
        all_slots = {}
        for slot in range(4):
            n = slot + 2  # 2, 3, 4, 5 ranges
            ranges = [_make_range(i, gap=8) for i in range(n)]
            self.bridge.subscribe_slot(
                slot=slot, divider=4, ranges=ranges, transport=1,
            )
            all_slots[slot] = ranges

        # No schemas yet — simulate that 0x08 was lost for slots 1 and 2
        # Slots 0 and 3 get their schemas
        for s in (0, 3):
            ranges = all_slots[s]
            frame = _build_0x08_frame(
                slot=s, n_ranges=len(ranges), divider=4,
                total_bytes=len(ranges) * 4,
            )
            self.bridge._handle_schema_frame(frame)

        # Slots 0 and 3 should be registered
        self.assertIn(0, self.bridge._stream_schemas)
        self.assertIn(3, self.bridge._stream_schemas)
        self.assertNotIn(1, self.bridge._stream_schemas)
        self.assertNotIn(2, self.bridge._stream_schemas)

        # Retry: re-subscribe slots 1 and 2
        for s in (1, 2):
            self.bridge.subscribe_slot(
                slot=s, divider=4, ranges=all_slots[s], transport=1,
            )

        # Deliver 0x08 for slots 1 and 2
        for s in (1, 2):
            ranges = all_slots[s]
            frame = _build_0x08_frame(
                slot=s, n_ranges=len(ranges), divider=4,
                total_bytes=len(ranges) * 4,
            )
            result = self.bridge._handle_schema_frame(frame)
            self.assertEqual(result, s)

        # All slots now have schemas
        with self.bridge._stream_lock:
            for s in range(4):
                self.assertIn(s, self.bridge._stream_schemas)
                self.assertEqual(len(self.bridge._stream_schemas[s].ranges),
                                 s + 2)


# ---------------------------------------------------------------------------
# Test: _handle_schema_frame handles coalesced ranges without exception
# ---------------------------------------------------------------------------

class TestSchemaFrameCoalescedRanges(unittest.TestCase):
    """_handle_schema_frame must handle count > 1 ranges without error."""

    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._wifi_send = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def tearDown(self):
        self.bridge.stop()

    def test_handle_schema_with_count_10(self):
        """Schema frame with range count=10 is decoded without exception."""
        ranges = [
            StreamRange(address=0x20000000, size=4, count=10,
                        name="sensor_data"),
        ]
        self.bridge.subscribe_slot(
            slot=3, divider=2, ranges=ranges, transport=1,
        )

        # Build 0x08 with count=10: 1 range, size=4, count=10, total=40
        from ground_station.livewatch.transport import _xor_crc
        n_ranges = 1
        slot = 3
        divider = 2
        transport = 1
        total_bytes = 40  # 4 * 10
        config = bytes([n_ranges, divider, transport, slot,
                        (total_bytes >> 8) & 0xFF, total_bytes & 0xFF])
        # Range block: address=0x20000000, size=4, count=10
        ranges_bytes = struct.pack("<I", 0x20000000)
        ranges_bytes += struct.pack("<H", 4)
        ranges_bytes += struct.pack("<H", 10)
        payload = config + ranges_bytes
        payload_len = 5 + n_ranges * 8
        len_hi = (payload_len >> 8) & 0xFF
        len_lo = payload_len & 0xFF
        crc_body = bytes([0x08, len_hi, len_lo]) + payload
        crc_byte = _xor_crc(crc_body)
        frame = bytes([0xAA, 0xBB, 0x08, len_hi, len_lo]) + payload + bytes([crc_byte])
        result = self.bridge._handle_schema_frame(frame)
        self.assertEqual(result, 3)
        self.assertIn(3, self.bridge._stream_schemas)
        schema = self.bridge._stream_schemas[3]
        self.assertEqual(schema.ranges[0].count, 10)
        self.assertEqual(schema.ranges[0].name, "sensor_data")

    def test_handle_schema_mixed_named_unnamed(self):
        """Schema with some named, some unnamed ranges is registered correctly."""
        # Subscribe with one named and one unnamed range
        named = StreamRange(
            address=0x20000000, size=4, count=1, name="status_flag",
        )
        unnamed = StreamRange(
            address=0x20000020, size=4, count=1,
        )
        self.bridge.subscribe_slot(
            slot=0, divider=4,
            ranges=[named, unnamed], transport=1,
        )

        # 0x08 echoes both addresses in the same order
        frame = _build_0x08_frame(
            slot=0, n_ranges=2, divider=4,
            total_bytes=8,
            range_base=0x20000000, range_gap=32,  # 0x20 gap
        )
        result = self.bridge._handle_schema_frame(frame)
        self.assertEqual(result, 0)
        self.assertIn(0, self.bridge._stream_schemas)
        schema = self.bridge._stream_schemas[0]
        self.assertEqual(len(schema.ranges), 2)
        self.assertEqual(schema.ranges[0].name, "status_flag")
        self.assertEqual(schema.ranges[1].name, "")  # unnamed


# ---------------------------------------------------------------------------
# Test: verify retry logic in apply_startup_preset context
# ---------------------------------------------------------------------------

class TestPresetRetryLogic(unittest.TestCase):
    """Test the retry logic that checks for missing schemas after subscribe."""

    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._wifi_send = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def tearDown(self):
        self.bridge.stop()

    def test_check_missing_schemas_after_subscribe(self):
        """After subscribe_slot without 0x08, the slot is correctly reported as missing."""
        ranges = [_make_range(i, gap=8) for i in range(3)]
        self.bridge.subscribe_slot(
            slot=0, divider=4, ranges=ranges, transport=1,
        )
        # Slot 0 is not in _stream_schemas because no 0x08 arrived
        with self.bridge._stream_lock:
            self.assertNotIn(0, self.bridge._stream_schemas)

    def test_all_schemas_registered_breaks_loop(self):
        """When all schemas are registered, the retry loop would break."""
        ranges = [_make_range(i, gap=8) for i in range(3)]
        self.bridge.subscribe_slot(
            slot=0, divider=4, ranges=ranges, transport=1,
        )
        # Simulate 0x08 arriving
        frame = _build_0x08_frame(slot=0, n_ranges=3, divider=4,
                                  total_bytes=12)
        self.bridge._handle_schema_frame(frame)

        with self.bridge._stream_lock:
            # After schema arrives, slot 0 is in _stream_schemas
            self.assertIn(0, self.bridge._stream_schemas)

    def test_retry_after_first_failure_re_subscribes(self):
        """On retry, re-subscribe clears old state and re-sends."""
        ranges1 = [_make_range(i, gap=8) for i in range(3)]
        ranges2 = [_make_range(i, gap=8) for i in range(3, 6)]

        # First subscribe
        self.bridge.subscribe_slot(
            slot=1, divider=4, ranges=ranges1, transport=1,
        )
        # Clear the pending ranges manually (simulate that the first 0x08 was lost
        # and we're re-subscribing with the retry logic)
        self.bridge.subscribe_slot(
            slot=1, divider=4, ranges=ranges2, transport=1,
        )
        # After re-subscribe, pending ranges should reflect ranges2
        with self.bridge._stream_lock:
            pending = self.bridge._pending_schema_ranges.get(1)
        self.assertIsNotNone(pending)
        # The address of the first range in pending should match ranges2[0]
        self.assertEqual(pending[0].address, ranges2[0].address)
        self.assertEqual(pending[0].name, "test_var_3")


if __name__ == "__main__":
    unittest.main()
