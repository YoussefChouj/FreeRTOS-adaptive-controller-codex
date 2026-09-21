"""Offline coverage for path-panel.js (Path Planning), task 20260921-141231.

The real harness is ``path_panel_harness.js`` (fake DOM, recording canvas
context and stubbed fetch/XHR). This wrapper runs it under Node and checks
the verdict; no service on 8081 is contacted and no network call is made.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("path_panel_harness.js")

# On this WSL host ``node`` on PATH is a broken npm shim; the working binary
# lives in the Windows install. Try PATH first, then the known location.
_FALLBACK_NODE = "/mnt/c/Program Files/nodejs/node.exe"


class TestPathPanel(unittest.TestCase):
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
            "path panel harness failed:\n" + proc.stdout + proc.stderr,
        )
        self.assertIn("ALL CHECKS PASSED", proc.stdout)
        # Every required behavior must be evidenced in the output.
        self.assertIn('no keys -> "No position data"', proc.stdout)
        self.assertIn("position rendered from c.earth_x/c.earth_y", proc.stdout)
        self.assertIn("waypoint add / delete", proc.stdout)
        self.assertIn("Set Home / Set Target markers", proc.stdout)
        self.assertIn("zoom in / out / reset", proc.stdout)
        self.assertIn("mission payload captured by fetch stub", proc.stdout)
        self.assertIn("a real network call is structurally impossible",
                      proc.stdout)


if __name__ == "__main__":
    unittest.main()
