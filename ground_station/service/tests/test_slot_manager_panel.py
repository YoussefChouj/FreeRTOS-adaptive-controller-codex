"""Offline coverage for slot-manager-panel.js, task 20260921-145106.

The real harness is ``slot_manager_panel_harness.js`` (fake DOM,
recording shell-api stubs and fetch router, manual timers and fixed
clock). This wrapper runs it under Node and checks the verdict; no
service on 8081 is contacted and no network call is made.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("slot_manager_panel_harness.js")

# On this WSL host ``node`` on PATH is a broken npm shim; the working
# binary lives in the Windows install. Try PATH first, then the known
# location.
_FALLBACK_NODE = "/mnt/c/Program Files/nodejs/node.exe"


class TestSlotManagerPanel(unittest.TestCase):
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
            "slot manager harness failed:\n" + proc.stdout + proc.stderr,
        )
        self.assertIn("ALL CHECKS PASSED", proc.stdout)
        self.assertIn("picker root load (_loadPickerRoot), drill, crumb back",
                      proc.stdout)
        self.assertIn("preset fills subscribe form", proc.stdout)
        self.assertIn("divider ladder readout, cadence set, invalid input",
                      proc.stdout)
        self.assertIn("subscribe sends captured preview + request bodies",
                      proc.stdout)
        self.assertIn("unresolved preview blocks the subscribe", proc.stdout)
        self.assertIn("per-slot selection removed via Clear", proc.stdout)
        self.assertIn("empty table honest; health silent, symbols error visible",
                      proc.stdout)
        self.assertIn("slot expand renders channels, units, null as ??",
                      proc.stdout)


if __name__ == "__main__":
    unittest.main()
