"""Tests for the decode pipeline: delta decode, float16, RLE."""
from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from ground_station.comm.decode import (
    DecodePipeline,
    _float16_to_float32,
)
from ground_station.livewatch.stream import StreamRange, StreamSchema


def _make_schema(ranges: tuple[StreamRange, ...], slot: int = 0) -> StreamSchema:
    total = sum(r.nbytes for r in ranges)
    return StreamSchema(
        divider=1,
        transport=1,
        total_bytes=total,
        ranges=ranges,
        slot=slot,
    )


class TestFloat16RoundTrip(unittest.TestCase):
    """Verify _float16_to_float32 correctness with known Float16 bit-patterns.

    Float16 bit-patterns are the actual wire format — the firmware sends
    Float16 as uint16 in the frame payload. These are NOT float32 representations
    of the same value.
    """

    def test_zero_round_trip(self):
        # Float16 zero: all bits zero
        result = _float16_to_float32(0x0000)
        self.assertEqual(result, 0.0)

    def test_positive_normal_round_trip(self):
        # Float16 1.0: sign=0, exp=15, frac=0 -> bits = 0b0_01111_0000000000 = 0x3C00
        result = _float16_to_float32(0x3C00)
        self.assertAlmostEqual(result, 1.0, places=3)

    def test_negative_normal_round_trip(self):
        # Float16 -2.5: sign=1, exp=15 (bias), frac=0.25 -> bits = 0b1_10000_0100000000 = 0xC100
        result = _float16_to_float32(0xC100)
        self.assertAlmostEqual(result, -2.5, places=2)

    def test_subnormal_round_trip(self):
        # Float16 smallest positive subnormal: bits = 0b0_00000_0000000001 = 0x0001
        result = _float16_to_float32(0x0001)
        self.assertAlmostEqual(result, 5.96e-8, places=3)

    def test_inf_round_trip(self):
        # Float16 +inf: sign=0, exp=31, frac=0 -> bits = 0b0_11111_0000000000 = 0x7C00
        result = _float16_to_float32(0x7C00)
        self.assertEqual(result, float("inf"))

    def test_nan_round_trip(self):
        # Float16 NaN: sign=0, exp=31, frac!=0 -> bits = 0b0_11111_0000000001 = 0x7C01
        result = _float16_to_float32(0x7C01)
        self.assertTrue(result != result)  # NaN != NaN


class TestFloat16Detection(unittest.TestCase):
    def test_float16_channel_is_cast(self):
        ranges = (StreamRange(address=0x20000000, size=2, count=2),)
        schema = _make_schema(ranges)
        pipeline = DecodePipeline(schema, precision_map={0: "float16", 1: "float16"})

        # Raw uint16 bit-patterns for 1.0 and 2.0 in float16
        raw = struct.pack("<HH", 0x3C00, 0x4000)
        values, _ = pipeline.decode_frame(raw)

        self.assertAlmostEqual(values[0], 1.0, places=3)
        self.assertAlmostEqual(values[1], 2.0, places=3)

    def test_float32_channel_not_cast(self):
        ranges = (StreamRange(address=0x20000000, size=4, count=1),)
        schema = _make_schema(ranges)
        pipeline = DecodePipeline(schema, precision_map={0: "float32"})

        raw = struct.pack("<f", 1.5)
        values, _ = pipeline.decode_frame(raw)
        self.assertAlmostEqual(values[0], 1.5)


class TestDeltaDecode(unittest.TestCase):
    def test_first_sample_is_raw_value(self):
        ranges = (StreamRange(address=0x20000000, size=4, count=1),)
        schema = _make_schema(ranges)
        pipeline = DecodePipeline(schema)
        pipeline.reset()

        raw = struct.pack("<f", 1.23)
        values, _ = pipeline.decode_frame(raw)
        self.assertAlmostEqual(values[0], 1.23)

    def test_subsequent_samples_are_stored_as_is(self):
        """In v1 firmware sends full Float32 values, not deltas.
        Each frame decodes to its raw value, with no accumulation."""
        ranges = (StreamRange(address=0x20000000, size=4, count=1),)
        schema = _make_schema(ranges)
        pipeline = DecodePipeline(schema)
        pipeline.reset()

        # Frame 1: raw = 1.0 -> decoded = 1.0
        raw1 = struct.pack("<f", 1.0)
        values1, _ = pipeline.decode_frame(raw1)
        self.assertAlmostEqual(values1[0], 1.0)

        # Frame 2: raw = 0.5 -> decoded = 0.5 (no delta accumulation in v1)
        raw2 = struct.pack("<f", 0.5)
        values2, _ = pipeline.decode_frame(raw2)
        self.assertAlmostEqual(values2[0], 0.5)

        # Frame 3: raw = -0.2 -> decoded = -0.2
        raw3 = struct.pack("<f", -0.2)
        values3, _ = pipeline.decode_frame(raw3)
        self.assertAlmostEqual(values3[0], -0.2)

    def test_counter_skips_delta(self):
        ranges = (StreamRange(address=0x20000000, size=4, count=1),)
        schema = _make_schema(ranges)
        pipeline = DecodePipeline(schema, precision_map={0: "counter"})
        pipeline.reset()

        # Firmware sends uint32 counter as 4 raw bytes; DecodePipeline unpacks
        # those 4 bytes as uint32 little-endian to get the actual integer value.
        raw1 = struct.pack("<I", 100)
        values1, _ = pipeline.decode_frame(raw1)
        self.assertAlmostEqual(values1[0], 100.0)

        raw2 = struct.pack("<I", 101)
        values2, _ = pipeline.decode_frame(raw2)
        self.assertAlmostEqual(values2[0], 101.0)


class TestRLE(unittest.TestCase):
    def test_identical_values_increment_count(self):
        ranges = (StreamRange(address=0x20000000, size=4, count=1),)
        schema = _make_schema(ranges)
        pipeline = DecodePipeline(schema)
        pipeline.reset()

        raw = struct.pack("<f", 1.5)

        _, counts1 = pipeline.decode_frame(raw)
        self.assertEqual(counts1[0], 1)

        _, counts2 = pipeline.decode_frame(raw)
        self.assertEqual(counts2[0], 2)

        _, counts3 = pipeline.decode_frame(raw)
        self.assertEqual(counts3[0], 3)

    def test_changing_values_reset_count(self):
        ranges = (StreamRange(address=0x20000000, size=4, count=1),)
        schema = _make_schema(ranges)
        pipeline = DecodePipeline(schema)
        pipeline.reset()

        raw1 = struct.pack("<f", 1.0)
        _, c1 = pipeline.decode_frame(raw1)
        self.assertEqual(c1[0], 1)

        raw2 = struct.pack("<f", 1.0)
        _, c2 = pipeline.decode_frame(raw2)
        self.assertEqual(c2[0], 2)

        raw3 = struct.pack("<f", 2.0)
        _, c3 = pipeline.decode_frame(raw3)
        self.assertEqual(c3[0], 1)  # reset

    def test_different_channels_track_independently(self):
        ranges = (
            StreamRange(address=0x20000000, size=4, count=2),
        )
        schema = _make_schema(ranges)
        pipeline = DecodePipeline(schema)
        pipeline.reset()

        raw = struct.pack("<ff", 1.0, 2.0)
        _, c0 = pipeline.decode_frame(raw)
        self.assertEqual(c0[0], 1)
        self.assertEqual(c0[1], 1)

        raw_same = struct.pack("<ff", 1.0, 2.0)
        _, c1 = pipeline.decode_frame(raw_same)
        self.assertEqual(c1[0], 2)
        self.assertEqual(c1[1], 2)

        raw_chg = struct.pack("<ff", 1.0, 3.0)
        _, c2 = pipeline.decode_frame(raw_chg)
        self.assertEqual(c2[0], 3)  # still same -> keeps going
        self.assertEqual(c2[1], 1)  # ch1 changed -> reset


class TestReset(unittest.TestCase):
    def test_reset_clears_delta_state(self):
        ranges = (StreamRange(address=0x20000000, size=4, count=1),)
        schema = _make_schema(ranges)
        pipeline = DecodePipeline(schema)
        pipeline.reset()

        raw1 = struct.pack("<f", 1.0)
        pipeline.decode_frame(raw1)

        pipeline.reset()

        raw2 = struct.pack("<f", 0.5)
        values, _ = pipeline.decode_frame(raw2)
        self.assertAlmostEqual(values[0], 0.5)  # fresh, not 1.5


if __name__ == "__main__":
    unittest.main()
