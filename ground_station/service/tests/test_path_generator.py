"""Offline runner for the Path Panel generator/binding harness (Bug 5).

Runs ``path_generator_harness.js`` under Node: it loads
docs/dashboard-platform/shell/plugins/path-panel.js in a fake DOM and
verifies (1) Frame C position binds from the live /state stream under BOTH
the adapter's spec alias (``c.earth_x``) and the raw slot-prefixed DWARF
spelling (``slot1.ano_of.earth_x``), with altitude normalised cm->m, (2) a
genuinely absent position is reported honestly as "No position data" rather
than a fabricated 0, and (3) the random-path generator produces N bounded,
configurably-spaced waypoints — all without a single network send.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("path_generator_harness.js")

# On this WSL host ``node`` on PATH is a broken npm shim; the working binary
# lives in the Windows install. Try PATH first, then the known location.
_FALLBACK_NODE = "/mnt/c/Program Files/nodejs/node.exe"


class TestPathGenerator(unittest.TestCase):
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
            timeout=180,
            cwd=Path(__file__).parents[1],
        )
        self.assertEqual(
            proc.returncode,
            0,
            "path_generator_harness exited %r\nSTDOUT:\n%s\nSTDERR:\n%s"
            % (proc.returncode, proc.stdout, proc.stderr),
        )
        self.assertIn("ALL CHECKS PASSED SUCCESSFULLY.", proc.stdout)


if __name__ == "__main__":
    unittest.main()