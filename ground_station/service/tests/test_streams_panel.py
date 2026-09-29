"""Offline coverage for streams-panel.js (Phase 3 Streams panel).

The real checks live in ``streams_panel_harness.js`` (fake DOM, routing fetch
stub, manual timers). This wrapper runs it under Node; nothing contacts the
ground service or the drone.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("streams_panel_harness.js")
INDEX = (Path(__file__).resolve().parents[3] / "docs" / "dashboard-platform"
         / "shell" / "index.html")


class TestStreamsPanel(unittest.TestCase):
    def test_offline_harness_all_checks_pass(self):
        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("no node on PATH")
        proc = subprocess.run([node, str(HARNESS)], capture_output=True,
                              text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn("FAIL", proc.stdout)
        self.assertIn("16 passed", proc.stdout)

    def test_plugin_is_registered_in_the_shell(self):
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn("/plugins/streams-panel.js", html)
        self.assertIn("'Streams':", html)


if __name__ == "__main__":
    unittest.main()
