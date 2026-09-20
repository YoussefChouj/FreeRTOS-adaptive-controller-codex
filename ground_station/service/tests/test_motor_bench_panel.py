"""Offline coverage for the rebuilt motor-bench panel (task 20260921-012643).

The panel is browser JS, so the real harness is
``motor_bench_panel_harness.js`` (fake DOM + stubbed shell API, run under
Node). It captures every ``(cmdId, index, value)`` tuple the panel would put
on the wire and asserts the safety sequencing: no auto-enable, select/CCR
before enable, CCR=2000 before enable=0, 10 Hz heartbeat inside the 500 ms
firmware dead-man, heartbeat stops on every teardown trigger, and every send
gated on ``['disarmed']``. This wrapper just runs it and checks the verdict.
"""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("motor_bench_panel_harness.js")


class TestMotorBenchPanel(unittest.TestCase):
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
            "motor-bench panel harness failed:\n" + proc.stdout + proc.stderr,
        )
        self.assertIn("ALL CHECKS PASSED", proc.stdout)
        # Core protocol evidence must be present in the run.
        self.assertIn('no ungated submitCommand on the bench path', proc.stdout)
        self.assertIn('every emitted send (heartbeats included) goes through', proc.stdout)
        self.assertIn('ZERO commands (no auto-enable)', proc.stdout)


if __name__ == "__main__":
    unittest.main()
