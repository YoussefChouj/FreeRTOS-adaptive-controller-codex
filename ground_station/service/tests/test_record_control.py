"""Offline runner for the shell Record-control harness.

Runs ``record_control_harness.js`` under Node: it loads the shell's inline
script in a fake DOM + recording fetch stub and verifies the REC/Stop toggle
POSTs the right endpoints, the note button posts ``/api/session/note`` with
the typed text/kind/source, and the off state renders a disabled note button.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("record_control_harness.js")
_FALLBACK_NODE = "/mnt/c/Program Files/nodejs/node.exe"


class TestRecordControl(unittest.TestCase):
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

    def test_record_control_toggle_and_note(self):
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
            "record control harness failed:\n" + proc.stdout + proc.stderr,
        )
        data = json.loads(proc.stdout)
        self.assertTrue(data["off_state_renders_rec"])
        self.assertTrue(data["start_posted_to_recording_start"])
        self.assertTrue(data["start_requested_by_operator"])
        self.assertTrue(data["stop_posted_when_recording"])
        self.assertTrue(data["note_posted_to_session_note"])
        self.assertTrue(data["note_cleared_input"])
        self.assertTrue(data["notes_were_created"])


if __name__ == "__main__":
    unittest.main()

def test_label_cannot_escape_root(tmp_path):
    from ground_station.service.storage import CsvRecorder
    rec = CsvRecorder(tmp_path / "sessions", enabled=True)
    rec.start(label="../../evil/x", requested_by="agent:test", reason="t")
    try:
        assert rec.session_dir.parent == tmp_path / "sessions"
        assert ".." not in rec.session_dir.name
    finally:
        rec.stop()
