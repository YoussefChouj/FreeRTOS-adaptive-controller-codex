"""Tests for the 50-53 B "DataBuf_to_linux-style" fallback added 2026-08-20.

The live MicoAir downlink carries 50-53 B datagrams at ~98 Hz with no
recognisable magic byte at any offset 0..4, even though TASK/send_data.c
specifies a 4-byte 0xAA 0xAA 0x00 0x00 header for `DataBuf_to_linux`
(line 283-286). The bytes that arrive look like 12 LE float32 values
with no header; the wifi_bridge's existing decoder rejects all of them.

The new path treats 50-53 B datagrams as 12 LE float32 + variable tail.
This is a workaround, not a permanent fix -- the deeper diagnosis
(FC vs ELF drift) is tracked in CLAUDE.md session state.
"""
from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from ground_station.comm.wifi_bridge import WifiBridge


def _build_52b_dataframe(values, pad_to=52):
    """Build a `pad_to`-B frame with 12 LE float32 values. No magic header.

    The default pads to 52 B (4 B tail after the 48 B of floats) to match
    the dominant wire-cluster size. The `pad_to` parameter is overridden
    by the 50 B / 53 B tests to exercise the size-window edges.
    """
    assert len(values) == 12
    floats = b"".join(struct.pack("<f", float(v)) for v in values)
    assert len(floats) == 48
    return floats + b"\x00" * (pad_to - 48)


class TestDataBufFrame(unittest.TestCase):
    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def test_52b_frame_decodes_as_12_floats(self):
        """50-53 B datagrams with no magic must be accepted as 12-float payloads."""
        values = [0.1 * (i + 1) for i in range(12)]  # 0.1, 0.2, ..., 1.2
        frame = _build_52b_dataframe(values, pad_to=52)
        self.assertEqual(len(frame), 52)
        buf = bytearray(frame)
        result = self.bridge._parse_one(buf)
        self.assertIsNotNone(result)
        tag, payload = result
        self.assertEqual(tag, "data")
        self.assertEqual(payload["len"], 52)
        for i in range(12):
            self.assertAlmostEqual(payload[f"f{i}"], values[i], places=4)
        # The 52 B frame must be fully consumed.
        self.assertEqual(len(buf), 0)

    def test_50b_frame_accepted(self):
        """A 50 B frame (still in the dominant cluster) must decode."""
        values = [float(i) for i in range(12)]
        frame = _build_52b_dataframe(values, pad_to=50)
        self.assertEqual(len(frame), 50)
        buf = bytearray(frame)
        result = self.bridge._parse_one(buf)
        self.assertIsNotNone(result)
        tag, payload = result
        self.assertEqual(tag, "data")
        self.assertEqual(payload["len"], 50)

    def test_53b_frame_accepted(self):
        """A 53 B frame (the upper end of the cluster) must decode."""
        values = [float(i) for i in range(12)]
        frame = _build_52b_dataframe(values, pad_to=53)
        self.assertEqual(len(frame), 53)
        buf = bytearray(frame)
        result = self.bridge._parse_one(buf)
        self.assertIsNotNone(result)
        tag, payload = result
        self.assertEqual(tag, "data")
        self.assertEqual(payload["len"], 53)

    def test_49b_frame_rejected(self):
        """49 B is below the cluster range and must not be picked up by this path.

        The 49 B size does appear on the wire (likely a partial frame or
        link-layer keepalive) but it is not a DataBuf_to_linux-style payload.
        Other paths (0xFE / 0xAA 0xAA / 0xAA 0xBB / JustFloat attitude) have
        already been tried by the time we reach this point, so a 49 B buffer
        falls through to "wait for more data"."""
        frame = b"\x00" * 49
        buf = bytearray(frame)
        result = self.bridge._parse_one(buf)
        self.assertIsNone(result)

    def test_54b_frame_rejected(self):
        """54 B is above the cluster range and must not be picked up here.

        54 B and above are not seen on the live wire at any significant
        rate. If one ever shows up it will fall through to the partial-frame
        handler."""
        frame = b"\x00" * 54
        buf = bytearray(frame)
        result = self.bridge._parse_one(buf)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()