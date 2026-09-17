"""Tests for ground_station.analysis.runs."""
from __future__ import annotations

from ground_station.analysis.runs import (
    compare_runs,
    detect_settling,
    summarize_run,
)
from ground_station.platform.experiments import (
    ExperimentRun,
    ExperimentRuntime,
    ExperimentState,
)


class TestSummarizeRun:
    def test_idle_run(self):
        run = ExperimentRun(name="test", settle_ticks=10, measure_ticks=100)
        summary = summarize_run(run)
        assert summary["name"] == "test"
        assert summary["state"] == "idle"
        assert summary["duration_ms"] == 0.0

    def test_complete_run_timing(self):
        run = ExperimentRun(name="test", settle_ticks=10, measure_ticks=100)
        run.state = ExperimentState.COMPLETE
        run.tick = 110
        summary = summarize_run(run)
        assert summary["state"] == "complete"
        assert summary["duration_ms"] > 0
        assert summary["settle_ms"] == 10.0

    def test_parameter_changes_recorded(self):
        run = ExperimentRun(name="test", settle_ticks=10, measure_ticks=100,
                            parameters_before={"kp": 1.0, "ki": 0.5},
                            parameters_after={"kp": 2.0, "ki": 0.5})
        summary = summarize_run(run)
        changes = summary["parameter_changes"]
        assert ("kp", 1.0, 2.0) in changes
        assert ("ki", 0.5, 0.5) not in changes

    def test_safety_aborted_flag(self):
        run = ExperimentRun(name="test", settle_ticks=10, measure_ticks=100)
        from ground_station.platform.experiments import ExperimentEvent
        run.events.append(ExperimentEvent("experiment_aborted", 50, "safety predicate failed"))
        summary = summarize_run(run)
        assert summary["safety_aborted"] is True


class TestCompareRuns:
    def test_empty_list(self):
        assert compare_runs([]) == []

    def test_compares_multiple_runs(self):
        run_a = ExperimentRun(name="run_a", settle_ticks=5, measure_ticks=50,
                             parameters_before={"kp": 1.0},
                             parameters_after={"kp": 2.0})
        run_b = ExperimentRun(name="run_b", settle_ticks=5, measure_ticks=50,
                             parameters_before={"kp": 1.0},
                             parameters_after={"kp": 3.0})
        rows = compare_runs([run_a, run_b])
        assert len(rows) == 2
        assert rows[0]["name"] == "run_a"
        assert rows[1]["name"] == "run_b"


class TestDetectSettling:
    def test_never_settled(self):
        samples = [{"v": i} for i in range(20)]
        assert detect_settling(samples, "v", window=10, threshold=0.001) == -1

    def test_settles_at_index(self):
        # First 5 oscillating (std≈0.55), then constant zeros (std=0)
        samples = [{"v": float(i % 2)} for i in range(5)]
        samples += [{"v": 0.0} for _ in range(20)]
        idx = detect_settling(samples, "v", window=5, threshold=0.4)
        # Window at i=4 has std≈0.55 > 0.4; at i=5 std≈0.45 > 0.4;
        # at i=6 the window is all zeros: std=0 < 0.4 → settled
        assert idx >= 5

    def test_empty_samples(self):
        assert detect_settling([], "v") == -1

    def test_window_larger_than_samples(self):
        samples = [{"v": 1.0}, {"v": 2.0}]
        assert detect_settling(samples, "v", window=10) == -1

    def test_window_one(self):
        samples = [{"v": 1.0}, {"v": 1.0}, {"v": 1.0}]
        idx = detect_settling(samples, "v", window=1, threshold=0.001)
        # With window=1, std is always 0, should settle immediately
        assert idx == 0

    def test_threshold_not_met(self):
        samples = [{"v": float(i % 3)} for i in range(30)]
        assert detect_settling(samples, "v", window=10, threshold=0.001) == -1

    def test_missing_key_skipped(self):
        samples = [{"other": 1.0}, {"v": 0.0}, {"v": 0.0}, {"v": 0.0}]
        idx = detect_settling(samples, "v", window=3, threshold=0.001)
        assert idx == 2
