"""Offline coverage for the overview-panel experiment widgets
(task 20260921-103141): flight-FSM state view and MRAC adaptation view.

The real harness is ``overview_experiment_harness.js`` (fake DOM + stubbed
shell API, run under Node — same pattern as ``overview_followons_harness.js``).
This wrapper runs it and checks the verdict.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("overview_experiment_harness.js")

# On this WSL host ``node`` on PATH is a broken npm shim; the working binary
# lives in the Windows install. Try PATH first, then the known location.
_FALLBACK_NODE = "/mnt/c/Program Files/nodejs/node.exe"


class TestOverviewExperiment(unittest.TestCase):
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
            "overview experiment harness failed:\n" + proc.stdout + proc.stderr,
        )
        self.assertIn("ALL CHECKS PASSED", proc.stdout)
        # Core evidence for both widgets must be present.
        self.assertIn("known state highlights, dwell and last transition", proc.stdout)
        self.assertIn("keys absent → NOT PUBLISHED, no default highlight", proc.stdout)
        self.assertIn("converging series shows verdict and evidence", proc.stdout)
        self.assertIn("short series renders UNKNOWN", proc.stdout)
        self.assertIn("gap shown as a broken line", proc.stdout)
        # Review fixes (task 20260921-130828).
        self.assertIn("unrecognized prior value falls back to numeric", proc.stdout)
        self.assertIn("stale weight values show age and frozen styling", proc.stdout)
        self.assertIn("four adaptation axes: yaw and z rate", proc.stdout)


if __name__ == "__main__":
    unittest.main()
