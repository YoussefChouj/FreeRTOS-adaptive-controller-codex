"""Fly mode of the Path panel (3D panel flight UX spec, item 1).

Runs ``path_fly_harness.js`` (fake DOM, stubbed fetch, no network) under Node
and checks the verdict. Nothing is sent to a service.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("path_fly_harness.js")


class TestPathFly(unittest.TestCase):
    def test_offline_harness_all_checks_pass(self):
        node = shutil.which("node") or shutil.which("node.exe")
        if not node:
            self.skipTest("no node on PATH")
        proc = subprocess.run([node, str(HARNESS)], capture_output=True, text=True,
                              timeout=120, cwd=str(Path(__file__).resolve().parents[3]))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ALL CHECKS PASSED SUCCESSFULLY.", proc.stdout)
        for n in range(1, 10):
            self.assertIn("[CHECK %d:" % n, proc.stdout)


if __name__ == "__main__":
    unittest.main()
