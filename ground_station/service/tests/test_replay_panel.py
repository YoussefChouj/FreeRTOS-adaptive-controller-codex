"""Offline coverage for replay-panel.js, task 20260921-145106.

The real harness is ``replay_panel_harness.js`` (fake DOM, recording
fetch router and canvas ctx, manual timers). This wrapper runs it under
Node and checks the verdict; no service on 8081 is contacted and no
network call is made.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("replay_panel_harness.js")

# On this WSL host ``node`` on PATH is a broken npm shim; the working
# binary lives in the Windows install. Try PATH first, then the known
# location.
_FALLBACK_NODE = "/mnt/c/Program Files/nodejs/node.exe"


class TestReplayPanel(unittest.TestCase):
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
            "replay harness failed:\n" + proc.stdout + proc.stderr,
        )
        self.assertIn("ALL CHECKS PASSED", proc.stdout)
        self.assertIn("empty sessions, honest placeholder states", proc.stdout)
        self.assertIn("session list and selected detail render", proc.stdout)
        self.assertIn("ended/count missing render dash", proc.stdout)
        self.assertIn("export posts captured request body", proc.stdout)
        self.assertIn("play posts captured; error response surfaced", proc.stdout)
        self.assertIn("paged records render with truncation note", proc.stdout)
        self.assertIn("scrub offset navigation, speed switch, slot filter",
                      proc.stdout)


if __name__ == "__main__":
    unittest.main()
