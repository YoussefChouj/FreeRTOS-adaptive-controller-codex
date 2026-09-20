"""Tests for the 50-53 B "DataBuf_to_linux-style" fallback -- BEHAVIOUR CHANGED 2026-09-17.

Historical context (2026-08-20 → 2026-09-17): the live MicoAir downlink
carries 50-53 B datagrams at ~98 Hz with no recognisable magic byte at
any offset 0..4, even though TASK/send_data.c specifies a 4-byte 0xAA 0xAA
0x00 0x00 header for `DataBuf_to_linux` (line 283-286). The original
workaround treated those bytes as 12 LE float32 values in a slot-less
"data" tag -- but the service's `_resolve_tag_slot` then routed that tag
into a phantom `streams["-1"]` entry that the dashboard sidebar showed as
a 700-second-stale row full of garbage floats (`f0: -2.7e-43`, etc.).

Per PLANNING_PROMPT.md §1 the 50-53 B path was the wrong assumption. The
decoder no longer fabricates a "data" tag from those bytes; the partial-
frame handler keeps the buffer intact until a header byte (0xFE / 0xAA
0xAA / 0xAA 0xBB / 0x7F etc.) arrives to anchor a real frame. 50-53 B
buffers therefore fall through to `None` (wait for more data).

The deeper diagnosis (FC vs ELF drift on the DataBuf_to_linux header)
is still tracked in CLAUDE.md session state -- if/when that root cause
is fixed in the firmware, this branch can be reintroduced WITH a real
header check rather than the previous size-window heuristic.
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
    """Behaviour after the 2026-09-17 phantom-slot fix.

    50-53 B datagrams are NOT decoded by wifi_bridge anymore. The previous
    work-around produced a `("data", payload)` tuple that the service
    routed into a phantom `streams["-1"]` slot full of garbage floats.
    """

    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def test_52b_frame_no_longer_decoded(self):
        """The 52 B 'DataBuf_to_linux' cluster must NOT be decoded anymore.

        Returning a fabricated "data" tag here was the source of the
        phantom `streams["-1"]` slot in /state. The buffer should now be
        left untouched so the partial-frame handler can wait for a
        properly-headered frame to arrive (or the 1024 B resync kicks
        in if it really is unbounded noise).
        """
        values = [0.1 * (i + 1) for i in range(12)]  # 0.1, 0.2, ..., 1.2
        frame = _build_52b_dataframe(values, pad_to=52)
        self.assertEqual(len(frame), 52)
        buf = bytearray(frame)
        result = self.bridge._parse_one(buf)
        self.assertIsNone(result)
        # Buffer is unchanged (wait-for-more-data semantics).
        self.assertEqual(len(buf), 52)

    def test_50b_frame_not_decoded(self):
        """A 50 B frame (the lower edge of the historical cluster) must not decode."""
        values = [float(i) for i in range(12)]
        frame = _build_52b_dataframe(values, pad_to=50)
        buf = bytearray(frame)
        result = self.bridge._parse_one(buf)
        self.assertIsNone(result)

    def test_53b_frame_not_decoded(self):
        """A 53 B frame (the upper edge of the historical cluster) must not decode."""
        values = [float(i) for i in range(12)]
        frame = _build_52b_dataframe(values, pad_to=53)
        buf = bytearray(frame)
        result = self.bridge._parse_one(buf)
        self.assertIsNone(result)

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