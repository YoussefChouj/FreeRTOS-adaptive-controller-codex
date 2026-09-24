"""Byte-exact encoding tests for CMD 0x19 (MRAC Simplex) encoder."""
from __future__ import annotations

import struct
import unittest

from ground_station.comm.simplex_encoder import (
    SIMPLEX_CMD_ID,
    SIMPLEX_PARAM_MAP,
    build_simplex_frame,
    encode_simplex,
    encode_simplex_frame,
    get_param_index,
)


def _xor_crc8(data: bytes) -> int:
    """XOR checksum used by the 0xCC 0xDD protocol."""
    crc = 0
    for b in data:
        crc ^= b
    return crc


# ── Name mapping tests ──────────────────────────────────────────────────────

class TestParamMapping(unittest.TestCase):
    """Verify the named-param → (index, description) mapping."""

    def test_all_expected_names_present(self):
        expected_names = [
            "simplex.mode",
            "simplex.variant",
            "simplex.roll_max",
            "simplex.pitch_max",
            "simplex.w_norm_max",
            "simplex.sat_ticks_max",
            "simplex.hold_ticks",
            "simplex.reset_counters",
        ]
        for name in expected_names:
            self.assertIn(name, SIMPLEX_PARAM_MAP, f"{name} missing from map")

    def test_index_values_0_to_7(self):
        for i in range(8):
            for name, (idx, _desc) in SIMPLEX_PARAM_MAP.items():
                if idx == i:
                    break
            else:
                self.fail(f"No param found for index {i}")

    def test_unique_indexes(self):
        indexes = [idx for idx, _ in SIMPLEX_PARAM_MAP.values()]
        self.assertEqual(len(indexes), len(set(indexes)), "indexes must be unique")

    def test_cmd_id_is_0x19(self):
        for name, (idx, _desc) in SIMPLEX_PARAM_MAP.items():
            cmd_id, _, _ = encode_simplex(name, 0.0)
            self.assertEqual(cmd_id, 0x19, f"{name} has wrong cmd_id")

    def test_get_param_index(self):
        self.assertEqual(get_param_index("simplex.mode"), 0)
        self.assertEqual(get_param_index("simplex.variant"), 1)
        self.assertEqual(get_param_index("simplex.roll_max"), 2)
        self.assertEqual(get_param_index("simplex.pitch_max"), 3)
        self.assertEqual(get_param_index("simplex.w_norm_max"), 4)
        self.assertEqual(get_param_index("simplex.sat_ticks_max"), 5)
        self.assertEqual(get_param_index("simplex.hold_ticks"), 6)
        self.assertEqual(get_param_index("simplex.reset_counters"), 7)

    def test_unknown_name_raises(self):
        with self.assertRaises(KeyError):
            encode_simplex("simplex.nonexistent", 1.0)


# ── encode_simplex return value tests ───────────────────────────────────────

class TestEncodeSimplex(unittest.TestCase):
    """Verify encode_simplex returns correct (cmd_id, index, value)."""

    def test_mode(self):
        self.assertEqual(encode_simplex("simplex.mode", 1.0), (0x19, 0, 1.0))

    def test_variant(self):
        self.assertEqual(encode_simplex("simplex.variant", 0.0), (0x19, 1, 0.0))

    def test_roll_max(self):
        self.assertEqual(encode_simplex("simplex.roll_max", 3.14), (0x19, 2, 3.14))

    def test_pitch_max(self):
        self.assertEqual(encode_simplex("simplex.pitch_max", 1.57), (0x19, 3, 1.57))

    def test_w_norm_max(self):
        self.assertEqual(encode_simplex("simplex.w_norm_max", 0.5), (0x19, 4, 0.5))

    def test_sat_ticks_max(self):
        self.assertEqual(encode_simplex("simplex.sat_ticks_max", 40.0), (0x19, 5, 40.0))

    def test_hold_ticks(self):
        self.assertEqual(encode_simplex("simplex.hold_ticks", 200.0), (0x19, 6, 200.0))

    def test_reset_counters(self):
        self.assertEqual(encode_simplex("simplex.reset_counters", 1.0), (0x19, 7, 1.0))

    def test_value_preserved(self):
        name = "simplex.roll_max"
        _cid, _idx, val = encode_simplex(name, 2.71828)
        self.assertAlmostEqual(val, 2.71828, places=5)


# ── Byte-exact frame encoding tests ─────────────────────────────────────────

def _expected_frame(index: int, value: float) -> bytes:
    """Build the expected wire frame for a given (index, value)."""
    cmd_id = 0x19
    value_bytes = struct.pack("<f", value)
    header = bytes([0xCC, 0xDD, cmd_id, index])
    body = header + value_bytes
    crc = _xor_crc8(body[2:])  # XOR of cmd_id, index, value_bytes
    return body + bytes([crc])


class TestBuildSimplexFrame(unittest.TestCase):
    """Byte-exact tests for build_simplex_frame -- matches firmware expectation."""

    def _check_frame(self, name: str, value: float, expected_index: int) -> bytes:
        frame = build_simplex_frame(expected_index, value)

        # Frame length: 2 sync + 1 cmd + 1 index + 4 value + 1 crc = 9
        self.assertEqual(len(frame), 9, f"frame length should be 9, got {len(frame)}")

        # Header bytes
        self.assertEqual(frame[0], 0xCC, "sync byte 0")
        self.assertEqual(frame[1], 0xDD, "sync byte 1")
        self.assertEqual(frame[2], 0x19, "cmd_id 0x19")
        self.assertEqual(frame[3], expected_index, "index byte")

        # Value bytes round-trip
        read_value = struct.unpack("<f", frame[4:8])[0]
        self.assertAlmostEqual(read_value, value, places=5, msg=f"value mismatch for {name}")

        # CRC check
        computed_crc = _xor_crc8(frame[2:8])
        self.assertEqual(frame[8], computed_crc, "CRC8 mismatch")

        # Full frame matches expected
        expected = _expected_frame(expected_index, value)
        self.assertEqual(frame, expected, f"frame mismatch for {name}={value}")

        return frame

    def test_frame_idx0_mode(self):
        self._check_frame("simplex.mode", 1.0, 0)

    def test_frame_idx0_mode_zero(self):
        self._check_frame("simplex.mode", 0.0, 0)

    def test_frame_idx0_mode_two(self):
        self._check_frame("simplex.mode", 2.0, 0)

    def test_frame_idx1_variant(self):
        self._check_frame("simplex.variant", 0.0, 1)

    def test_frame_idx1_variant_one(self):
        self._check_frame("simplex.variant", 1.0, 1)

    def test_frame_idx2_roll_max(self):
        self._check_frame("simplex.roll_max", 3.14, 2)

    def test_frame_idx3_pitch_max(self):
        self._check_frame("simplex.pitch_max", 1.57, 3)

    def test_frame_idx4_w_norm_max(self):
        self._check_frame("simplex.w_norm_max", 1e6, 4)

    def test_frame_idx5_sat_ticks_max(self):
        self._check_frame("simplex.sat_ticks_max", 40.0, 5)

    def test_frame_idx6_hold_ticks(self):
        self._check_frame("simplex.hold_ticks", 200.0, 6)

    def test_frame_idx7_reset(self):
        self._check_frame("simplex.reset_counters", 1.0, 7)

    def test_frame_small_value(self):
        self._check_frame("simplex.roll_max", 0.01, 2)

    def test_frame_large_value(self):
        self._check_frame("simplex.w_norm_max", 1e5, 4)


# ── encode_simplex_frame end-to-end ─────────────────────────────────────────

class TestEncodeSimplexFrame(unittest.TestCase):
    """End-to-end: name + value → wire frame."""

    def test_encode_frame_mode(self):
        frame = encode_simplex_frame("simplex.mode", 1.0)
        expected = _expected_frame(0, 1.0)
        self.assertEqual(frame, expected)

    def test_encode_frame_variant(self):
        frame = encode_simplex_frame("simplex.variant", 0.0)
        expected = _expected_frame(1, 0.0)
        self.assertEqual(frame, expected)

    def test_encode_frame_roll_max(self):
        frame = encode_simplex_frame("simplex.roll_max", 2.5)
        expected = _expected_frame(2, 2.5)
        self.assertEqual(frame, expected)

    def test_encode_frame_reset(self):
        frame = encode_simplex_frame("simplex.reset_counters", 0.5)
        expected = _expected_frame(7, 0.5)
        self.assertEqual(frame, expected)


# ── Tier mapping verification ───────────────────────────────────────────────

class TestTierMapping(unittest.TestCase):
    """Verify 0x19 is in the critical param sets in service/agent."""

    def test_0x19_in_critical_param_write(self):
        from ground_station.service.agent import CRITICAL_PARAM_WRITE
        self.assertIn(0x19, CRITICAL_PARAM_WRITE)

    def test_0x19_in_param_write_tier(self):
        from ground_station.service.agent import PARAM_WRITE_TIER
        self.assertIn(0x19, PARAM_WRITE_TIER)
        self.assertEqual(PARAM_WRITE_TIER[0x19], 0)

    def test_command_tier_0x19(self):
        from ground_station.service.agent import command_tier
        self.assertEqual(command_tier(0x19), 0)


if __name__ == "__main__":
    unittest.main()
