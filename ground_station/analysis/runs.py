"""Analysis tools for experiment runs and parameter sweeps."""
from __future__ import annotations

from ground_station.platform.experiments import ExperimentRun


def summarize_run(run: ExperimentRun) -> dict:
    """Return a summary dict for an ExperimentRun.

    {name, state, duration_ms, settle_ms, measure_ms,
     parameter_changes: [(name, before, after), ...],
     event_markers: [(tick, name, detail), ...],
     safety_aborted: bool}
    """
    duration_ms = 0.0
    settle_ms = 0.0
    measure_ms = 0.0
    # Rough timing: assume 1 ms per tick (adjust if real timing is available)
    if run.state.value in ("complete", "aborted"):
        total_ticks = run.tick
        if run.settle_ticks:
            settle_ms = run.settle_ticks * 1.0
        measure_ms = (total_ticks - run.settle_ticks) * 1.0 if total_ticks > run.settle_ticks else 0.0
        duration_ms = settle_ms + measure_ms
    elif run.state.value in ("settling", "measuring"):
        total_ticks = run.tick
        if run.state.value == "settling":
            settle_ms = total_ticks * 1.0
        else:
            settle_ms = run.settle_ticks * 1.0
            measure_ms = total_ticks * 1.0
        duration_ms = settle_ms + measure_ms

    param_changes = []
    for name in sorted(set(list(run.parameters_before.keys()) + list(run.parameters_after.keys()))):
        before = run.parameters_before.get(name)
        after = run.parameters_after.get(name)
        if before != after:
            param_changes.append((name, before, after))

    safety_aborted = any(
        e.name == "experiment_aborted" and "safety" in e.detail.lower()
        for e in run.events
    )

    return {
        "name": run.name,
        "state": run.state.value,
        "duration_ms": duration_ms,
        "settle_ms": settle_ms,
        "measure_ms": measure_ms,
        "parameter_changes": param_changes,
        "event_markers": [(e.tick, e.name, e.detail) for e in run.events],
        "safety_aborted": safety_aborted,
    }


def compare_runs(runs: list[ExperimentRun]) -> list[dict]:
    """Compare multiple experiment runs.

    Returns a table: [{name, state, duration_ms, parameter_deltas, events}, ...]
    """
    rows = []
    for run in runs:
        deltas = {}
        for name in sorted(run.parameters_before.keys()):
            delta = run.parameters_after.get(name, run.parameters_before[name]) - run.parameters_before[name]
            if delta != 0:
                deltas[name] = delta
        rows.append({
            "name": run.name,
            "state": run.state.value,
            "duration_ms": summarize_run(run)["duration_ms"],
            "parameter_deltas": deltas,
            "events": [(e.tick, e.name, e.detail) for e in run.events],
        })
    return rows


def detect_settling(samples: list[dict[str, float]], key: str,
                    window: int = 10, threshold: float = 0.01) -> int:
    """Detect the first index where the rolling std of `key` drops below threshold.

    Returns index or -1 if never settled.
    """
    if not samples:
        return -1
    if window < 1:
        raise ValueError("window must be >= 1")
    vals = [s[key] for s in samples if key in s]
    n = len(vals)
    if n < window:
        return -1
    for i in range(window - 1, n):
        window_vals = vals[i - window + 1:i + 1]
        if len(window_vals) < window:
            continue
        std = _std(window_vals)
        if std < threshold:
            return i
    return -1


def _std(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / len(vals)
    variance = sum((v - mean) ** 2 for v in vals) / len(vals)
    return variance ** 0.5
