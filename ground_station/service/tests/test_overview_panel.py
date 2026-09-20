"""Offline coverage for the PLC-style HMI overview panel (task 20260921-044235).

The panel is browser JS, so the real harness is ``overview_panel_harness.js``
(fake DOM + stubbed shell API, run under Node — same pattern as
``time_series_panel_harness.js`` / ``motor_bench_panel_harness.js``). This
wrapper runs it and checks the verdict. Verified bindings, honest no-data
rendering, staleness (age readout → grey), the alarm banner/list with
recent-cleared retention, and absence of synthetic data.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("overview_panel_harness.js")

# On this WSL host ``node`` on PATH is a broken npm shim; the working binary
# lives in the Windows install. Try PATH first, then the known location.
_FALLBACK_NODE = "/mnt/c/Program Files/nodejs/node.exe"


class TestOverviewPanel(unittest.TestCase):
    def _find_node(self):
        node = shutil.which("node") or shutil.which("node.exe")
        if node:
            probe = subprocess.run(
                [node, "--version"], capture_output=True, text=True, timeout=30
            )
            if probe.returncode == 0:
                return node
        if Path(_FALLBACK_NODE).exists():
            return _FALLBACK_NODE
        return None

    def test_offline_harness_all_checks_pass(self):
        node = self._find_node()
        if not node:
            self.skipTest("no working node on PATH or fallback location")
        proc = subprocess.run(
            [node, str(HARNESS)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(Path(__file__).resolve().parents[3]),
        )
        self.assertEqual(
            proc.returncode, 0,
            "overview panel harness failed:\n" + proc.stdout + proc.stderr,
        )
        self.assertIn("ALL CHECKS PASSED", proc.stdout)
        # Core evidence must be present in the run.
        self.assertIn("Live Render — fed values with units", proc.stdout)
        self.assertIn('missing stage is grey "NO DATA"', proc.stdout)
        self.assertIn("Staleness — age readout, then grey", proc.stdout)
        self.assertIn("cleared alarm remains listed", proc.stdout)
        self.assertIn("No Synthetic Data", proc.stdout)


if __name__ == "__main__":
    unittest.main()
