"""Offline runner for the shell poll-loop harness (Bug 1 regression).

Runs ``shell_loop_harness.js`` under Node: it loads the shell's inline
script from docs/dashboard-platform/shell/index.html in a fake DOM and
verifies that a throwing plugin callback and a failed request can never
stop the global poll loop, and that per-plugin errors land in that
panel's card only.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("shell_loop_harness.js")
_FALLBACK_NODE = "/mnt/c/Program Files/nodejs/node.exe"


class TestShellLoop(unittest.TestCase):
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

    def test_shell_poll_loop_survives_plugin_and_request_failures(self):
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
            "shell loop harness failed:\n" + proc.stdout + proc.stderr,
        )
        data = json.loads(proc.stdout)
        self.assertEqual(data["ticks_survived"], 5)
        self.assertEqual(data["good_panel_renders"], 4)
        self.assertTrue(data["bad_panel_error_shown_in_panel"])
        self.assertTrue(data["good_panel_untouched"])
        self.assertTrue(data["recovered_from_failed_request"])
        self.assertEqual(data["final_status"], "connected")


if __name__ == "__main__":
    unittest.main()
