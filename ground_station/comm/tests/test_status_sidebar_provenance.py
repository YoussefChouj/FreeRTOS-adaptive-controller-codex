"""Coverage for task A1-A3: Critical few promoted to sidebar, honest degradation,
duplicated stream removal, and consolidated status area.
"""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from ground_station.comm.wifi_bridge import WifiBridge

REPO_ROOT = Path(__file__).resolve().parents[3]
STATUS_PANEL_JS = REPO_ROOT / "docs" / "dashboard-platform" / "shell" / "plugins" / "status-panel.js"


class TestFieldProvenance(unittest.TestCase):
    """Verify that arm, flymode, and vbat are decoded honestly by the bridge."""

    def test_arm_provenance_slot0(self):
        names = ["DroneStatus.ARM_Status"]
        values = [1.0]
        out = WifiBridge._slot0_to_sidebar(names, values)
        self.assertIn("status.arm", out)
        self.assertEqual(out["status.arm"], 1.0)

        out_disarmed = WifiBridge._slot0_to_sidebar(names, [0.0])
        self.assertEqual(out_disarmed["status.arm"], 0.0)

    def test_flymode_provenance_slot0(self):
        names = ["DroneStatus.FlyMode"]
        values = [5.0]  # SDK mode
        out = WifiBridge._slot0_to_sidebar(names, values)
        self.assertIn("status.flymode", out)
        self.assertEqual(out["status.flymode"], 5.0)

    def test_vbat_provenance_slot0(self):
        names = ["real_voltage"]
        values = [16.234]
        out = WifiBridge._slot0_to_sidebar(names, values)
        self.assertIn("status.vbat", out)
        self.assertAlmostEqual(out["status.vbat"], 16.234, places=3)

    def test_frame_id_decodes_arm_and_flymode(self):
        bridge = WifiBridge(vofa_enabled=False)
        # Synthetic Frame ID: header 6 B + payload
        # counter(4), arm(1), mode(1), rc_auth(1), of_hold(1), est_ready(1), sysid(1)
        import struct
        payload = struct.pack("<IBBBBBB", 100, 1, 4, 1, 0, 1, 2)
        frame = bytes([0xAA, 0xBB, 0x03, 0, len(payload), 0]) + payload + b"\x00"
        decoded = bridge._decode_frame_id(frame)
        self.assertEqual(decoded.get("status.arm"), 1.0)
        self.assertEqual(decoded.get("status.flymode"), 4.0)


class TestHonestDegradation(unittest.TestCase):
    """Absent fields must NOT be faked as 0.0, empty, or default."""

    def test_absent_arm_is_omitted(self):
        out = WifiBridge._slot0_to_sidebar(["imu_data.rol"], [0.1])
        self.assertNotIn("status.arm", out)

    def test_absent_flymode_is_omitted(self):
        out = WifiBridge._slot0_to_sidebar(["imu_data.rol"], [0.1])
        self.assertNotIn("status.flymode", out)

    def test_absent_vbat_is_omitted(self):
        out = WifiBridge._slot0_to_sidebar(["imu_data.rol"], [0.1])
        self.assertNotIn("status.vbat", out)


HARNESS = Path(__file__).with_name("status_sidebar_harness.js")


class TestStatusPanelJs(unittest.TestCase):
    """Verify status-panel.js behaviour using node in Windows environment."""

    def _run_harness(self, check: str) -> None:
        import shutil
        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node not available on PATH")
        proc = subprocess.run(
            [node, str(HARNESS), check],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(REPO_ROOT),
        )
        self.assertEqual(
            proc.returncode, 0,
            f"status-panel harness ({check}) failed:\n" + proc.stdout + proc.stderr,
        )

    def test_a2_stream_widget_removed_from_panel(self):
        self._run_harness("check_a2")

    def test_synthetic_absence_renders_not_published(self):
        self._run_harness("check_absence")

    def test_synthetic_live_values_render_correctly(self):
        self._run_harness("check_live")


if __name__ == "__main__":
    unittest.main()

