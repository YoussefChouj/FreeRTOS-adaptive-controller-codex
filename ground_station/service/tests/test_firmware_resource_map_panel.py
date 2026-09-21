"""Offline coverage for resource-map-panel.js (Firmware Resource Map).

Task 20260921-153142. The real harness is ``resource_map_panel_harness.js``
(fake DOM, recording fetch/XHR stubs, stubbed session_stats fixture). This
wrapper runs it under Node and checks the verdict; no service on 8081 is
contacted and no network call is made.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("resource_map_panel_harness.js")

# On this WSL host ``node`` on PATH is a broken npm shim; the working binary
# lives in the Windows install. Try PATH first, then the known location.
_FALLBACK_NODE = "/mnt/c/Program Files/nodejs/node.exe"


class TestFirmwareResourceMapPanel(unittest.TestCase):
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
            "resource map panel harness failed:\n" + proc.stdout + proc.stderr,
        )
        self.assertIn("ALL CHECKS PASSED", proc.stdout)
        # Every required behavior must be evidenced in the output.
        self.assertIn("empty state", proc.stdout)
        self.assertIn("GET /api/view-model?stats=1", proc.stdout)
        self.assertIn("absent metadata NOT PUBLISHED", proc.stdout)
        # The three missing-feature checks are recorded expected failures.
        self.assertIn("symbol search input", proc.stdout)
        self.assertIn("symbol filter control", proc.stdout)
        self.assertIn("symbol paging controls", proc.stdout)
        self.assertEqual(proc.stdout.count("EXPECTED FAILURE"), 3)
        self.assertIn("zero /api/symbols fetches", proc.stdout)


if __name__ == "__main__":
    unittest.main()
