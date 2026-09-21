"""Offline coverage for the Diagnostics-tab telemetry-bridge banner
(resource-panel.js, RTOS Resources), task 20260922-060031.

The real harness is ``diagnostics_bridge_harness.js`` (fake DOM, configurable
fetch stub, chained-Promise drain). It mirrors the panel loading in
time_series_panel_harness.js / fft_panel_harness.js and asserts the banner
reacts to /health/slots stream_health across four states (streaming, stalled,
not-running, unavailable) without ever fabricating a stall when the route is
absent. This wrapper runs it under Node and checks the verdict; no service on
8081 is contacted and no network call is made.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("diagnostics_bridge_harness.js")

# On this WSL host ``node`` on PATH is a broken npm shim; the working binary
# lives in the Windows install. Try PATH first, then the known location.
_FALLBACK_NODE = "/mnt/c/Program Files/nodejs/node.exe"


class TestDiagnosticsBridgePanel(unittest.TestCase):
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

    def test_offline_bridge_banner_all_checks_pass(self):
        node = self._find_node()
        if not node:
            self.skipTest("no working node on PATH or fallback location")
        proc = subprocess.run(
            [node, str(HARNESS)],
            capture_output=True, text=True, timeout=120,
            cwd=str(Path(__file__).resolve().parents[3]),
        )
        self.assertEqual(proc.returncode, 0,
                         "bridge harness failed:\n" + proc.stdout + proc.stderr)
        self.assertIn("ALL CHECKS PASSED", proc.stdout)
        for needle in (
            "running",
            "Telemetry stalled",
            "bridge not running",
            "status unavailable",
            "ALL CHECKS PASSED",
        ):
            self.assertIn(needle, proc.stdout)


if __name__ == "__main__":
    unittest.main()