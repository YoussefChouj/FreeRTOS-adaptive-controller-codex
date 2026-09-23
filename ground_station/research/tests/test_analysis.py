"""Tests for ground_station.research.analysis."""
from __future__ import annotations

import math
import statistics
from pathlib import Path

import pytest

from ground_station.research.analysis import (
    rmse_of, overshoot, settling_time, saturation_time, dominant_peaks,
    analyse_run, generate_report, load_csv_columns,
    metric, list_plugins,
    u_ad_spike_ratio, w_norm_convergence, gate_saturation,
)
from ground_station.research.run import Run


class TestRmseOf:
    def test_zero_error(self) -> None:
        assert rmse_of([0.0, 0.0, 0.0]) == 0.0

    def test_known_errors(self) -> None:
        errors = [1.0, 2.0, 3.0]
        expected = math.sqrt((1 + 4 + 9) / 3)
        assert abs(rmse_of(errors) - expected) < 1e-10

    def test_empty(self) -> None:
        assert rmse_of([]) == 0.0


class TestOvershoot:
    def test_step_with_overshoot(self) -> None:
        setpoint = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
        response = [0.0, 0.2, 0.6, 1.0, 1.2, 1.1]
        os_val = overshoot(setpoint, response)
        assert abs(os_val - 0.2) < 1e-10

    def test_no_overshoot(self) -> None:
        setpoint = [0.0, 0.0, 1.0, 1.0]
        response = [0.0, 0.5, 1.0, 1.0]
        assert overshoot(setpoint, response) == 0.0

    def test_empty(self) -> None:
        assert overshoot([], []) == 0.0


class TestSettlingTime:
    def test_step_response(self) -> None:
        setpoint = [0.0, 0.0, 1.0, 1.0, 1.0, 1.0]
        response = [0.0, 0.5, 1.1, 0.99, 1.0, 1.0]
        t = settling_time(response, setpoint, tolerance=0.02)
        assert t == 2.0  # index 2 is last outside band

    def test_empty(self) -> None:
        assert settling_time([], []) == 0.0


class TestSaturationTime:
    def test_all_outside(self) -> None:
        signal = [2.0, 2.5, 3.0]
        assert saturation_time(signal, lo=-1.0, hi=1.0) == 3

    def test_none_outside(self) -> None:
        signal = [0.0, 0.5, 1.0]
        assert saturation_time(signal, lo=-1.0, hi=1.0) == 0

    def test_empty(self) -> None:
        assert saturation_time([]) == 0


class TestDominantPeaks:
    def test_single_peak(self) -> None:
        signal = [0.0, 0.5, 1.0, 0.5, 0.0]
        peaks = dominant_peaks(signal, n_peaks=1)
        assert peaks == [2]

    def test_empty(self) -> None:
        assert dominant_peaks([]) == []


class TestStubs:
    """Thesis plugin stubs return None when columns are missing."""

    def test_u_ad_spike_ratio_missing(self) -> None:
        assert u_ad_spike_ratio({}) is None
        assert u_ad_spike_ratio({"u_ad": []}) is None

    def test_w_norm_convergence_missing(self) -> None:
        assert w_norm_convergence({}) is None
        assert w_norm_convergence({"w_norm": [1.0]}) is None

    def test_gate_saturation_missing(self) -> None:
        assert gate_saturation({}) is None

    def test_u_ad_spike_ratio_zero_median(self) -> None:
        assert u_ad_spike_ratio({"u_ad": [0.0, 0.0]}) is None


class TestPluginRegistry:
    def test_list_plugins(self) -> None:
        plugins = list_plugins()
        assert "u_ad_spike_ratio" in plugins
        assert "w_norm_convergence" in plugins
        assert "gate_saturation" in plugins

    def test_register_custom_metric(self) -> None:
        @metric("test_metric")
        def test_fn(data):
            return 42.0

        assert "test_metric" in list_plugins()
        assert test_fn({}) == 42.0

    def test_custom_metric_missing_columns(self) -> None:
        @metric("test_missing")
        def fn(data):
            vals = data.get("x")
            if vals is None:
                return None
            return sum(vals)

        assert fn({}) is None


class TestAnalyseRun:
    def test_core_metrics(self) -> None:
        run = Run(kind="experiment")
        data = {
            "setpoint": [0.0, 0.0, 1.0, 1.0, 1.0, 1.0],
            "response": [0.0, 0.3, 1.2, 1.0, 1.0, 1.0],
            "error": [0.0, -0.3, -0.2, 0.0, 0.0, 0.0],
        }
        metrics = analyse_run(run, data)
        assert "rmse" in metrics
        assert "overshoot" in metrics
        assert "settling_time" in metrics
        assert "saturation_time" in metrics
        assert "dominant_peaks" in metrics

    def test_core_metrics_no_response(self) -> None:
        run = Run()
        metrics = analyse_run(run, {})
        # Only core metrics should be present (dominant_peaks always runs)
        assert "dominant_peaks" in metrics
        # No axis-specific metrics
        assert all(not k.startswith(("rmse", "overshoot", "settling", "saturation")) for k in metrics)


class TestGateSaturation:
    def test_all_valid(self) -> None:
        data = {
            "gate_0": [0.25, 0.25],
            "gate_1": [0.25, 0.25],
            "gate_2": [0.25, 0.25],
            "gate_3": [0.25, 0.25],
        }
        assert gate_saturation(data) == 0.0

    def test_out_of_range(self) -> None:
        data = {
            "gate_0": [1.5, 0.25],
            "gate_1": [0.25, 0.25],
            "gate_2": [0.25, 0.25],
            "gate_3": [0.25, 0.25],
        }
        val = gate_saturation(data)
        assert val is not None
        assert val == 0.5  # 1 out of 2 samples bad


class TestGenerateReport:
    def test_report_created(self, tmp_path: Path) -> None:
        run = Run(kind="experiment", phase="alpha", hypothesis="test")
        metrics = {"rmse": 0.1, "overshoot": 0.2}
        run_dir = tmp_path / "runs" / run.id
        run_dir.mkdir(parents=True)
        path = generate_report(run, metrics, run_dir)
        assert Path(path).exists()
        content = Path(path).read_text()
        assert "experiment" in content
        assert "rmse" in content


class TestLoadCsvColumns:
    def test_load_existing_csv(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "data.csv"
        csv_path.write_text("x,y,z\n1.0,2.0,3.0\n4.0,5.0,6.0\n")
        cols = load_csv_columns(csv_path)
        assert "x" in cols
        assert len(cols["x"]) == 2
        assert cols["x"][0] == 1.0
        assert cols["y"] == [2.0, 5.0]

    def test_missing_file(self) -> None:
        assert load_csv_columns("/nonexistent.csv") == {}
