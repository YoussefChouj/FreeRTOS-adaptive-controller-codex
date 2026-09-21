"""Offline coverage for the Control-tab command-panel widget/toggle UX
(task 20260922-061106, AUDIT_2026-09-21 §3.1-3.2).

The panel is browser JS, so the real harness is ``command_panel_harness.js``
(fake DOM + stubbed shell API, run under Node). It asserts:
  - value-parameter commands render as widgets (numeric input, plus a slider
    when the catalog gives a finite range) rather than list entries
  - flag commands render as checkboxes/toggles whose state follows telemetry
    (never optimistic, "not published" when absent)
  - NaN / out-of-range input disables Send with a reason
  - every send routes through the existing arm gate (fail-closed)
  - the render/poll loop survives a failed command POST (Bug 1, commit 7dea39f)
This wrapper just runs it and checks the verdict.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("command_panel_harness.js")


class TestCommandPanelWidgets(unittest.TestCase):
    def test_offline_harness_all_checks_pass(self):
        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node not available on PATH")
        proc = subprocess.run(
            [node, str(HARNESS)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(Path(__file__).resolve().parents[3]),
        )
        self.assertEqual(
            proc.returncode, 0,
            "command-panel widget harness failed:\n" + proc.stdout + proc.stderr,
        )
        self.assertIn("ALL CHECKS PASSED", proc.stdout)
        # Core evidence used by the supervisor must be present in the run.
        self.assertIn('7 value-parameter commands render as widget cards', proc.stdout)
        self.assertIn('Runtime Flags 0x0F renders 13 flag toggles', proc.stdout)
        self.assertIn('armed + disarmed-gated PID-Gain widget => ZERO commands', proc.stdout)
        self.assertIn('after a failed POST a later send still reaches the API', proc.stdout)


if __name__ == "__main__":
    unittest.main()