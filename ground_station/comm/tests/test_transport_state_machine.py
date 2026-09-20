"""Tests for WifiBridge subscribe transport state machine.

Verifies one-request-per-slot: subscribe_slot() sends one 0x21 frame,
_handle_schema_frame() processes the 0x08 reply, and the state machine
transitions correctly. No batch queue; no multi-batch release.
"""
from __future__ import annotations

import pytest

pytest.skip("spec for unimplemented subscribe transaction layer; see reports/COMM-TEST-TRIAGE.md", allow_module_level=True)

import struct
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from ground_station.comm.wifi_bridge import WifiBridge
from ground_station.livewatch.stream import StreamRange, build_stream_request


def _make_range(index: int, gap: int = 8) -> StreamRange:
    """Create a StreamRange at address 0x20000000 + index * gap."""
    return StreamRange(
        address=0x20000000 + index * gap,
        size=4,
        count=1,
        name=f"test_var_{index}",
    )


def _build_0x08_frame(slot: int, n_ranges: int = 1, total_bytes: int = 4,
                      divider: int = 4, transport: int = 1) -> bytes:
    """Build a valid 0x08 schema frame.

    Addresses are placed at 0x20000000 + i*8, matching `_make_range(i, gap=8)`
    so the overlap check in _handle_schema_frame passes.
    """
    from ground_station.livewatch.transport import _xor_crc
    config = bytes([n_ranges, divider, transport, slot,
                    (total_bytes >> 8) & 0xFF, total_bytes & 0xFF])
    ranges_bytes = b""
    for i in range(n_ranges):
        addr = 0x20000000 + i * 8
        ranges_bytes += struct.pack("<I", addr)
        ranges_bytes += struct.pack("<H", 4)
        ranges_bytes += struct.pack("<H", 1)
    payload = config + ranges_bytes
    payload_len = 5 + n_ranges * 8
    len_hi = (payload_len >> 8) & 0xFF
    len_lo = payload_len & 0xFF
    crc_body = bytes([0x08, len_hi, len_lo]) + payload
    crc_byte = _xor_crc(crc_body)
    return (bytes([0xAA, 0xBB, 0x08, len_hi, len_lo])
            + payload + bytes([crc_byte]))


class TestOneRequestPerSlot(unittest.TestCase):
    """Verify one-request-per-slot behavior."""

    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._wifi_send = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def tearDown(self):
        self.bridge.stop()

    def test_subscribe_sends_one_request(self):
        """subscribe_slot sends exactly one 0x21 request."""
        self.bridge.subscribe_slot(
            slot=0, divider=4,
            ranges=[_make_range(0)],
            transport=1,
        )
        self.assertEqual(self.bridge._wifi_send.sendto.call_count, 1)

    def test_schema_response_transitions_sent_to_schema_received(self):
        """_handle_schema_frame transitions slot state from 'sent' to 'schema_received'."""
        self.bridge.subscribe_slot(
            slot=1, divider=4,
            ranges=[_make_range(0)],
            transport=1,
        )
        self.assertEqual(self.bridge._slot_states[1], "sent")
        frame = _build_0x08_frame(slot=1, n_ranges=1, total_bytes=4)
        result = self.bridge._handle_schema_frame(frame)
        self.assertEqual(result, 1)
        self.assertEqual(self.bridge._slot_states[1], "schema_received")

    def test_pending_ranges_cleared_after_schema(self):
        """After schema response, _pending_schema_ranges is cleared."""
        self.bridge.subscribe_slot(
            slot=0, divider=4,
            ranges=[_make_range(0)],
            transport=1,
        )
        self.assertIn(0, self.bridge._pending_schema_ranges)
        frame = _build_0x08_frame(slot=0, n_ranges=1, total_bytes=4)
        self.bridge._handle_schema_frame(frame)
        self.assertNotIn(0, self.bridge._pending_schema_ranges)

    def test_request_id_unique_per_subscribe(self):
        """Each subscribe gets a unique request ID."""
        self.bridge.subscribe_slot(
            slot=0, divider=4,
            ranges=[_make_range(0)],
            transport=1,
        )
        self.bridge.subscribe_slot(
            slot=1, divider=4,
            ranges=[_make_range(1)],
            transport=1,
        )
        ids = list(self.bridge._request_states.keys())
        self.assertEqual(len(ids), 2)
        self.assertEqual(ids[0], 0)
        self.assertEqual(ids[1], 1)

    def test_metadata_complete(self):
        """subscribe_slot populates metadata with all required fields."""
        self.bridge.subscribe_slot(
            slot=0, divider=4,
            ranges=[_make_range(0)],
            transport=1,
        )
        meta = self.bridge._request_metadata[0]
        self.assertEqual(meta["slot"], 0)
        self.assertEqual(meta["divider"], 4)
        self.assertEqual(meta["transport"], 1)
        self.assertEqual(meta["range_count"], 1)
        self.assertEqual(meta["batch_index"], 0)
        self.assertEqual(meta["total_batches"], 1)
        self.assertIsNotNone(meta["created_ns"])
        self.assertIsNotNone(meta["timeout_ns"])
        self.assertEqual(meta["retry_count"], 0)

    def test_slot_state_transitions(self):
        """Slot state: sent -> schema_received on 0x08."""
        self.bridge.subscribe_slot(
            slot=0, divider=4,
            ranges=[_make_range(0)],
            transport=1,
        )
        self.assertEqual(self.bridge._slot_states[0], "sent")
        self.bridge._handle_schema_frame(_build_0x08_frame(slot=0))
        self.assertEqual(self.bridge._slot_states[0], "schema_received")

    def test_schema_identity_tracking(self):
        """First schema registers identity; second with different addresses triggers changed."""
        self.bridge.subscribe_slot(
            slot=1, divider=4,
            ranges=[_make_range(0)],
            transport=1,
        )
        frame1 = _build_0x08_frame(slot=1, n_ranges=1, total_bytes=4)
        self.bridge._handle_schema_frame(frame1)
        self.assertNotIn(1, self.bridge._slot_schema_changed)
        self.assertIsNotNone(self.bridge._slot_schema_identity.get(1))

        # Second frame with different addresses
        from ground_station.livewatch.transport import _xor_crc
        config = bytes([1, 4, 1, 1, 0, 4])
        addr = 0x30000000
        rng = struct.pack("<I", addr) + struct.pack("<HH", 4, 1)
        payload = config + rng
        payload_len = 5 + 8
        len_hi = (payload_len >> 8) & 0xFF
        len_lo = payload_len & 0xFF
        crc_body = bytes([0x08, len_hi, len_lo]) + payload
        crc_byte = _xor_crc(crc_body)
        frame2 = bytes([0xAA, 0xBB, 0x08, len_hi, len_lo]) + payload + bytes([crc_byte])
        self.bridge._handle_schema_frame(frame2)
        self.assertTrue(self.bridge._slot_schema_changed.get(1))

    def test_stop_clears_request_state(self):
        """stop() clears all request state."""
        self.bridge.subscribe_slot(
            slot=0, divider=4,
            ranges=[_make_range(0)],
            transport=1,
        )
        self.bridge.stop()
        self.assertEqual(len(self.bridge._request_states), 0)
        self.assertEqual(len(self.bridge._request_metadata), 0)
        self.assertEqual(len(self.bridge._request_to_slot), 0)
        self.assertEqual(len(self.bridge._pending_schema_ranges), 0)

    def test_reconnect_clears_subscriptions(self):
        """_invalidate_subscriptions_on_reconnect clears all state."""
        self.bridge.subscribe_slot(
            slot=0, divider=4,
            ranges=[_make_range(0)],
            transport=1,
        )
        self.bridge._invalidate_subscriptions_on_reconnect()
        self.assertNotIn(0, self.bridge._slot_states)
        self.assertEqual(self.bridge._request_states[0], "stopped")

    def test_same_slot_replaces(self):
        """New subscribe for occupied slot replaces prior subscription."""
        self.bridge.subscribe_slot(
            slot=0, divider=4,
            ranges=[_make_range(0)],
            transport=1,
        )
        self.assertEqual(self.bridge._slot_states[0], "sent")
        self.bridge.subscribe_slot(
            slot=0, divider=8,
            ranges=[_make_range(0), _make_range(1)],
            transport=1,
        )
        self.assertEqual(self.bridge._slot_states[0], "sent")

    def test_schema_identity_changed_flag_propagated(self):
        """_slot_schema_changed flag is propagated and cleared by stream decoder."""
        self.bridge.subscribe_slot(
            slot=1, divider=4,
            ranges=[_make_range(0)],
            transport=1,
        )
        frame1 = _build_0x08_frame(slot=1, n_ranges=1, total_bytes=4)
        self.bridge._handle_schema_frame(frame1)

        # Simulate different addresses
        from ground_station.livewatch.transport import _xor_crc
        config = bytes([1, 4, 1, 1, 0, 4])
        addr = 0x30000000
        rng = struct.pack("<I", addr) + struct.pack("<HH", 4, 1)
        payload = config + rng
        payload_len = 13
        len_hi = 0
        len_lo = payload_len
        crc_body = bytes([0x08, len_hi, len_lo]) + payload
        crc_byte = _xor_crc(crc_body)
        frame2 = bytes([0xAA, 0xBB, 0x08, len_hi, len_lo]) + payload + bytes([crc_byte])
        self.bridge._handle_schema_frame(frame2)

        # After schema change, _slot_schema_changed[1] is True
        self.assertTrue(self.bridge._slot_schema_changed.get(1, False))


class TestSchemaFrameProcessing(unittest.TestCase):
    """Test _handle_schema_frame in isolation."""

    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._wifi_send = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def tearDown(self):
        self.bridge.stop()

    def test_malformed_frame_returns_none(self):
        """Malformed frame returns None."""
        result = self.bridge._handle_schema_frame(b"\x00\x01\x02")
        self.assertIsNone(result)

    def test_schema_registered_without_pending_request(self):
        """Schema frame for a slot with no subscription still registers."""
        frame = _build_0x08_frame(slot=3, n_ranges=1, total_bytes=4)
        result = self.bridge._handle_schema_frame(frame)
        self.assertEqual(result, 3)
        self.assertIn(3, self.bridge._stream_schemas)

    def test_schema_frame_clears_pending_ranges(self):
        """After processing 0x08, _pending_schema_ranges is cleared (Item 2 fix)."""
        self.bridge.subscribe_slot(
            slot=0, divider=4,
            ranges=[_make_range(0)],
            transport=1,
        )
        self.assertIn(0, self.bridge._pending_schema_ranges)
        frame = _build_0x08_frame(slot=0, n_ranges=1, total_bytes=4)
        self.bridge._handle_schema_frame(frame)
        self.assertNotIn(0, self.bridge._pending_schema_ranges)

    def test_correct_slot_accepted(self):
        """0x08 for the subscribed slot is accepted."""
        self.bridge.subscribe_slot(
            slot=0, divider=4,
            ranges=[_make_range(0)],
            transport=1,
        )
        frame = _build_0x08_frame(slot=0, n_ranges=1, total_bytes=4)
        result = self.bridge._handle_schema_frame(frame)
        self.assertEqual(result, 0)
        self.assertEqual(self.bridge._request_states[0], "schema_received")

    def test_wrong_slot_ignored(self):
        """0x08 for wrong slot does not affect the subscription."""
        self.bridge.subscribe_slot(
            slot=0, divider=4,
            ranges=[_make_range(0)],
            transport=1,
        )
        frame = _build_0x08_frame(slot=1, n_ranges=1, total_bytes=4)
        self.bridge._handle_schema_frame(frame)
        # Slot 0 should remain in "sent" state
        self.assertEqual(self.bridge._slot_states[0], "sent")
        self.assertEqual(self.bridge._request_states[0], "sent")
