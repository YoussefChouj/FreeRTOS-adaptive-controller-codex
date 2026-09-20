"""Offline coverage for the rebuilt time-series panel (task 20260921-032031).

Verifies tasks B1-B3:
- B1: Position traces (X, Y, Z from Frame C: c.earth_x, c.earth_y, c.altitude).
      Honest no-data rendering when variables are missing.
- B2: Dynamic variable picker dropdown, trace toggle, persistent selection across reconnect.
- B3: Pause safety indicator (hazard banner + pulsing border + age/offset readout),
      frozen view vs ongoing buffer ingestion, resume-to-live.
- B3: Bounded ring buffer (3000 samples = 30.0s @ 100 Hz, 37.5s @ 80 Hz).
- B3: Region zoom (view operation, zero sample loss, zoom-out restores buffer).
- No synthetic waveforms or Lissajous demo data.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("time_series_panel_harness.js")


class TestTimeSeriesPanel(unittest.TestCase):
    def test_offline_harness_all_checks_pass(self):
        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node not available on PATH")
        proc = subprocess.run(
            [node, str(HARNESS)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(Path(__file__).resolve().parents[3]),
        )
        self.assertEqual(
            proc.returncode, 0,
            "time-series panel harness failed:\n" + proc.stdout + proc.stderr,
        )
        self.assertIn("ALL CHECKS PASSED", proc.stdout)
        self.assertIn("B1 Position Traces (X, Y, Z)", proc.stdout)
        self.assertIn("B2 Configurable Variable Picker", proc.stdout)
        self.assertIn("B3 Recording Controls & Safety Rule", proc.stdout)
        self.assertIn("B3 Bounded Ring Buffer", proc.stdout)
        self.assertIn("B3 Region Zoom", proc.stdout)
        self.assertIn("No Synthetic Waveform", proc.stdout)


if __name__ == "__main__":
    unittest.main()
