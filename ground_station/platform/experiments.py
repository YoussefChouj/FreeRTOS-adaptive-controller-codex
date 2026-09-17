"""Deterministic, abort-safe firmware experiment runtime model."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Mapping


class ExperimentState(str, Enum):
    IDLE = "idle"
    SETTLING = "settling"
    MEASURING = "measuring"
    COMPLETE = "complete"
    ABORTED = "aborted"


@dataclass(frozen=True)
class ExperimentEvent:
    name: str
    tick: int
    detail: str = ""


@dataclass
class ExperimentRun:
    name: str
    settle_ticks: int
    measure_ticks: int
    state: ExperimentState = ExperimentState.IDLE
    tick: int = 0
    samples: list[Mapping[str, float]] = field(default_factory=list)
    events: list[ExperimentEvent] = field(default_factory=list)
    parameters_before: dict[str, float] = field(default_factory=dict)
    parameters_after: dict[str, float] = field(default_factory=dict)

    def marker(self, name: str, detail: str = "") -> None:
        self.events.append(ExperimentEvent(name, self.tick, detail))


class ExperimentRuntime:
    """Owns snapshots and guarantees restore on complete, abort, or error."""

    def __init__(self, parameters: Mapping[str, float]):
        self.parameters = dict(parameters)
        self.active: ExperimentRun | None = None

    def start(self, name: str, updates: Mapping[str, float],
              settle_ticks: int, measure_ticks: int) -> ExperimentRun:
        if self.active is not None and self.active.state in {
            ExperimentState.SETTLING, ExperimentState.MEASURING,
        }:
            raise RuntimeError("experiment already running")
        if settle_ticks < 0 or measure_ticks <= 0:
            raise ValueError("invalid settling/measuring window")
        unknown = set(updates) - set(self.parameters)
        if unknown:
            raise KeyError(f"unknown experiment parameters: {sorted(unknown)}")
        run = ExperimentRun(name, settle_ticks, measure_ticks,
                            parameters_before=dict(self.parameters))
        self.active = run
        self.parameters.update(updates)
        run.marker("experiment_started", name)
        if settle_ticks:
            run.state = ExperimentState.SETTLING
            run.marker("settling_started")
        else:
            run.state = ExperimentState.MEASURING
            run.marker("measuring_started")
        return run

    def tick(self, sample: Mapping[str, float] | None = None,
             abort: str | None = None,
             safe: Callable[[Mapping[str, float]], bool] | None = None) -> ExperimentState:
        run = self.active
        if run is None or run.state not in {ExperimentState.SETTLING, ExperimentState.MEASURING}:
            raise RuntimeError("no experiment is running")
        run.tick += 1
        observation = dict(sample or {})
        if abort:
            return self.abort(abort)
        if safe is not None and not safe(observation):
            return self.abort("safety predicate failed")
        if run.state is ExperimentState.SETTLING:
            if run.tick >= run.settle_ticks:
                run.state = ExperimentState.MEASURING
                run.marker("measuring_started")
            return run.state
        run.samples.append(observation)
        if len(run.samples) >= run.measure_ticks:
            return self.complete()
        return run.state

    def complete(self) -> ExperimentState:
        run = self._require_active()
        if run.state is not ExperimentState.MEASURING:
            raise RuntimeError("experiment is not measuring")
        self._restore(run)
        run.state = ExperimentState.COMPLETE
        run.marker("experiment_completed")
        return run.state

    def abort(self, reason: str) -> ExperimentState:
        run = self._require_active()
        self._restore(run)
        run.state = ExperimentState.ABORTED
        run.marker("experiment_aborted", reason)
        self.active = None  # clear so GET /experiments returns []
        return run.state

    def _restore(self, run: ExperimentRun) -> None:
        self.parameters.clear()
        self.parameters.update(run.parameters_before)
        run.parameters_after = dict(self.parameters)

    def _require_active(self) -> ExperimentRun:
        if self.active is None:
            raise RuntimeError("no experiment is running")
        return self.active

