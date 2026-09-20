"""Offline test runner for all 17 dashboard panels audit harness.
Runs ``all_panels_audit.js`` under Node and verifies that all panels
initialize and execute.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("all_panels_audit.js")
_FALLBACK_NODE = "/mnt/c/Program Files/nodejs/node.exe"


class TestAllPanelsAudit(unittest.TestCase):
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

    def test_all_17_panels_offline_audit(self):
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
            "all panels audit harness failed:\n" + proc.stdout + proc.stderr,
        )
        data = json.loads(proc.stdout)
        self.assertEqual(len(data), 17, f"Expected 17 panels audited, got {len(data)}")
        for filename, res in data.items():
            self.assertEqual(
                res.get("status"), "OK",
                f"Panel {filename} failed: {res.get('error')}"
            )


if __name__ == "__main__":
    unittest.main()
