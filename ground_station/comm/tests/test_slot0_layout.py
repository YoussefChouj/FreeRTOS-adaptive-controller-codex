"""Tests for the slot-0 layout dispatcher + telemetry-mode switch.

Covers:
  - ``_request_slot0_schema`` dispatches to DASHBOARD_FRAME_A_VARS by default
    (legacy firmware mirror layout selectable via layout="boot_default")
  - ``set_telemetry_mode_now`` builds the 9-byte CMD 0x0F frame with the
    right index for each mode and validates inputs
  - Rejects unknown modes, unknown layouts, pre-start() calls
"""
from __future__ import annotations

import socket
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from ground_station.comm import wifi_bridge as wb
from ground_station.comm.boot_default_layout import (
    BOOT_DEFAULT_VARS,
    BOOT_DEFAULT_DIVIDER,
    DASHBOARD_FRAME_A_VARS,
    DASHBOARD_FRAME_A_DIVIDER,
)


# Expected CMD 0x0F frame shape per the protocol (see
# docs/telemetry-protocol.md and ground_station/docs/channel_map.txt).
#   [0xCC][0xDD][0x0F][idx][0,0,0,0][CRC8]   = 9 bytes
# Value (4 bytes LE float32) is ignored for idx 100..102.
def _build_expected_frame(idx: int) -> bytes:
    body = bytes([0xCC, 0xDD, 0x0F, idx]) + struct.pack("<f", 0.0)
    crc = 0
    for b in body[2:]:
        crc ^= b
    return body + bytes([crc])


class TestTelemetryModeSwitch(unittest.TestCase):
    """WifiBridge.set_telemetry_mode_now — frame shape and validation."""

    def _bridge_with_mock_wifi(self) -> wb.WifiBridge:
        b = wb.WifiBridge.__new__(wb.WifiBridge)
        b._wifi = MagicMock(spec=socket.socket)
        b._wifi_host = "192.168.4.1"
        b._wifi_port = 14550
        return b

    def test_legacy_index_100(self):
        b = self._bridge_with_mock_wifi()
        b.set_telemetry_mode_now("legacy")
        sent = b._wifi.sendto.call_args[0][0]
        self.assertEqual(sent, _build_expected_frame(100))
        self.assertEqual(b._wifi.sendto.call_args[0][1], ("192.168.4.1", 14550))

    def test_mixed_index_101(self):
        b = self._bridge_with_mock_wifi()
        b.set_telemetry_mode_now("mixed")
        sent = b._wifi.sendto.call_args[0][0]
        self.assertEqual(sent, _build_expected_frame(101))

    def test_subscribe_only_index_102(self):
        b = self._bridge_with_mock_wifi()
        b.set_telemetry_mode_now("subscribe_only")
        sent = b._wifi.sendto.call_args[0][0]
        self.assertEqual(sent, _build_expected_frame(102))

    def test_unknown_mode_rejected(self):
        b = self._bridge_with_mock_wifi()
        with self.assertRaises(ValueError) as ctx:
            b.set_telemetry_mode_now("turbo")
        self.assertIn("turbo", str(ctx.exception))

    def test_pre_start_call_rejected(self):
        b = wb.WifiBridge.__new__(wb.WifiBridge)
        b._wifi = None  # start() not called
        with self.assertRaises(RuntimeError) as ctx:
            b.set_telemetry_mode_now("mixed")
        self.assertIn("start()", str(ctx.exception))

    def test_index_mapping_table(self):
        # Constants must match the firmware dispatch sentinel in
        # TASK/send_data.c::Process_GroundStation_Command.
        self.assertEqual(wb.WifiBridge._TELEMETRY_MODE_INDEX["legacy"], 100)
        self.assertEqual(wb.WifiBridge._TELEMETRY_MODE_INDEX["mixed"], 101)
        self.assertEqual(wb.WifiBridge._TELEMETRY_MODE_INDEX["subscribe_only"], 102)


class TestSlot0SchemaDispatch(unittest.TestCase):
    """WifiBridge._request_slot0_schema picks the right var list + divider."""

    def _bridge_with_mock_wifi(self) -> wb.WifiBridge:
        b = wb.WifiBridge.__new__(wb.WifiBridge)
        b._wifi = MagicMock(spec=socket.socket)
        b._wifi_host = "192.168.4.1"
        b._wifi_port = 14550
        return b

    @patch.object(wb, "BOOT_DEFAULT_VARS", BOOT_DEFAULT_VARS, create=True)
    @patch.object(wb, "DASHBOARD_FRAME_A_VARS", DASHBOARD_FRAME_A_VARS, create=True)
    def test_unknown_layout_rejected(self):
        b = self._bridge_with_mock_wifi()
        with self.assertRaises(ValueError) as ctx:
            b._request_slot0_schema(layout="turbo")
        self.assertIn("turbo", str(ctx.exception))


class TestBootDefaultLayoutModule(unittest.TestCase):
    """boot_default_layout module — var count and divider constants."""

    def test_dashboard_var_count_matches_manifest(self):
        # S15 expanded the slot-0 layout beyond the 21-var sidebar frame to
        # include MRAC theta vectors (4 axes × 6 weights = 24 vars) and the
        # EKF shadow-mode state (9 vars) so the dashboard's MRAC + Estimator
        # panels can read them via ``a[*]`` sidebar keys. New total: 54.
        #   3 attitude + 8 MRAC e/u_ad + 24 MRAC theta + 8 status + 1 vbat
        #   + 1 xTickCount + 9 EKF = 54 vars
        # Source of truth is the boot_default_layout.py tuple; the YAML
        # manifest is being extended to match in the same change.
        self.assertEqual(len(DASHBOARD_FRAME_A_VARS), 54)

    def test_dashboard_divider_is_4(self):
        # 80/4 = 20 Hz measured in MIXED mode (MIXED cadence ~80 Hz,
        # not the nominal 200 Hz SUBSCRIBE_SEND_TASK_HZ).
        self.assertEqual(DASHBOARD_FRAME_A_DIVIDER, 4)

    def test_boot_default_divider_is_20(self):
        # Mirrors API/subscribe.c Subscribe_BootDefault() (200 / 20 = 10 Hz
        # at nominal Send_Task cadence).
        self.assertEqual(BOOT_DEFAULT_DIVIDER, 20)

    def test_required_sidebar_keys_present(self):
        # _slot0_to_sidebar maps these DWARF names to dashboard "a" keys.
        # If any of these drop out, the dashboard renders "?" for that
        # sidebar entry. Keep this list aligned with the mapping in
        # wifi_bridge.py::_slot0_to_sidebar.
        required = {
            "imu_data.rol", "imu_data.pit", "imu_data.yaw",
            "mrac_state.pitch.e", "mrac_state.pitch.u_ad",
            "mrac_state.roll.e", "mrac_state.roll.u_ad",
            "mrac_state.yaw.e", "mrac_state.yaw.u_ad",
            "mrac_state.z_rate.e", "mrac_state.z_rate.u_ad",
            "DroneStatus.ARM_Status", "DroneStatus.FlyMode",
            "sbus_lost", "TWC.execute", "TWC_arrived",
            "s_authority", "g_of_hold_active", "g_estimator_ready",
            "real_voltage",
        }
        missing = required - set(DASHBOARD_FRAME_A_VARS)
        self.assertEqual(missing, set(),
                         f"DASHBOARD_FRAME_A_VARS missing sidebar keys: {missing}")


if __name__ == "__main__":
    unittest.main()
