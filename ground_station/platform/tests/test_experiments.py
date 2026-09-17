import pytest

from ground_station.platform.experiments import ExperimentRuntime, ExperimentState


def test_experiment_completes_and_restores_exact_parameters():
    runtime = ExperimentRuntime({"gain": 1.0, "bias": 0.25})
    run = runtime.start("sweep", {"gain": 2.0}, settle_ticks=1, measure_ticks=2)
    assert run.state is ExperimentState.SETTLING
    assert runtime.parameters["gain"] == 2.0
    assert runtime.tick({"y": 0.1}) is ExperimentState.MEASURING
    assert runtime.tick({"y": 0.2}) is ExperimentState.MEASURING
    assert runtime.tick({"y": 0.3}) is ExperimentState.COMPLETE
    assert runtime.parameters == {"gain": 1.0, "bias": 0.25}
    assert [event.name for event in run.events] == [
        "experiment_started", "settling_started", "measuring_started",
        "experiment_completed",
    ]


def test_abort_restores_parameters_and_records_reason():
    runtime = ExperimentRuntime({"gain": 1.0})
    run = runtime.start("unsafe", {"gain": 3.0}, settle_ticks=0, measure_ticks=4)
    assert run.state is ExperimentState.MEASURING
    assert runtime.tick({"arm": 0}, safe=lambda row: row["arm"] == 0) is ExperimentState.MEASURING
    assert runtime.tick({"arm": 1}, safe=lambda row: row["arm"] == 0) is ExperimentState.ABORTED
    assert runtime.parameters == {"gain": 1.0}
    assert run.events[-1].name == "experiment_aborted"
    assert "safety" in run.events[-1].detail


def test_runtime_rejects_unknown_or_overlapping_runs():
    runtime = ExperimentRuntime({"gain": 1.0})
    with pytest.raises(KeyError):
        runtime.start("bad", {"missing": 2.0}, 0, 1)
    runtime.start("one", {"gain": 2.0}, 0, 2)
    with pytest.raises(RuntimeError, match="already running"):
        runtime.start("two", {"gain": 3.0}, 0, 1)

