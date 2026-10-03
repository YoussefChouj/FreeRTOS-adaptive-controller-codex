"""Offline coverage for the campaign-panel."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("campaign_panel_harness.js")


class TestCampaignPanel(unittest.TestCase):
    def test_offline_harness_all_checks_pass(self):
        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("node not available on PATH")
        proc = subprocess.run(
            [node, str(HARNESS)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            cwd=str(Path(__file__).resolve().parents[3]),
        )
        self.assertEqual(
            proc.returncode, 0,
            "campaign-panel harness failed:\n" + proc.stdout + proc.stderr,
        )
        self.assertIn("ALL CHECKS PASSED", proc.stdout)

        tags = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm', 'n', 'o', 'p', 'r']
        lines = proc.stdout.splitlines()
        for tag in tags:
            self.assertTrue(any(line.startswith(tag + " ") for line in lines), "Missing check tag: " + tag)


if __name__ == "__main__":
    unittest.main()
