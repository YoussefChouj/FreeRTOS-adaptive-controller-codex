"""Coverage for the three revived dashboard panels (task 20260920-223109).

F1 — subscribe ranges built from DWARF symbols must carry the resolver's
     ``fmt`` so a u32 (xTickCount) unpacks as an integer, not as a
     denormal float32 that rounds to 0.0.
I2 — Frame C's wire ``rpm[4]`` is expanded to the motor-bench panel's
     scalar contract ``motor.rpm_0..3`` in the bridge.
E1 — ``s_ekf.x[0..8]`` is mapped to the estimator panel's ``ekf.*`` keys
     by the live ``_slot0_to_sidebar`` mapper.
"""
from __future__ import annotations

import struct
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from ground_station.comm import wifi_bridge as wb
from ground_station.comm.wifi_bridge import WifiBridge
from ground_station.livewatch.stream import StreamRange


class _FakeSchema:
    def __init__(self, ranges):
        self.ranges = ranges


def _build_data_frame(slot, seq, t_ms, value_chunks):
    """0xAA 0xBB 0x09+slot data frame with raw value chunks (any width)."""
    from ground_station.livewatch.transport import crc16_ccitt
    values_bytes = b"".join(value_chunks)
    payload = struct.pack("<I", t_ms) + values_bytes
    frame_type = 0x09 + slot
    crc_input = bytes([frame_type,
                       (len(payload) >> 8) & 0xFF,
                       len(payload) & 0xFF,
                       seq]) + payload
    crc = crc16_ccitt(crc_input)
    return (bytes([0xAA, 0xBB, frame_type,
                   (len(payload) >> 8) & 0xFF,
                   len(payload) & 0xFF,
                   seq]) + payload + struct.pack(">H", crc))


def _frame_c(rpm_vals):
    """Synthetic Frame C: 10 f32 then 4×u16 rpm then u16 seq (50 B payload)."""
    payload = struct.pack(
        "<3f3f2ff4HH",
        1.0, 2.0, 3.0,          # roll pitch yaw (deg)
        0.01, 0.02, 0.03,       # gyro x/y/z (rad/s)
        0.5, 0.6,               # earth x/y (m)
        1.25,                   # altitude (m)
        rpm_vals[0], rpm_vals[1], rpm_vals[2], rpm_vals[3],
        42,                     # seq
    )
    header = bytes([0xAA, 0xBB, 0x06,
                    (len(payload) >> 8) & 0xFF, len(payload) & 0xFF,
                    0x00])  # byte 5 = MAX_NUM_BASIS parity
    return header + payload + b"\x00\x00"  # 2 tail bytes per 0x06 framing


class TestF1FmtPlumbing(unittest.TestCase):
    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def test_u32_range_decodes_to_integer(self):
        rng = StreamRange(address=0x20001000, size=4, count=1,
                          name="xTickCount", fmt="I")
        self.bridge.apply_manifest_schema(1, _FakeSchema((rng,)))
        tick = 1234567
        frame = _build_data_frame(1, 1, 999,
                                  [struct.pack("<I", tick)])
        decoded = self.bridge._decode_stream_frame(1, frame)
        self.assertIsInstance(decoded["values"][0], int)
        self.assertEqual(decoded["values"][0], tick)
        self.assertEqual(decoded["json"]["slot1.xTickCount"], float(tick))

    def test_missing_fmt_u32_was_denormal(self):
        # Documents the pre-fix bug: fmt unset -> _SIZE_FMT[4]="f".
        rng = StreamRange(address=0x20001000, size=4, count=1,
                          name="xTickCount", fmt=None)
        self.bridge.apply_manifest_schema(1, _FakeSchema((rng,)))
        tick = 1234567
        frame = _build_data_frame(1, 1, 999,
                                  [struct.pack("<I", tick)])
        decoded = self.bridge._decode_stream_frame(1, frame)
        self.assertNotEqual(decoded["values"][0], tick)
        self.assertEqual(decoded["json"]["slot1.xTickCount"], 0.0)

    def test_request_slot0_schema_carries_resolver_fmt(self):
        import ground_station.livewatch.symbols as symbols

        class _FakeSymbol:
            def __init__(self, name):
                self.name = name
                self.address = 0x20001000
                self.size = 4
                self.fmt = "I"

        class _FakeResolver:
            def __init__(self, path):
                pass

            def resolve(self, name):
                return _FakeSymbol(name)

        bridge = WifiBridge.__new__(WifiBridge)
        bridge._wifi_send = MagicMock()
        bridge._wifi_host = "192.168.4.1"
        bridge._wifi_port = 14550
        bridge._stream_lock = threading.Lock()
        bridge._pending_schema_ranges = {}
        with patch.object(wb, "DASHBOARD_FRAME_A_VARS", ("xTickCount",)), \
                patch.object(symbols, "SymbolResolver", _FakeResolver):
            bridge._request_slot0_schema(layout="dashboard")
        pending = bridge._pending_schema_ranges[0]
        self.assertEqual(pending[0].name, "xTickCount")
        self.assertEqual(pending[0].fmt, "I")


class TestI2RpmKeyMapping(unittest.TestCase):
    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def test_rpm_scalar_keys_helper(self):
        out = wb._rpm_scalar_keys([111, 222, 333, 444])
        self.assertEqual(out, {
            "motor.rpm_0": 111, "motor.rpm_1": 222,
            "motor.rpm_2": 333, "motor.rpm_3": 444,
        })

    def test_decode_frame_c_exposes_scalar_contract(self):
        frame = _frame_c([1111, 2222, 3333, 4444])
        decoded = self.bridge._decode_frame_c(frame)
        self.assertEqual(decoded["c.rpm"], [1111, 2222, 3333, 4444])
        for i, expected in enumerate([1111, 2222, 3333, 4444]):
            key = f"motor.rpm_{i}"
            self.assertIn(key, decoded)
            self.assertEqual(decoded[key], expected)
            self.assertIsInstance(decoded[key], int)

    def test_parse_one_routes_frame_c(self):
        frame = _frame_c([5100, 5200, 5300, 5400])
        buf = bytearray(frame)
        tag, payload = self.bridge._parse_one(buf)
        self.assertEqual(tag, "c")
        self.assertEqual(payload["motor.rpm_0"], 5100)
        self.assertEqual(payload["motor.rpm_3"], 5400)


class TestE1EkfSidebarMapping(unittest.TestCase):
    def test_s_ekf_states_reach_panel_key_names(self):
        names = [f"s_ekf.x[{i}]" for i in range(9)]
        values = [0.1 * (i + 1) for i in range(9)]
        out = WifiBridge._slot0_to_sidebar(names, values)
        expected_keys = [
            "ekf.vel_x", "ekf.vel_y", "ekf.vel_z",
            "ekf.bias_accel_x", "ekf.bias_accel_y", "ekf.bias_accel_z",
            "ekf.bias_gyro_x", "ekf.bias_gyro_y", "ekf.bias_gyro_z",
        ]
        for ek, nv in zip(expected_keys, values):
            self.assertIn(ek, out)
            self.assertAlmostEqual(out[ek], round(nv, 3), places=3)

    def test_absent_fields_are_not_faked(self):
        out = WifiBridge._slot0_to_sidebar(
            [f"s_ekf.x[{i}]" for i in range(9)],
            [0.0] * 9)
        self.assertNotIn("ekf.pos_x", out)
        self.assertNotIn("ekf.pos_y", out)
        self.assertNotIn("ekf.pos_z", out)
        self.assertNotIn("estimator.filter_status", out)
        self.assertNotIn("estimator.cov_pxx", out)


if __name__ == "__main__":
    unittest.main()
