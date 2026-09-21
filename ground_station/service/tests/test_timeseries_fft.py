"""Offline runner for the Time Series / FFT panel harness (Bug 4 regression).

Runs ``timeseries_fft_harness.js`` under Node: it loads
docs/dashboard-platform/shell/plugins/time-series-panel.js and
fft-panel.js in a fake DOM and verifies both panels bind the selected
variables to the live /state stream the Telemetry Explorer reads — under
the raw ``slot0.<dwarf>`` spelling and the adapter's spec aliases — and
that an absent variable honestly reports "waiting for data" rather than
plotting a fabricated zero line.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).with_name("timeseries_fft_harness.js")
_FALLBACK_NODE = "/mnt/c/Program Files/nodejs/node.exe"


class TestTelemPlotHarness(unittest.TestCase):
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

    def test_panels_plot_selected_vars_from_the_live_stream(self):
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
            "telem plot harness failed:\n" + proc.stdout + proc.stderr,
        )
        data = json.loads(proc.stdout)
        self.assertEqual(data["result"], "PASS", data.get("failure"))
        for key in ("time_series_aliased_plots", "time_series_raw_prefix_plots",
                    "honest_absent_not_zero", "fft_aliased_plots",
                    "fft_raw_prefix_plots"):
            self.assertTrue(data["checks"][key], key)


if __name__ == "__main__":
    unittest.main()