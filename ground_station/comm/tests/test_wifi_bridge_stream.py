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


if __name__ == "__main__":
    unittest.main()
