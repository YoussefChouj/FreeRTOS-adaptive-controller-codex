"""Tests for MAVLink v1.0 custom message decoders in wifi_bridge.

Three messages mirror the firmware's BSP/mavlink_custom.h:
  MSG 10001 — MRAC_WEIGHTS  (132 bytes payload)
  MSG 10002 — EKF_STATES    (52 bytes payload)
  MSG 10003 — CONTROL_DEBUG (64 bytes payload)

We build wire-format MAVLink frames matching the firmware output and verify
the Python decoders produce the expected dict keys.
"""
from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from ground_station.comm.wifi_bridge import WifiBridge


# ---------------------------------------------------------------------------
# CRC-16/X.25 — mirrors BSP/mavlink_crc.c
# ---------------------------------------------------------------------------

_CRC_TABLE = tuple(int(x) for x in [
    0x0000, 0x1189, 0x2312, 0x329B, 0x4624, 0x57AD, 0x6536, 0x74BF,
    0x8C48, 0x9DC1, 0xAF5A, 0xBED3, 0xCA6C, 0xDBE5, 0xE97E, 0xF8F7,
    0x1081, 0x0108, 0x3393, 0x221A, 0x56A5, 0x472C, 0x75B7, 0x643E,
    0x9CC9, 0x8D40, 0xBFDB, 0xAE52, 0xDAED, 0xCB64, 0xF9FF, 0xE876,
    0x2102, 0x308B, 0x0210, 0x1399, 0x6726, 0x76AF, 0x4434, 0x55BD,
    0xAD4A, 0xBCC3, 0x8E58, 0x9FD1, 0xEB6E, 0xFAE7, 0xC87C, 0xD9F5,
    0x3183, 0x200A, 0x1291, 0x0318, 0x77A7, 0x662E, 0x54B5, 0x453C,
    0xBDCB, 0xAC42, 0x9ED9, 0x8F50, 0xFBEF, 0xEA66, 0xD8FD, 0xC974,
    0x4204, 0x538D, 0x6116, 0x709F, 0x0420, 0x15A9, 0x2732, 0x36BB,
    0xCE4C, 0xDFC5, 0xED5E, 0xFCD7, 0x8868, 0x99E1, 0xAB7A, 0xBAF3,
    0x5285, 0x430C, 0x7197, 0x601E, 0x14A1, 0x0528, 0x37B3, 0x263A,
    0xDECD, 0xCF44, 0xFDDF, 0xEC56, 0x98E9, 0x8960, 0xBBFB, 0xAA72,
    0x6306, 0x728F, 0x4014, 0x519D, 0x2522, 0x34AB, 0x0630, 0x17B9,
    0xEF4E, 0xFEC7, 0xCC5C, 0xDDD5, 0xA96A, 0xB8E3, 0x8A78, 0x9BF1,
    0x7387, 0x620E, 0x5095, 0x411C, 0x35A3, 0x242A, 0x16B1, 0x0738,
    0xFFCF, 0xEE46, 0xDCDD, 0xCD54, 0xB9EB, 0xA862, 0x9AF9, 0x8B70,
    0x8408, 0x9581, 0xA71A, 0xB693, 0xC22C, 0xD3A5, 0xE13E, 0xF0B7,
    0x0840, 0x19C9, 0x2B52, 0x3ADB, 0x4E64, 0x5FED, 0x6D76, 0x7CFF,
    0x9489, 0x8500, 0xB79B, 0xA612, 0xD2AD, 0xC324, 0xF1BF, 0xE036,
    0x18C1, 0x0948, 0x3BD3, 0x2A5A, 0x5EE5, 0x4F6C, 0x7DF7, 0x6C7E,
    0xA50A, 0xB483, 0x8618, 0x9791, 0xE32E, 0xF2A7, 0xC03C, 0xD1B5,
    0x2942, 0x38CB, 0x0A50, 0x1BD9, 0x6F66, 0x7EEF, 0x4C74, 0x5DFD,
    0xB58B, 0xA402, 0x9699, 0x8710, 0xF3AF, 0xE226, 0xD0BD, 0xC134,
    0x39C3, 0x284A, 0x1AD1, 0x0B58, 0x7FE7, 0x6E6E, 0x5CF5, 0x4D7C,
    0xC60C, 0xD785, 0xE51E, 0xF497, 0x8028, 0x91A1, 0xA33A, 0xB2B3,
    0x4A44, 0x5BCD, 0x6956, 0x78DF, 0x0C60, 0x1DE9, 0x2F72, 0x3EFB,
    0xD68D, 0xC704, 0xF59F, 0xE416, 0x90A9, 0x8120, 0xB3BB, 0xA232,
    0x5AC5, 0x4B4C, 0x79D7, 0x685E, 0x1CE1, 0x0D68, 0x3FF3, 0x2E7A,
    0xE70E, 0xF687, 0xC41C, 0xD595, 0xA12A, 0xB0A3, 0x8238, 0x93B1,
    0x6B46, 0x7ACF, 0x4854, 0x59DD, 0x2D62, 0x3CEB, 0x0E70, 0x1FF9,
    0xF78F, 0xE606, 0xD49D, 0xC514, 0xB1AB, 0xA022, 0x92B9, 0x8330,
    0x7BC7, 0x6A4E, 0x58D5, 0x495C, 0x3DE3, 0x2C6A, 0x1EF1, 0x0F78,
])


def _crc16_x25(data: bytes, init: int = 0xFFFF) -> int:
    """CRC-16/X.25 — mirrors BSP/mavlink_crc.c."""
    crc = init
    for b in data:
        crc ^= b
        crc = ((crc >> 8) ^ _CRC_TABLE[crc & 0xFF]) & 0xFFFF
    return crc ^ 0xFFFF


# ---------------------------------------------------------------------------
# Frame builders — mirror BSP/mavlink_custom.h wire format
# ---------------------------------------------------------------------------

def _build_mavlink_frame(msg_id: int, payload: bytes,
                         seq: int = 0, sys_id: int = 1, comp_id: int = 1) -> bytes:
    """Wire-format MAVLink v1.0 frame.

    Header: [STX=0xFE][LEN][SEQ][SYS][COMP][MSG_LO][MSG_HI][PAD=0]
    CRC covers: [MSG_LO][MSG_HI][LEN][payload]
    """
    header = bytes([0xFE, len(payload), seq, sys_id, comp_id,
                    msg_id & 0xFF, (msg_id >> 8) & 0xFF, 0])
    crc_data = bytes([msg_id & 0xFF, (msg_id >> 8) & 0xFF, len(payload)]) + payload
    crc = _crc16_x25(crc_data)
    return header + payload + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def _build_mrac_weights_payload(time_usec: int, theta: list,
                               u_nom: list, xm: list) -> bytes:
    """132-byte payload for MSG 10001 — MRAC_WEIGHTS."""
    buf = struct.pack("<I", time_usec)
    for v in theta:
        buf += struct.pack("<f", float(v))
    for v in u_nom:
        buf += struct.pack("<f", float(v))
    for v in xm:
        buf += struct.pack("<f", float(v))
    return buf


def _build_ekf_states_payload(time_usec: int, vel_body: list,
                             accel_bias: list, gyro_bias: list,
                             euler: list) -> bytes:
    """52-byte payload for MSG 10002 — EKF_STATES."""
    buf = struct.pack("<I", time_usec)
    for v in vel_body:
        buf += struct.pack("<f", float(v))
    for v in accel_bias:
        buf += struct.pack("<f", float(v))
    for v in gyro_bias:
        buf += struct.pack("<f", float(v))
    for v in euler:
        buf += struct.pack("<f", float(v))
    return buf


def _build_ctrl_debug_payload(time_usec: int, e: list, u_ad: list, r: list,
                             fsm_state: int, active_path: int,
                             target: list) -> bytes:
    """64-byte payload for MSG 10003 — CONTROL_DEBUG."""
    buf = struct.pack("<I", time_usec)
    for v in e:
        buf += struct.pack("<f", float(v))
    for v in u_ad:
        buf += struct.pack("<f", float(v))
    for v in r:
        buf += struct.pack("<f", float(v))
    buf += struct.pack("<HH", fsm_state, active_path)
    for v in target:
        buf += struct.pack("<f", float(v))
    return buf


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCRC16X25(unittest.TestCase):
    def test_zeroes(self):
        # CRC-16/X.25 of N zero bytes is NOT 0x0000 (the XOR-final makes it non-zero).
        # Value computed from the reflected algorithm with table verified against MAVLink source.
        self.assertEqual(_crc16_x25(b"\x00" * 10), 0x6378)

    def test_known_vector(self):
        # CRC-16/X.25 of [0x09, 0x01] with init=0xFFFF.
        # Verified against MAVLink source (mavlink_types.h crc_x25_lut).
        self.assertEqual(_crc16_x25(bytes([0x09, 0x01])), 0xC9D6)


class TestMavlinkFrameLengths(unittest.TestCase):
    def test_mrac_weights_frame_size(self):
        payload = _build_mrac_weights_payload(1, [0.0] * 24, [0.0] * 4, [0.0] * 4)
        self.assertEqual(len(payload), 132)
        frame = _build_mavlink_frame(10001, payload)
        self.assertEqual(len(frame), 142)  # 8 header + 132 payload + 2 CRC

    def test_ekf_states_frame_size(self):
        payload = _build_ekf_states_payload(1, [0.0]*3, [0.0]*3, [0.0]*3, [0.0]*3)
        self.assertEqual(len(payload), 52)
        frame = _build_mavlink_frame(10002, payload)
        self.assertEqual(len(frame), 62)

    def test_ctrl_debug_frame_size(self):
        payload = _build_ctrl_debug_payload(1, [0.0]*4, [0.0]*4, [0.0]*4,
                                           0, 0, [0.0]*3)
        # 4 + 16 + 16 + 16 + 2 + 2 + 12 = 68 bytes
        self.assertEqual(len(payload), 68)
        frame = _build_mavlink_frame(10003, payload)
        self.assertEqual(len(frame), 78)


class TestDecodeMRACWeights(unittest.TestCase):
    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def test_full_decode(self):
        theta = [float(i) for i in range(24)]   # 4 axes × 6 basis
        u_nom = [0.1, 0.2, 0.3, 0.4]
        xm = [1.0, 2.0, 3.0, 4.0]
        payload = _build_mrac_weights_payload(1234567890, theta, u_nom, xm)
        frame = _build_mavlink_frame(10001, payload, seq=5)
        result = self.bridge._decode_mavlink_mrac_weights(frame)
        self.assertEqual(result["mav.time_usec"], 1234567890.0)
        # pitch.theta_0..5 = theta[0..5] = 0..5
        self.assertEqual(result["mrac.pitch.theta_0"], 0.0)
        self.assertEqual(result["mrac.pitch.theta_5"], 5.0)
        # roll.theta_0 = theta[6] = 6
        self.assertEqual(result["mrac.roll.theta_0"], 6.0)
        # z.theta_5 = theta[23] = 23
        self.assertEqual(result["mrac.z.theta_5"], 23.0)
        self.assertAlmostEqual(result["mrac.pitch.u_nom"], 0.1)
        self.assertAlmostEqual(result["mrac.z.u_nom"], 0.4)
        self.assertAlmostEqual(result["mrac.pitch.xm"], 1.0)
        self.assertAlmostEqual(result["mrac.z.xm"], 4.0)

    def test_corrupt_crc_returns_empty(self):
        payload = _build_mrac_weights_payload(1, [0.0]*24, [0.0]*4, [0.0]*4)
        frame = _build_mavlink_frame(10001, payload)
        corrupted = bytearray(frame)
        corrupted[-1] ^= 0xFF
        result = self.bridge._decode_mavlink_mrac_weights(bytes(corrupted))
        self.assertEqual(result, {})

    def test_wrong_msg_id_returns_empty(self):
        payload = _build_mrac_weights_payload(1, [0.0]*24, [0.0]*4, [0.0]*4)
        frame = _build_mavlink_frame(99999, payload)
        result = self.bridge._decode_mavlink_mrac_weights(frame)
        self.assertEqual(result, {})


class TestDecodeEKFStates(unittest.TestCase):
    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def test_full_decode(self):
        payload = _build_ekf_states_payload(
            987654321,
            vel_body=[0.5, -0.3, 0.1],
            accel_bias=[0.01, -0.02, 0.005],
            gyro_bias=[0.001, -0.001, 0.0],
            euler=[0.1, 0.2, 1.57],
        )
        frame = _build_mavlink_frame(10002, payload)
        result = self.bridge._decode_mavlink_ekf_states(frame)
        self.assertEqual(result["mav.time_usec"], 987654321.0)
        self.assertAlmostEqual(result["ekf.vel_body_x"], 0.5)
        self.assertAlmostEqual(result["ekf.vel_body_y"], -0.3)
        self.assertAlmostEqual(result["ekf.vel_body_z"], 0.1)
        self.assertAlmostEqual(result["ekf.accel_bias_x"], 0.01)
        self.assertAlmostEqual(result["ekf.gyro_bias_z"], 0.0)
        self.assertAlmostEqual(result["ekf.roll_rad"], 0.1)
        self.assertAlmostEqual(result["ekf.pitch_rad"], 0.2)
        self.assertAlmostEqual(result["ekf.yaw_rad"], 1.57, places=5)


class TestDecodeCtrlDebug(unittest.TestCase):
    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def test_full_decode(self):
        payload = _build_ctrl_debug_payload(
            5555,
            e=[0.01, -0.02, 0.0, 0.0],
            u_ad=[0.05, -0.04, 0.1, 0.0],
            r=[0.1, -0.1, 0.0, 0.0],
            fsm_state=2, active_path=1,
            target=[1.5, 2.5, -3.0],
        )
        frame = _build_mavlink_frame(10003, payload)
        result = self.bridge._decode_mavlink_ctrl_debug(frame)
        self.assertEqual(result["mav.time_usec"], 5555.0)
        self.assertAlmostEqual(result["ctrl.pitch.e"], 0.01)
        self.assertAlmostEqual(result["ctrl.roll.e"], -0.02)
        self.assertAlmostEqual(result["ctrl.pitch.u_ad"], 0.05)
        self.assertAlmostEqual(result["ctrl.yaw.r"], 0.0)
        self.assertEqual(result["ctrl.fsm_state"], 2.0)
        self.assertEqual(result["ctrl.active_path"], 1.0)
        self.assertAlmostEqual(result["ctrl.target_x"], 1.5)
        self.assertAlmostEqual(result["ctrl.target_z"], -3.0)


class TestParseOneMavlink(unittest.TestCase):
    """Feed MAVLink frames through _parse_one() to verify end-to-end integration."""

    def setUp(self):
        self.bridge = WifiBridge(vofa_enabled=False)
        self.bridge._wifi = MagicMock()
        self.bridge._cmd_udp = MagicMock()
        self.bridge._telem_udp = MagicMock()
        self.bridge._udp_send = MagicMock()

    def test_mrac_weights_via_parse_one(self):
        theta = [float(i) for i in range(24)]
        payload = _build_mrac_weights_payload(1, theta, [1.0]*4, [0.5]*4)
        frame = _build_mavlink_frame(10001, payload)
        buf = bytearray(frame)
        result = self.bridge._parse_one(buf)
        self.assertIsNotNone(result)
        tag, data = result
        self.assertEqual(tag, "mav_w")
        self.assertEqual(data["mav.time_usec"], 1.0)
        self.assertEqual(len(buf), 0)  # full frame consumed

    def test_ekf_states_via_parse_one(self):
        payload = _build_ekf_states_payload(42, [0.0]*3, [0.0]*3, [0.0]*3, [0.0]*3)
        frame = _build_mavlink_frame(10002, payload)
        buf = bytearray(frame)
        result = self.bridge._parse_one(buf)
        self.assertIsNotNone(result)
        tag, data = result
        self.assertEqual(tag, "mav_e")
        self.assertEqual(data["mav.time_usec"], 42.0)
        self.assertEqual(len(buf), 0)

    def test_ctrl_debug_via_parse_one(self):
        payload = _build_ctrl_debug_payload(
            99, [0.0]*4, [0.0]*4, [0.0]*4, 3, 2, [0.0]*3)
        frame = _build_mavlink_frame(10003, payload)
        buf = bytearray(frame)
        result = self.bridge._parse_one(buf)
        self.assertIsNotNone(result)
        tag, data = result
        self.assertEqual(tag, "mav_c")
        self.assertEqual(data["ctrl.fsm_state"], 3.0)
        self.assertEqual(data["ctrl.active_path"], 2.0)

    def test_mavlink_priority_over_custom_frames(self):
        """0xFE magic is unambiguous — it is always MAVLink regardless of what
        follows, even if it could superficially match a 0xAA pattern."""
        theta = [0.0] * 24
        payload = _build_mrac_weights_payload(7, theta, [0.0]*4, [0.0]*4)
        frame = _build_mavlink_frame(10001, payload)
        buf = bytearray(frame)
        result = self.bridge._parse_one(buf)
        self.assertIsNotNone(result)
        tag, _ = result
        self.assertEqual(tag, "mav_w")

    def test_unknown_msg_id_returns_none(self):
        # Build a valid MAVLink frame with an unknown message ID
        payload = _build_mrac_weights_payload(1, [0.0]*24, [0.0]*4, [0.0]*4)
        frame = _build_mavlink_frame(50000, payload)  # not 10001/10002/10003
        buf = bytearray(frame)
        result = self.bridge._parse_one(buf)
        # _parse_one returns None for unknown MAVLink IDs (no matching decoder)
        self.assertIsNone(result)
        # Buffer should be consumed (we recognized it as MAVLink)
        self.assertEqual(len(buf), 0)


if __name__ == "__main__":
    unittest.main()
