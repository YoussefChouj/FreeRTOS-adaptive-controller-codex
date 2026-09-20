"""Offline coverage for the overview-panel follow-ons (task 20260921-065323):
the attitude indicator (artificial horizon) and the pre-flight checklist.

The panel is browser JS, so the real harness is
``overview_followons_harness.js`` (fake DOM + stubbed shell API, run under
Node — same pattern as ``overview_panel_harness.js``). This wrapper runs it
and checks the verdict. Verified keys, honest dead states for the horizon
(absent and stale must render an explicit NO DATA legend, never a level
horizon), PASS/FAIL/UNKNOWN checklist verdicts with UNKNOWN first-class.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("overview_followons_harness.js")

# On this WSL host ``node`` on PATH is a broken npm shim; the working binary
# lives in the Windows install. Try PATH first, then the known location.
_FALLBACK_NODE = "/mnt/c/Program Files/nodejs/node.exe"


class TestOverviewFollowons(unittest.TestCase):
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
            "overview follow-ons harness failed:\n" + proc.stdout + proc.stderr,
        )
        self.assertIn("ALL CHECKS PASSED", proc.stdout)
        # Core evidence must be present in the run.
        self.assertIn("Attitude Indicator — live, tracks roll/pitch", proc.stdout)
        self.assertIn("keys absent → dead, NO DATA legend", proc.stdout)
        self.assertIn("stale: amber age, then grey frozen", proc.stdout)
        self.assertIn("live verdicts from synthetic telemetry", proc.stdout)
        self.assertIn("no data: all UNKNOWN, none PASS", proc.stdout)
        self.assertIn("Alarm History — episodes, ACK, SILENCE, CSV export", proc.stdout)
        self.assertIn("Trend-on-Demand — sparkline / insufficient / gap", proc.stdout)
        self.assertIn("Battery Trend — estimate, no-estimate, not-published", proc.stdout)
        self.assertIn("Read-Only — no widget sends, arms or gates anything", proc.stdout)


if __name__ == "__main__":
    unittest.main()
