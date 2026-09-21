"""Offline coverage for the estimator-mode panel (task 20260922-061216).

The panel is browser JS, so the real harness is
``estimator_panel_harness.js`` (fake DOM + stubbed shell API, run under
Node). It verifies: mode switch uses gatedCommand(['disarmed']), mode
switch is blocked when armed, tau change and freeze toggle use
submitCommand, and destroy cleans up globals.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("estimator_panel_harness.js")


class TestEstimatorPanel(unittest.TestCase):
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
            "estimator panel harness failed:\n" + proc.stdout + proc.stderr,
        )
        self.assertIn("ALL PASSED", proc.stdout)


if __name__ == "__main__":
    unittest.main()
