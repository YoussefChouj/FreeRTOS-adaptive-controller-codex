"""Pytest wrapper for rtos_panel_harness.js (task 20260921-141231).

Runs the offline resource-panel harness under Node and checks its
assertions; no live service, SWD probe, or network involved.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("rtos_panel_harness.js")

# On this WSL host ``node`` on PATH is a broken npm shim; the working binary
# lives in the Windows install. Try PATH first, then the known location.
_FALLBACK_NODE = "/mnt/c/Program Files/nodejs/node.exe"


class TestRtosPanelHarness(unittest.TestCase):
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

    def test_harness_assertions_pass(self):
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
            "rtos panel harness failed:\n" + proc.stdout + proc.stderr,
        )
        self.assertIn("ALL ASSERTIONS PASSED", proc.stdout)
        self.assertIn("RTOS bridge running", proc.stdout)
        self.assertIn("RTOS bridge not running", proc.stdout)


if __name__ == "__main__":
    unittest.main()
