"""Offline coverage for telemetry-explorer-panel.js, task 20260921-145106.

The real harness is ``telemetry_explorer_panel_harness.js`` (fake DOM,
recording fetch stub, manual timers). This wrapper runs it under Node and
checks the verdict; no service on 8081 is contacted and no network call
is made.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("telemetry_explorer_panel_harness.js")

# On this WSL host ``node`` on PATH is a broken npm shim; the working
# binary lives in the Windows install. Try PATH first, then the known
# location.
_FALLBACK_NODE = "/mnt/c/Program Files/nodejs/node.exe"


class TestTelemetryExplorerPanel(unittest.TestCase):
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
            "telemetry explorer harness failed:\n" + proc.stdout + proc.stderr,
        )
        self.assertIn("ALL CHECKS PASSED", proc.stdout)
        self.assertIn('initial "Waiting for telemetry"', proc.stdout)
        self.assertIn("legitimate zero displays 0", proc.stdout)
        self.assertIn("filter narrows rows; no-match state", proc.stdout)
        self.assertIn("key set accumulates across polls", proc.stdout)
        self.assertIn("null value, missing schema, null state", proc.stdout)
        self.assertIn("short display key, full key in title", proc.stdout)


if __name__ == "__main__":
    unittest.main()
