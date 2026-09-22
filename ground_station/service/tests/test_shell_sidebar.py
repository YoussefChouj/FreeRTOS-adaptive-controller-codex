"""Offline runner for the shell sidebar hide/show + layout regressions.

Runs ``shell_sidebar_harness.js`` under Node: it loads the shell's inline
script from docs/dashboard-platform/shell/index.html into a fake DOM and
verifies that:
  * collapsing the sidebar and re-opening it round-trips with all contents
    (same child count / testids) — operator walkthrough 2 item 1;
  * the Activity log is gated to the Approvals workspace (item 3);
  * the agent-mode pill + STOP live in the bottom-centre strip and the pill
    resolves the real mode (item 4);
  * an empty overview data-flow block can name its carrier preset while the
    overview panel stays read-only (item 2);
  * the narrow-screen bottom padding keeps the strip off panel content.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("shell_sidebar_harness.js")
_FALLBACK_NODE = "/mnt/c/Program Files/nodejs/node.exe"


class TestShellSidebar(unittest.TestCase):
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

    def test_sidebar_hide_show_round_trips_and_layout_regressions(self):
        node = self._find_node()
        if not node:
            self.skipTest("no working node on PATH or fallback location")
        proc = subprocess.run(
            [node, str(HARNESS)],
            capture_output=True, text=True, timeout=120,
            cwd=str(Path(__file__).resolve().parents[3]),
        )
        self.assertEqual(
            proc.returncode, 0,
            "shell sidebar harness failed:\n" + proc.stdout + proc.stderr,
        )
        self.assertIn("sidebar_round_trip", proc.stdout)


if __name__ == "__main__":
    unittest.main()