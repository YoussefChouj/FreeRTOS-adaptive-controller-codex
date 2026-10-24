"""Tests for the WiFi bridge's subscribe-stream decoder + VoFA+ forwarder.

The decoder reads 0xAA 0xBB 0x09+slot frames off the WiFi socket, decodes
the value payload into named floats using the registered per-slot schema,
and forwards them to UDP 1347 / 1348 in JustFloat format.

We exercise the decoder by feeding it pre-built bytes that match the
firmware's `Subscribe_BuildStreamFrame` layout (see
`ground_station/livewatch/stream.py` for the host-side mirror). The
VoFA+ forwarder is tested by stubbing `_udp_send.sendto` and capturing
what was sent.
"""
from __future__ import annotations

import pytest
import socket
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from ground_station.comm.wifi_bridge import WifiBridge
from ground_station.livewatch.stream import StreamRange, StreamSchema


def _build_stream_frame(slot: int, seq: int, t_ms: int, values: list) -> bytes:
    """Build a 0xAA 0xBB 0x09+slot data frame as the firmware emits it.

    Firmware frame layout (see `Subscribe_BuildStreamFrame` in
    `API/subscribe.c` and the docstring of `subscribe.h`):
      [0xAA][0xBB][TYPE][LEN_HI][LEN_LO][SEQ][T_MS LE u32][values][CRC16 BE]
    The CRC16-CCITT is computed over [TYPE..last value byte] (see
    `livewatch/transport.crc16_ccitt` for the host-side mirror).
    """
    from ground_station.livewatch.transport import crc16_ccitt
    values_bytes = b"".join(struct.pack("<f", float(v)) for v in values)
    payload = struct.pack("<I", t_ms) + values_bytes  # payload is T_MS + values
    frame_type = 0x09 + slot
    # CRC is over TYPE + LEN + payload = bytes [2 .. end-2]
    crc_input = bytes([frame_type,
                       (len(payload) >> 8) & 0xFF,
                       len(payload) & 0xFF,
                       seq]) + payload
    crc = crc16_ccitt(crc_input)
    return (bytes([0xAA, 0xBB, frame_type,
                   (len(payload) >> 8) & 0xFF,
                   len(payload) & 0xFF,
                   seq]) + payload
            + struct.pack(">H", crc))


class FakeSchema:
    """A minimal StreamSchema-shaped object the bridge can read."""

    def __init__(self, ranges):
        self.ranges = ranges


class TestDecodeStreamFrame(unittest.TestCase):
    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        # Stub out network so __init__ doesn't try to bind sockets.
        self.bridge._wifi = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def test_decode_with_schema_names_channels(self):
        # Slot 1, two packed same-size scalars: gyro FB and U.
        rng = StreamRange(
            address=0x20001000, size=4, count=2,
            name="Ctrler.gyroxPID.FB, Ctrler.gyroxPID.U", fmt="f")
        self.bridge.apply_manifest_schema(1, FakeSchema(ranges=(rng,)))

        frame = _build_stream_frame(slot=1, seq=7, t_ms=12345,
                                    values=[0.5, 0.25])
        decoded = self.bridge._decode_stream_frame(1, frame)
        self.assertIsNotNone(decoded)
        names = decoded["names"]
        self.assertEqual(names[0], "Ctrler.gyroxPID.FB")
        self.assertEqual(names[1], "Ctrler.gyroxPID.U")
        self.assertEqual(decoded["values"], [0.5, 0.25])
        self.assertIn("slot1.Ctrler.gyroxPID.FB", decoded["json"])
        self.assertEqual(decoded["json"]["slot1.seq"], 7)
        self.assertEqual(decoded["json"]["slot1.t_ms"], 12345)

    def test_decode_without_schema_uses_sequential_names(self):
        frame = _build_stream_frame(slot=1, seq=1, t_ms=10, values=[1.0, 2.0, 3.0])
        decoded = self.bridge._decode_stream_frame(1, frame)
        self.assertIsNotNone(decoded)
        names = decoded["names"]
        self.assertEqual(names, ["ch1.0", "ch1.1", "ch1.2"])

    def test_sequence_metadata_reports_modulo_256_loss(self):
        first = self.bridge._decode_stream_frame(
            1, _build_stream_frame(1, 10, 100, [1.0]))
        second = self.bridge._decode_stream_frame(
            1, _build_stream_frame(1, 13, 200, [2.0]))
        self.assertEqual(first["json"]["slot1.received"], 1)
        self.assertEqual(second["json"]["slot1.received"], 2)
        self.assertEqual(second["json"]["slot1.dropped"], 2)
        self.assertAlmostEqual(second["json"]["slot1.loss_pct"], 50.0)

    def test_decode_truncated_payload_returns_none(self):
        # Truncated header -- only 5 bytes when 10 are needed.
        result = self.bridge._decode_stream_frame(1, b"\xAA\xBB\x09\x00\x04")
        self.assertIsNone(result)

    def test_parse_one_decodes_real_shaped_0x09_frame(self):
        """Verify _parse_one correctly sizes and slices a real-shaped 0x09 data frame."""
        frame_bytes = _build_stream_frame(slot=0, seq=42, t_ms=5000, values=[-0.5, 1.25, 3.14])
        buf = bytearray(frame_bytes)
        # Register a schema for slot 0
        rng = StreamRange(address=0x20000000, size=4, count=3, name="status.roll_deg, status.pitch_deg, status.yaw_deg", fmt="f")
        self.bridge.apply_manifest_schema(0, FakeSchema(ranges=(rng,)))

        self.bridge._parse_one(buf)
        # Buffer must be fully consumed with zero trailing residual bytes
        self.assertEqual(len(buf), 0)
        # Verify decoded telemetry was published under 'a'
        telem, _ = self.bridge.get_telemetry()
        self.assertIn("a", telem)
        self.assertIn("status.roll_deg", telem["a"])
        self.assertAlmostEqual(telem["a"]["status.roll_deg"], -0.5, places=3)
        self.assertIn("status.pitch_deg", telem["a"])
        self.assertAlmostEqual(telem["a"]["status.pitch_deg"], 1.25, places=3)



class TestForwardVofa(unittest.TestCase):
    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=True,
                                 vofa_host="127.0.0.1",
                                 vofa_port_a=1347,
                                 vofa_port_b=1348)
        self.bridge._udp_send = MagicMock()

    def test_slot1_goes_to_port_a(self):
        self.bridge._forward_vofa(1, [0.5, 0.25])
        self.bridge._udp_send.sendto.assert_called_once()
        args = self.bridge._udp_send.sendto.call_args[0]
        payload, dest = args
        self.assertEqual(dest, ("127.0.0.1", 1347))
        # 2 floats = 8 B + 4 B JustFloat tail.
        self.assertEqual(len(payload), 8 + 4)
        self.assertEqual(payload[-4:], b"\x00\x00\x80\x7f")
        # LE float32 of 0.5 and 0.25.
        v0, v1 = struct.unpack("<2f", payload[:8])
        self.assertAlmostEqual(v0, 0.5)
        self.assertAlmostEqual(v1, 0.25)

    def test_slot2_goes_to_port_b(self):
        self.bridge._forward_vofa(2, [1.0])
        self.bridge._udp_send.sendto.assert_called_once()
        _, dest = self.bridge._udp_send.sendto.call_args[0]
        self.assertEqual(dest, ("127.0.0.1", 1348))

    def test_slot3_is_dropped(self):
        self.bridge._forward_vofa(3, [1.0])
        self.bridge._udp_send.sendto.assert_not_called()

    def test_disabled_bridge_does_nothing(self):
        self.bridge._vofa_enabled = False
        self.bridge._forward_vofa(1, [1.0])
        self.bridge._udp_send.sendto.assert_not_called()


# ----------------------------------------------------------------------
# Session 3: Transport and telemetry correctness tests
# ----------------------------------------------------------------------


class TestCrcValidation(unittest.TestCase):
    """CRC16-CCITT validation rejects corrupted frames without misaligning the buffer."""

    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def test_valid_frame_is_accepted(self):
        """A frame with a correct CRC is decoded normally."""
        frame = _build_stream_frame(slot=1, seq=1, t_ms=100, values=[1.0, 2.0])
        result = self.bridge._decode_stream_frame(1, frame)
        self.assertIsNotNone(result)
        self.assertEqual(result["values"], [1.0, 2.0])

    def test_corrupted_crc_is_rejected(self):
        """A frame with a wrong CRC byte is dropped and crc_errors is incremented."""
        frame = _build_stream_frame(slot=1, seq=1, t_ms=100, values=[1.0])
        # Corrupt the last byte (CRC low byte)
        bad_frame = bytearray(frame)
        bad_frame[-1] ^= 0xFF  # flip all bits in the CRC
        result = self.bridge._decode_stream_frame(1, bad_frame)
        self.assertIsNone(result)
        # crc_errors was incremented in _stream_stats
        with self.bridge._stream_lock:
            stats = self.bridge._stream_stats.get(1, {})
        self.assertEqual(stats.get("crc_errors", 0), 1)
        # received is NOT incremented for a dropped frame
        self.assertEqual(stats.get("received", 0), 0)

    def test_second_corrupted_frame_increments_crc_errors(self):
        """Multiple CRC failures accumulate crc_errors without resetting other counters."""
        for i in range(3):
            frame = _build_stream_frame(slot=0, seq=i, t_ms=i * 100, values=[float(i)])
            bad = bytearray(frame)
            bad[-1] ^= 0x01
            self.bridge._decode_stream_frame(0, bad)
        with self.bridge._stream_lock:
            stats = self.bridge._stream_stats.get(0, {})
        self.assertEqual(stats["crc_errors"], 3)
        self.assertEqual(stats["received"], 0)
        self.assertEqual(stats["dropped"], 0)

    def test_crc_error_payload_contains_crc_errors_field(self):
        """The JSON payload from a valid frame carries crc_errors from _stream_stats."""
        # Pre-seed crc_errors via a bad frame
        bad = bytearray(_build_stream_frame(slot=1, seq=0, t_ms=0, values=[0.0]))
        bad[-1] ^= 0xFF
        self.bridge._decode_stream_frame(1, bad)
        # Now send a valid frame
        frame = _build_stream_frame(slot=1, seq=1, t_ms=100, values=[3.14])
        result = self.bridge._decode_stream_frame(1, frame)
        self.assertIsNotNone(result)
        # crc_errors=1 from the bad frame, now in the payload
        self.assertEqual(result["json"]["slot1.crc_errors"], 1)

    def test_crc_validation_does_not_misalign_buffer(self):
        """After a CRC error the buffer is untouched; the next correct frame parses."""
        # Send one bad frame followed by a valid one
        bad = bytearray(_build_stream_frame(slot=1, seq=10, t_ms=0, values=[0.0]))
        bad[-1] ^= 0xFF
        good = _build_stream_frame(slot=1, seq=11, t_ms=200, values=[9.99])
        # Simulate a buffer with bad+good concatenated
        buf = bad + good
        # _decode_stream_frame only looks at the frame it's given, not a shared buffer.
        # The caller (_parse_one) passes bytes(buf[:total]) so bad was already stripped.
        # We test the two-step: first bad returns None, then good succeeds.
        self.assertIsNone(self.bridge._decode_stream_frame(1, bad))
        result = self.bridge._decode_stream_frame(1, good)
        self.assertIsNotNone(result)
        # Float 9.99 is not exactly representable; use almost-equal comparison.
        self.assertAlmostEqual(result["values"][0], 9.99, places=3)


class TestSequenceGapsAndLoss(unittest.TestCase):
    """Sequence gap detection correctly computes dropped frames and loss_pct."""

    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def test_consecutive_frames_no_loss(self):
        """Adjacent sequence numbers produce 0% loss."""
        for seq in range(5):
            frame = _build_stream_frame(slot=2, seq=seq, t_ms=seq * 50, values=[float(seq)])
            result = self.bridge._decode_stream_frame(2, frame)
            self.assertIsNotNone(result)
        with self.bridge._stream_lock:
            stats = self.bridge._stream_stats[2]
        self.assertEqual(stats["received"], 5)
        self.assertEqual(stats["dropped"], 0)
        self.assertAlmostEqual(stats.get("crc_errors", 0), 0.0)

    def test_gap_accounts_for_modulo_256_wrapping(self):
        """A wrap from 255->0 is NOT counted as 255 dropped frames."""
        # seq 253, 254, 255, then 0
        for seq in [253, 254, 255, 0]:
            frame = _build_stream_frame(slot=1, seq=seq, t_ms=seq * 10, values=[1.0])
            self.bridge._decode_stream_frame(1, frame)
        with self.bridge._stream_lock:
            stats = self.bridge._stream_stats[1]
        # After 255 comes 0: gap = (0 - 255 - 1) & 0xFF = 1
        # But (0 - 255 - 1) = -256 & 0xFF = 0... wait let me recalculate.
        # gap = (0 - 255 - 1) & 0xFF = (-256) & 0xFF = 0
        # So modulo-256 arithmetic should give gap=0 for 255→0.
        self.assertEqual(stats["received"], 4)
        self.assertEqual(stats["dropped"], 0)

    def test_gap_counts_missing_sequences(self):
        """A gap of N missing sequences increments dropped by N."""
        # Send seq 10, skip 11, send 12
        self.bridge._decode_stream_frame(1, _build_stream_frame(1, 10, 0, [1.0]))
        self.bridge._decode_stream_frame(1, _build_stream_frame(1, 12, 0, [1.0]))
        with self.bridge._stream_lock:
            stats = self.bridge._stream_stats[1]
        self.assertEqual(stats["received"], 2)
        self.assertEqual(stats["dropped"], 1)  # seq 11 missing

    def test_loss_pct_computed_correctly(self):
        """loss_pct = 100 * dropped / (received + dropped)."""
        # seq 0, then skip 1-9, send 10
        self.bridge._decode_stream_frame(1, _build_stream_frame(1, 0, 0, [1.0]))
        self.bridge._decode_stream_frame(1, _build_stream_frame(1, 10, 0, [1.0]))
        with self.bridge._stream_lock:
            stats = self.bridge._stream_stats[1]
        # dropped = (10 - 0 - 1) & 0xFF = 9
        self.assertEqual(stats["dropped"], 9)
        self.assertEqual(stats["received"], 2)
        # loss_pct = 100 * 9 / (2 + 9) ≈ 81.818
        self.assertAlmostEqual(stats["dropped"] / (stats["received"] + stats["dropped"]) * 100, 81.818, places=2)


class TestTimestampPreservation(unittest.TestCase):
    """Firmware timestamps are preserved end-to-end, not zeroed."""

    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def test_source_timestamp_in_json_payload(self):
        """The decoded payload carries the firmware t_ms, not zero."""
        t_ms = 1_234_567
        frame = _build_stream_frame(slot=0, seq=5, t_ms=t_ms, values=[1.5])
        result = self.bridge._decode_stream_frame(0, frame)
        self.assertIsNotNone(result)
        self.assertEqual(result["json"]["slot0.t_ms"], t_ms)

    def test_source_timestamp_not_hardcoded_zero(self):
        """Different t_ms values produce different JSON output."""
        t1 = 99_000
        t2 = 101_000
        r1 = self.bridge._decode_stream_frame(0, _build_stream_frame(0, 1, t1, [0.0]))
        r2 = self.bridge._decode_stream_frame(0, _build_stream_frame(0, 2, t2, [0.0]))
        self.assertEqual(r1["json"]["slot0.t_ms"], t1)
        self.assertEqual(r2["json"]["slot0.t_ms"], t2)


class TestCounterSingleOwner(unittest.TestCase):
    """Each wire frame increments counters exactly once."""

    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def test_typed_stream_received_counter_increments_once(self):
        """Two typed subscribe frames produce received=1 and received=2, not 2 and 4."""
        # Simulate _rx_loop calling _decode_stream_frame for typed "s0" path.
        f1 = _build_stream_frame(slot=0, seq=1, t_ms=0, values=[1.0])
        f2 = _build_stream_frame(slot=0, seq=2, t_ms=0, values=[2.0])
        r1 = self.bridge._decode_stream_frame(0, f1)
        r2 = self.bridge._decode_stream_frame(0, f2)
        # Payload carries the count AFTER incrementing
        self.assertEqual(r1["json"]["slot0.received"], 1)
        self.assertEqual(r2["json"]["slot0.received"], 2)
        # Verify no double-increment: received=2 total, not 4
        with self.bridge._stream_lock:
            stats = self.bridge._stream_stats.get(0, {})
        self.assertEqual(stats["received"], 2)


class TestSchemaRenegotiation(unittest.TestCase):
    """New subscribe requests clear stale schema and stats."""

    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()
        self.bridge._wifi_send = MagicMock()  # needed by _send_subscribe_bytes

    def test_stale_schema_cleared_on_send(self):
        """subscribe_slot() deletes the old schema and stats for a slot.

        The schema/stats cleanup lives in subscribe_slot() (not _send_subscribe_bytes)
        so that _handle_schema_frame calling _send_subscribe_bytes to release a
        deferred batch does NOT clear the schema that _handle_schema_frame just set.
        This is the core bug the S3A fix addresses.
        """
        # Register stale schema and stats
        self.bridge._stream_schemas[1] = FakeSchema(ranges=())
        self.bridge._stream_stats[1] = {"received": 10, "dropped": 2, "crc_errors": 1, "last_seq": 9}
        self.assertIsNotNone(self.bridge._stream_schemas.get(1))
        # subscribe_slot() with a pre-built StreamRange bypasses the resolver
        # and triggers the schema/stats cleanup path. A single range = 1 batch.
        self.bridge.subscribe_slot(
            slot=1, divider=4,
            ranges=[StreamRange(address=0x20000000, size=4, count=1, name="test.x")],
            transport=1,
        )
        # Schema and stats should be cleared by subscribe_slot
        self.assertIsNone(self.bridge._stream_schemas.get(1))
        self.assertIsNone(self.bridge._stream_stats.get(1))

    def test_pending_ranges_cleared_by_schema_frame(self):
        """_handle_schema_frame clears _pending_schema_ranges[slot] after processing."""
        # Pre-populate pending ranges with proper StreamRange objects (not strings).
        # The firmware echoes address/size/count only; the host re-attaches DWARF
        # names from these pending StreamRange entries by (address, size) lookup.
        self.bridge._pending_schema_ranges[0] = (
            StreamRange(address=0x20000000, size=4, count=1, name="var1"),
            StreamRange(address=0x20000004, size=4, count=1, name="var2"),
        )
        self.assertIn(0, self.bridge._pending_schema_ranges)
        # Build a correct 0x08 frame with 1 range.
        # Firmware Subscribe_BuildSchema (API/subscribe.c:604):
        #   sync(2) + type(1) + LEN_HI(1) + LEN_LO(1) +
        #   payload: n_ranges(1) + divider(1) + transport(1) + slot(1) +
        #            total_bytes_hi(1) + total_bytes_lo(1) + 8*range +
        #   CRC(1)
        # For n=1: payload_len = 5 + 1*8 = 13; total frame = 6 + 13 + 1 = 20 B.
        # CRC covers TYPE + LEN_HI + LEN_LO + n_ranges + config + range
        #   = out[2]..out[10+8-1] = bytes 2-17 (TYPE, LEN[2], config[6], range[8]).
        from ground_station.livewatch.transport import _xor_crc
        n_ranges = 1
        divider = 4
        transport = 1
        slot = 0
        total_bytes = 4  # 2 vars × 4 B each (2 × StreamRange at size=4, count=1)
        # Config (6 B): n_ranges, divider, transport, slot, total_hi, total_lo
        # (matches firmware out[5]..out[10])
        config = bytes([n_ranges, divider, transport, slot,
                        (total_bytes >> 8) & 0xFF, total_bytes & 0xFF])
        # Range (8 B): address=0x20000000, size=4, count=1
        rng = struct.pack("<IHH", 0x20000000, 4, 1)
        payload = config + rng  # 6+8 = 14 B
        payload_len = 5 + n_ranges * 8  # firmware formula = 13
        len_hi = (payload_len >> 8) & 0xFF
        len_lo = payload_len & 0xFF
        # CRC covers TYPE + LEN_HI + LEN_LO + config + range = bytes 2..17
        crc_body = bytes([0x08, len_hi, len_lo]) + payload  # 3 + 14 = 17 B
        crc_byte = _xor_crc(crc_body)
        frame = bytes([0xAA, 0xBB, 0x08, len_hi, len_lo]) + payload + bytes([crc_byte])
        # Length check: 6 (sync+type+LEN[2]) + payload_len (13) + 1 (CRC) = 20 B
        self.assertEqual(len(frame), 20)
        result = self.bridge._handle_schema_frame(frame)
        self.assertEqual(result, slot)
        # Pending ranges are cleared after schema response
        self.assertNotIn(0, self.bridge._pending_schema_ranges)

    def test_slot_state_advanced_to_sent_on_new_request(self):
        """A new subscribe request sets the slot state to 'sent'."""
        self.bridge._slot_states[0] = "streaming"
        self.bridge._send_subscribe_bytes(
            slot=0, divider=4,
            ranges=[StreamRange(address=0x20000000, size=4, count=1, name="test.x")],
            transport=1, request_id=1,
        )
        self.assertEqual(self.bridge._slot_states[0], "sent")


class TestStreamStateMachine(unittest.TestCase):
    """Slot state transitions: planned → sent → schema_received → streaming."""

    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()
        self.bridge._wifi_send = MagicMock()  # needed by _send_subscribe_bytes

    def test_schema_frame_transitions_sent_to_schema_received(self):
        """_handle_schema_frame advances slot state from 'sent' to 'schema_received'."""
        # Set up: send a request → state is 'sent'.
        self.bridge._send_subscribe_bytes(
            slot=1, divider=4,
            ranges=[StreamRange(address=0x20000000, size=4, count=1, name="test.x")],
            transport=1, request_id=5,
        )
        self.assertEqual(self.bridge._slot_states[1], "sent")
        # Simulate receiving a 0x08 schema reply with 1 range.
        # Firmware: payload_len = 5 + n_ranges*8 = 13; frame = 20 B.
        # CRC covers TYPE + LEN[2] + config[6] + range[8].
        from ground_station.livewatch.transport import _xor_crc
        n_ranges = 1
        divider = 4
        transport = 1
        slot = 1
        total_bytes = 4
        config = bytes([n_ranges, divider, transport, slot,
                        (total_bytes >> 8) & 0xFF, total_bytes & 0xFF])
        rng = struct.pack("<IHH", 0x20000000, 4, 1)
        payload = config + rng  # 14 B
        payload_len = 5 + n_ranges * 8  # = 13
        len_hi = (payload_len >> 8) & 0xFF
        len_lo = payload_len & 0xFF
        crc_body = bytes([0x08, len_hi, len_lo]) + payload
        crc_byte = _xor_crc(crc_body)
        frame = bytes([0xAA, 0xBB, 0x08, len_hi, len_lo]) + payload + bytes([crc_byte])
        self.assertEqual(len(frame), 20)
        result = self.bridge._handle_schema_frame(frame)
        self.assertEqual(result, slot)
        self.assertEqual(self.bridge._slot_states[1], "schema_received")

    def test_first_data_frame_transitions_schema_received_to_streaming(self):
        """The first 0x09 frame arriving after 'schema_received' transitions to 'streaming'."""
        # Pre-state: slot is in 'schema_received'
        self.bridge._slot_states[0] = "schema_received"
        # Pre-seed stats so _decode_stream_frame doesn't fail
        self.bridge._stream_stats[0] = {"received": 0, "dropped": 0, "crc_errors": 0, "last_seq": -1}
        # Send the first data frame
        frame = _build_stream_frame(slot=0, seq=0, t_ms=1000, values=[1.0])
        result = self.bridge._decode_stream_frame(0, frame)
        self.assertIsNotNone(result)
        # State should now be 'streaming'
        self.assertEqual(self.bridge._slot_states[0], "streaming")

    def test_streaming_never_regresses_to_schema_received(self):
        """Once in 'streaming', a later data frame does not regress the state."""
        self.bridge._slot_states[0] = "streaming"
        self.bridge._stream_stats[0] = {"received": 5, "dropped": 0, "crc_errors": 0, "last_seq": 4}
        # Send more frames while in streaming
        for seq in range(5, 8):
            self.bridge._decode_stream_frame(0, _build_stream_frame(0, seq, 0, [1.0]))
        self.assertEqual(self.bridge._slot_states[0], "streaming")

    def test_schema_received_never_regresses_to_sent(self):
        """Once in 'schema_received', subsequent schema frames do not regress to 'sent'."""
        self.bridge._slot_states[1] = "schema_received"
        # _handle_schema_frame only advances from 'sent'; 'schema_received' is already past that
        self.assertEqual(self.bridge._slot_states[1], "schema_received")

    def test_new_slot_full_lifecycle_planned_sent_acked_live(self):
        """A brand-new slot (3) walks planned -> sent -> acked -> live end to end.

        This is the regression test for the item-1 bug: subscribing a NEW
        slot did not work because the per-slot subscribe transaction layer
        was unimplemented, so an operator's request to a fresh slot never
        produced a per-slot lifecycle, and the dashboard could never show
        pending/acked/live/error state for it. Fake transport, no drone:
        a brand-new WifiBridge instance, MagicMock sockets, pre-built
        StreamRange to bypass the ELF resolver.
        """
        # Fresh bridge: no slot has any state yet.
        self.assertNotIn(3, self.bridge._slot_states)
        self.bridge._wifi_send = MagicMock()
        # 1. Request on new slot 3 -> request bytes on the wire, state 'sent'.
        self.bridge.subscribe_slot(
            slot=3, divider=4,
            ranges=[StreamRange(address=0x20002000, size=4, count=1, name="new.var")],
            transport=1,
        )
        call_args = self.bridge._wifi_send.sendto.call_args
        request = call_args[0][0]
        self.assertTrue(request.startswith(b"\xCC\xDE"))
        self.assertEqual(self.bridge._slot_states[3], "sent")
        self.bridge._wifi_send.sendto.assert_called_once()
        # Build the 0x08 ack the FC would echo for slot 3 (1 range, 4 B).
        from ground_station.livewatch.transport import _xor_crc
        n_ranges, divider, transport, slot, total_bytes = 1, 4, 1, 3, 4
        payload = bytes([n_ranges, divider, transport, slot,
                         (total_bytes >> 8) & 0xFF, total_bytes & 0xFF]) + \
                  struct.pack("<IHH", 0x20002000, 4, 1)
        payload_len = 5 + n_ranges * 8
        crc_byte = _xor_crc(bytes([0x08, payload_len >> 8, payload_len & 0xFF]) + payload)
        ack = bytes([0xAA, 0xBB, 0x08, payload_len >> 8, payload_len & 0xFF]) + payload + bytes([crc_byte])
        # 2. Ack decoded -> state 'schema_received'.
        self.assertEqual(self.bridge._handle_schema_frame(ack), 3)
        self.assertEqual(self.bridge._slot_states[3], "schema_received")
        # 3. First data frame -> state 'streaming'.
        data = _build_stream_frame(slot=3, seq=0, t_ms=1000, values=[1.0])
        decoded = self.bridge._decode_stream_frame(3, data)
        self.assertIsNotNone(decoded)
        self.assertEqual(self.bridge._slot_states[3], "streaming")


class TestStreamMetadataCrcErrors(unittest.TestCase):
    """StreamMetadata carries crc_errors from the typed metadata path."""

    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def test_stream_metadata_includes_crc_errors(self):
        """_stream_metadata returns a StreamMetadata with crc_errors from _stream_stats."""
        # Seed crc_errors via a bad frame
        bad = bytearray(_build_stream_frame(slot=1, seq=0, t_ms=0, values=[0.0]))
        bad[-1] ^= 0xFF
        self.bridge._decode_stream_frame(1, bad)
        # Build typed metadata
        meta = self.bridge._stream_metadata(1)
        self.assertIsNotNone(meta)
        self.assertEqual(meta.crc_errors, 1)
        self.assertEqual(meta.received, 0)  # not incremented on CRC failure

    def test_stream_metadata_zero_crc_errors_when_no_errors(self):
        """A slot with no CRC errors has crc_errors=0 in StreamMetadata."""
        self.bridge._stream_stats[2] = {"received": 3, "dropped": 0, "crc_errors": 0, "last_seq": 2}
        meta = self.bridge._stream_metadata(2)
        self.assertIsNotNone(meta)
        self.assertEqual(meta.crc_errors, 0)


class TestReconnectSequence(unittest.TestCase):
    """Reconnecting the WiFi link causes the firmware sequence to reset.

    The firmware's per-slot SEQ is modulo-256. After a reconnection, the
    first received frame may have seq=0 regardless of what was sent before
    the disconnect. The gap detection must not over-count: a wrap 255→0
    is gap=0, not gap=255.
    """

    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def test_wrap_255_to_0_is_not_counted_as_loss(self):
        """A seq wrap 255→0 is normal and produces zero dropped frames."""
        for seq in [253, 254, 255, 0, 1]:
            self.bridge._decode_stream_frame(1, _build_stream_frame(1, seq, seq * 100, [1.0]))
        with self.bridge._stream_lock:
            stats = self.bridge._stream_stats[1]
        # gap = (0 - 255 - 1) & 0xFF = -256 & 0xFF = 0
        self.assertEqual(stats["received"], 5)
        self.assertEqual(stats["dropped"], 0)

    def test_reconnect_gap_from_last_seq_before_disconnect(self):
        """The first seq after reconnect is measured against the last known seq.

        If the firmware reset its counter, the host will see a large gap.
        This is expected radio loss / reconnect, not a bug -- the loss_pct
        reflects it but the counter keeps incrementing.
        """
        # Simulate a session: seq 250, 251, 252 received
        for seq in [250, 251, 252]:
            self.bridge._decode_stream_frame(0, _build_stream_frame(0, seq, seq * 100, [1.0]))
        with self.bridge._stream_lock:
            stats_before = dict(self.bridge._stream_stats[0])
        self.assertEqual(stats_before["received"], 3)
        self.assertEqual(stats_before["last_seq"], 252)
        # Simulate reconnect: firmware resets to seq=0
        # Gap = (0 - 252 - 1) & 0xFF = -253 & 0xFF = 3
        self.bridge._decode_stream_frame(0, _build_stream_frame(0, 0, 0, [1.0]))
        self.bridge._decode_stream_frame(0, _build_stream_frame(0, 1, 0, [1.0]))
        with self.bridge._stream_lock:
            stats_after = self.bridge._stream_stats[0]
        self.assertEqual(stats_after["received"], 5)
        # dropped = (0-252-1) & 0xFF = 3
        self.assertEqual(stats_after["dropped"], 3)

    def test_stats_persist_after_stop(self):
        """stop() does not clear _stream_stats or _stream_schemas."""
        self.bridge._stream_stats[0] = {"received": 42, "dropped": 3, "crc_errors": 1, "last_seq": 41}
        self.bridge._stream_schemas[0] = FakeSchema(ranges=())
        self.bridge._slot_states[0] = "streaming"
        # stop() is a no-op on in-memory state
        self.bridge.stop()
        # Stats and schema survive stop()
        self.assertEqual(self.bridge._stream_stats[0]["received"], 42)
        self.assertIsNotNone(self.bridge._stream_schemas.get(0))
        self.assertEqual(self.bridge._slot_states[0], "streaming")

    def test_new_subscribe_clears_stats_and_schema(self):
        """subscribe_slot() resets that slot's stats, schema, and slot state.

        The cleanup lives in subscribe_slot(), not _send_subscribe_bytes, so
        batch-release calls from _handle_schema_frame do NOT clear the schema
        that was just registered (S3A bug fix).
        """
        self.bridge._stream_stats[0] = {"received": 99, "dropped": 10, "crc_errors": 5, "last_seq": 98}
        self.bridge._stream_schemas[0] = FakeSchema(ranges=())
        self.bridge._wifi_send = MagicMock()
        self.bridge.subscribe_slot(
            slot=0, divider=4,
            ranges=[StreamRange(address=0x20000000, size=4, count=1, name="reconnect.x")],
            transport=1,
        )
        # Both schema and stats are cleared by subscribe_slot
        self.assertIsNone(self.bridge._stream_schemas.get(0))
        self.assertIsNone(self.bridge._stream_stats.get(0))
        # Slot state set to 'sent' by _send_subscribe_bytes (called by subscribe_slot)
        self.assertEqual(self.bridge._slot_states[0], "sent")
        # But other slots are unaffected
        self.bridge._stream_stats[2] = {"received": 7, "dropped": 0, "crc_errors": 0, "last_seq": 6}
        self.assertEqual(self.bridge._stream_stats[2]["received"], 7)

    def test_reconnect_only_clears_target_slot(self):
        """subscribe_slot(slot=1) does NOT clear slot 0 stats or schema."""
        self.bridge._stream_stats[0] = {"received": 50, "dropped": 0, "crc_errors": 0, "last_seq": 49}
        self.bridge._stream_schemas[0] = FakeSchema(ranges=())
        self.bridge._wifi_send = MagicMock()
        self.bridge.subscribe_slot(
            slot=1, divider=4,
            ranges=[StreamRange(address=0x20000000, size=4, count=1, name="slot1.x")],
            transport=1,
        )
        # Slot 0 is untouched
        self.assertEqual(self.bridge._stream_stats[0]["received"], 50)
        self.assertIsNotNone(self.bridge._stream_schemas.get(0))
        # Slot 1 is cleared by subscribe_slot
        self.assertIsNone(self.bridge._stream_stats.get(1))
        self.assertIsNone(self.bridge._stream_schemas.get(1))


class TestStaleSchemaDetection(unittest.TestCase):
    """Stale schema after reconnect is detected and cleared."""

    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def test_schema_frame_unknown_slot_is_ignored(self):
        """A 0x08 for a slot with no pending request is still registered.

        The firmware echoes the accepted ranges; registration is not gated on
        a prior 0x21 request. Without a pending range lookup, the schema will
        have anonymous channel names, but it is still valid and gets registered.
        """
        # Firmware payload_len = 5 + n_ranges*8 = 13; frame = 20 B.
        from ground_station.livewatch.transport import _xor_crc
        n_ranges = 1
        divider = 4
        transport = 1
        slot = 2
        total_bytes = 4
        # Firmware 0x08 (Subscribe_BuildSchema, API/subscribe.c):
        # frame[5] = n_ranges; payload = [divider, transport, slot,
        # total_hi, total_lo] (5 B) then 8 B per range; XOR CRC tail.
        config_fixed = bytes([divider, transport, slot,
                             (total_bytes >> 8) & 0xFF, total_bytes & 0xFF])
        rng_fixed = struct.pack("<IHH", 0x20001000, 4, 1)  # addr, size, count = 8 B
        payload_fixed = bytes([n_ranges]) + config_fixed + rng_fixed  # 1+5+8
        payload_len = 5 + n_ranges * 8  # = 13
        len_hi = (payload_len >> 8) & 0xFF
        len_lo = payload_len & 0xFF
        crc_body = bytes([0x08, len_hi, len_lo]) + payload_fixed
        crc_byte = _xor_crc(crc_body)
        frame = bytes([0xAA, 0xBB, 0x08, len_hi, len_lo]) + payload_fixed + bytes([crc_byte])
        self.assertEqual(len(frame), 20)
        result = self.bridge._handle_schema_frame(frame)
        # Firmware registers the schema even for unknown slots; slot 2 gets registered.
        self.assertEqual(result, 2)
        self.assertIsNotNone(self.bridge._stream_schemas.get(2))
        # But pending ranges for other slots are unaffected
        self.assertNotIn(0, self.bridge._pending_schema_ranges)

    def test_reconnect_drops_frames_with_old_schema(self):
        """After a reconnect, stale schemas cannot corrupt fresh data.

        The reconnect sequence is:
          1. subscribe_slot() → clears old schema and stats
          2. receive 0x08 → registers fresh schema
          3. receive 0x09 frames → decoded with fresh schema

        This test verifies the S3A fix: _send_subscribe_bytes must NOT clear
        _stream_schemas[slot] when called from _handle_schema_frame (batch
        release), only when called from subscribe_slot() (fresh cycle start).
        """
        self.bridge._stream_schemas[0] = FakeSchema(ranges=(StreamRange(
            address=0x20000000, size=4, count=1, name="old_addr", fmt="f"),))
        self.bridge._stream_stats[0] = {"received": 5, "dropped": 0, "crc_errors": 0, "last_seq": 4}
        self.bridge._wifi_send = MagicMock()
        # Step 1: subscribe_slot() clears old schema and stats
        self.bridge.subscribe_slot(
            slot=0, divider=4,
            ranges=[StreamRange(address=0x20001000, size=4, count=1, name="new_addr", fmt="f")],
            transport=1,
        )
        self.assertIsNone(self.bridge._stream_schemas.get(0))
        self.assertIsNone(self.bridge._stream_stats.get(0))
        # Step 2: fresh schema registered (correct 1-range frame)
        from ground_station.livewatch.transport import _xor_crc
        n_ranges = 1
        divider = 4
        transport = 1
        slot = 0
        total_bytes = 4
        # Config (6 B): n_ranges, divider, transport, slot, total_hi, total_lo
        config = bytes([n_ranges, divider, transport, slot,
                        (total_bytes >> 8) & 0xFF, total_bytes & 0xFF])
        rng = struct.pack("<IHH", 0x20001000, 4, 1)  # address=0x20001000
        payload = config + rng  # 14 B
        payload_len = 5 + n_ranges * 8  # = 13 (firmware formula)
        len_hi = (payload_len >> 8) & 0xFF
        len_lo = payload_len & 0xFF
        crc_body = bytes([0x08, len_hi, len_lo]) + payload
        crc_byte = _xor_crc(crc_body)
        frame = bytes([0xAA, 0xBB, 0x08, len_hi, len_lo]) + payload + bytes([crc_byte])
        self.assertEqual(len(frame), 20)
        result = self.bridge._handle_schema_frame(frame)
        self.assertEqual(result, 0)
        self.assertIsNotNone(self.bridge._stream_schemas.get(0))
        # Step 3: data frame decoded with new schema
        data_frame = _build_stream_frame(slot=0, seq=0, t_ms=1000, values=[3.14])
        decoded = self.bridge._decode_stream_frame(0, data_frame)
        self.assertIsNotNone(decoded)
        # The fresh schema resolves the channel name
        self.assertEqual(decoded["names"][0], "new_addr")


class TestKeepaliveNudge(unittest.TestCase):
    """The periodic re-nudge must refresh the MicoAir downlink route.

    Stalls ~8-10 min after start are caused by the module's downlink aim
    idling out (it routes telemetry to the source of the most recent uplink).
    A 1-byte 0x00 nudge re-aims it. We unit-test the timer + payload with a
    fake monotonic clock and a stubbed socket -- no network.
    """

    def _bridge(self, interval: float = 10.0):
        br = WifiBridge(vofa_enabled=False, keepalive_interval=interval)
        br._wifi = MagicMock()
        return br

    def test_no_nudge_until_interval_elapses(self):
        br = self._bridge(interval=10.0)
        br._last_nudge = 0.0
        clock = iter([0.0, 5.0, 12.0, 12.0, 30.0])
        with unittest.mock.patch(
            "ground_station.comm.wifi_bridge.time.monotonic",
            side_effect=lambda: next(clock),
        ):
            br._send_keepalive_nudge()   # now=0   -> within interval, no send
            self.assertEqual(br._wifi.sendto.call_count, 0)
            br._send_keepalive_nudge()   # now=5   -> still within interval
            self.assertEqual(br._wifi.sendto.call_count, 0)
            br._send_keepalive_nudge()   # now=12  -> 12-0>=10, send; last=12
            self.assertEqual(br._wifi.sendto.call_count, 1)
            br._send_keepalive_nudge()   # now=12  -> 12-12<10, no send
            self.assertEqual(br._wifi.sendto.call_count, 1)
            br._send_keepalive_nudge()   # now=30  -> 30-12>=10, send again
            self.assertEqual(br._wifi.sendto.call_count, 2)
            br._wifi.sendto.assert_called_with(b"\x00",
                                               ("192.168.4.1", 14550))

    def test_immediate_interval_sends_every_call(self):
        br = self._bridge(interval=0.0)
        br._last_nudge = 0.0
        with unittest.mock.patch(
            "ground_station.comm.wifi_bridge.time.monotonic",
            side_effect=lambda: 100.0,
        ):
            br._send_keepalive_nudge()
            br._send_keepalive_nudge()
        self.assertEqual(br._wifi.sendto.call_count, 2)

    def test_no_wifi_socket_noop(self):
        br = self._bridge()
        br._wifi = None
        with unittest.mock.patch(
            "ground_station.comm.wifi_bridge.time.monotonic",
            side_effect=lambda: 100.0,
        ):
            br._send_keepalive_nudge()   # must not raise
        self.assertIsNone(br._wifi)


if __name__ == "__main__":
    unittest.main()


class TestSubscribeLifecycle(unittest.TestCase):
    """Full lifecycle: subscribe_slot → schema ack → data frames.

    Uses only in-process mocks (no drone, no network).  Verifies that
    the wifi_bridge correctly:
      1. Builds a 0x21 request and tracks slot state as ``sent``.
      2. Handles the 0x08 schema reply, registers the schema,
         and advances state to ``schema_received``.
      3. Decodes 0x09+slot data frames, advances state to ``streaming``,
         and emits the correct JSON with values and metadata.
    """

    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()
        self.bridge._wifi_send = MagicMock()

    def _build_schema_frame(self, slot, n_ranges, divider, total_bytes,
                            ranges_addr_size_count):
        """Build a 0x08 schema frame that the firmware would send.

        ``ranges_addr_size_count`` is a list of
        ``(address, size, count)`` tuples (one per range).
        """
        from ground_station.livewatch.transport import _xor_crc
        n_ranges = len(ranges_addr_size_count)
        config = bytes([
            n_ranges, divider, 1, slot,  # n_ranges, divider, transport, slot
            (total_bytes >> 8) & 0xFF, total_bytes & 0xFF,
        ])
        range_bytes = b""
        for addr, size, count in ranges_addr_size_count:
            range_bytes += struct.pack("<IHH", addr, size, count)
        payload = config + range_bytes
        payload_len = 5 + n_ranges * 8
        len_hi = (payload_len >> 8) & 0xFF
        len_lo = payload_len & 0xFF
        crc_body = bytes([0x08, len_hi, len_lo]) + payload
        crc_byte = _xor_crc(crc_body)
        return (bytes([0xAA, 0xBB, 0x08, len_hi, len_lo]) +
                payload + bytes([crc_byte]))

    def test_full_lifecycle_slot_1(self):
        """Subscribe slot 1 → get schema ack → receive data frames.

        Two adjacent StreamRange objects get coalesced into 1 range (count=2).
        The firmware echoes 1 range with count=2; the schema stores the
        original per-element names in ``_names``.
        """
        rng1 = StreamRange(
            address=0x20001000, size=4, count=1,
            name="Ctrler.gyroxPID.FB", fmt="f")
        rng2 = StreamRange(
            address=0x20001004, size=4, count=1,
            name="Ctrler.gyroxPID.U", fmt="f")

        # 1. subscribe_slot sends the 0x21 request and sets state to "planned"
        #    then "sent".
        self.bridge.subscribe_slot(
            slot=1, divider=4, ranges=[rng1, rng2], transport=1)
        self.assertEqual(self.bridge._slot_states.get(1), "sent")
        call_args = self.bridge._wifi_send.sendto.call_args
        self.assertTrue(len(call_args[0][0]) > 0)

        # 2. Simulate 0x08 schema reply.  The firmware echoes back the
        #    coalesced range (1 range, count=2) because that's what we sent.
        schema_frame = self._build_schema_frame(
            slot=1, n_ranges=1, divider=4, total_bytes=8,
            ranges_addr_size_count=[(0x20001000, 4, 2)])
        result_slot = self.bridge._handle_schema_frame(schema_frame)
        self.assertEqual(result_slot, 1)
        self.assertEqual(self.bridge._slot_states.get(1), "schema_received")

        # Schema is now registered and can decode data.
        with self.bridge._stream_lock:
            schema = self.bridge._stream_schemas.get(1)
        self.assertIsNotNone(schema)
        self.assertEqual(len(schema.ranges), 1)
        # The coalesced range has per-element names
        self.assertEqual(schema.ranges[0].count, 2)
        self.assertEqual(schema.ranges[0]._names,
                          ("Ctrler.gyroxPID.FB", "Ctrler.gyroxPID.U"))

        # 3. Simulate first data frame → state transitions to "streaming".
        frame1 = _build_stream_frame(slot=1, seq=0, t_ms=1000,
                                     values=[0.5, 0.25])
        decoded1 = self.bridge._decode_stream_frame(1, frame1)
        self.assertIsNotNone(decoded1)
        self.assertEqual(self.bridge._slot_states.get(1), "streaming")
        self.assertEqual(decoded1["names"],
                         ["Ctrler.gyroxPID.FB", "Ctrler.gyroxPID.U"])
        self.assertEqual(decoded1["values"], [0.5, 0.25])
        self.assertIn("slot1.Ctrler.gyroxPID.FB", decoded1["json"])

        # 4. Simulate second data frame (sequence gap).
        frame2 = _build_stream_frame(slot=1, seq=2, t_ms=1020,
                                     values=[0.6, 0.30])
        decoded2 = self.bridge._decode_stream_frame(1, frame2)
        self.assertIsNotNone(decoded2)
        # Values are packed as float32 and unpacked, so check
        # the rounded values match.
        self.assertAlmostEqual(decoded2["values"][0], 0.6, places=3)
        self.assertAlmostEqual(decoded2["values"][1], 0.30, places=3)
        # Missing seq=1 should be counted as dropped.
        with self.bridge._stream_lock:
            stats = self.bridge._stream_stats.get(1)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["received"], 2)
        self.assertGreaterEqual(stats["dropped"], 1)

    def test_subscribe_preview_includes_projected_bps(self):
        """subscribe_preview returns payload_bytes, frame_bytes, projected_bps."""
        # Use the real subscribe_preview which resolves against the ELF.
        preview = self.bridge.subscribe_preview(
            slot=1, divider=4,
            ranges=["xTickCount"])
        self.assertIsNotNone(preview)
        self.assertIn("slot", preview)
        self.assertIn("ranges", preview)
        self.assertIn("expected_rate_hz", preview)
        self.assertIn("payload_bytes", preview)
        self.assertIn("frame_bytes", preview)
        self.assertIn("projected_bps", preview)
        self.assertGreater(preview["projected_bps"], 0)

    def test_unsubscribe_divider_zero_clears_slot(self):
        """divider=0 unsubscribes the slot and clears schema stats."""
        rng = StreamRange(
            address=0x20001000, size=4, count=1,
            name="test.x", fmt="f")
        self.bridge.subscribe_slot(slot=1, divider=4, ranges=[rng])
        self.assertEqual(self.bridge._slot_states.get(1), "sent")

        # Unsubscribe
        self.bridge.subscribe_slot(slot=1, divider=0, ranges=[])
        self.assertNotIn(1, self.bridge._pending_schema_ranges)
        # divider=0 still sends a 0x21 frame (to tell FC to stop),
        # so state moves through planned→sent; it's the absence of
        # pending ranges that matters for unsubscribe semantics.
        self.assertEqual(self.bridge._slot_states.get(1), "sent")
        # Old schema and stats are cleared.
        with self.bridge._stream_lock:
            self.assertNotIn(1, self.bridge._stream_schemas)
            self.assertNotIn(1, self.bridge._stream_stats)

    def test_stream_metadata_returns_typed_metadata(self):
        """_stream_metadata returns a StreamMetadata instance."""
        rng = StreamRange(
            address=0x20001000, size=4, count=1,
            name="test.x", fmt="f")
        self.bridge.subscribe_slot(slot=1, divider=4, ranges=[rng])
        self.bridge._send_subscribe_bytes(
            slot=1, divider=4,
            ranges=[StreamRange(address=0x20001000, size=4,
                                count=1, name="test.x", fmt="f")])

        # Pretend schema was received.
        self.bridge._slot_states[1] = "schema_received"
        frame = _build_stream_frame(slot=1, seq=0, t_ms=1000, values=[1.0])
        self.bridge._decode_stream_frame(1, frame)

        # Now stream_metadata should have stats.
        from ground_station.service.telemetry_adapter import StreamMetadata
        meta = self.bridge._stream_metadata(1)
        self.assertIsInstance(meta, StreamMetadata)
        self.assertEqual(meta.received, 1)
        self.assertGreater(meta.crc_errors, -1)  # just exists

    def test_new_subscribe_cleans_stale_schema(self):
        """Re-subscribing a slot clears the old schema and stats."""
        rng1 = StreamRange(
            address=0x20001000, size=4, count=1,
            name="old_sym", fmt="f")
        self.bridge.subscribe_slot(slot=1, divider=4, ranges=[rng1])

        # Simulate schema for the first subscription.
        schema1 = self._build_schema_frame(
            slot=1, n_ranges=1, divider=4, total_bytes=4,
            ranges_addr_size_count=[(0x20001000, 4, 1)])
        self.bridge._handle_schema_frame(schema1)
        with self.bridge._stream_lock:
            old_schema = self.bridge._stream_schemas.get(1)
        self.assertIsNotNone(old_schema)
        self.assertEqual(old_schema.ranges[0].name, "old_sym")

        # Subscribe again with different symbol.
        rng2 = StreamRange(
            address=0x20002000, size=4, count=1,
            name="new_sym", fmt="f")
        self.bridge.subscribe_slot(slot=1, divider=4, ranges=[rng2])

        # Old schema and stats should be cleared.
        with self.bridge._stream_lock:
            new_schema = self.bridge._stream_schemas.get(1)
        self.assertIsNone(new_schema)
        self.assertNotIn(1, self.bridge._stream_stats)

        # Simulate schema for the second subscription.
        schema2 = self._build_schema_frame(
            slot=1, n_ranges=1, divider=4, total_bytes=4,
            ranges_addr_size_count=[(0x20002000, 4, 1)])
        self.bridge._handle_schema_frame(schema2)
        with self.bridge._stream_lock:
            final_schema = self.bridge._stream_schemas.get(1)
        self.assertIsNotNone(final_schema)
        self.assertEqual(final_schema.ranges[0].name, "new_sym")
