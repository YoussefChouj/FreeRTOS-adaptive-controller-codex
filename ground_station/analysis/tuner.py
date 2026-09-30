"""Controller-agnostic gain tuner for Workflow B campaigns: a seeded (1+1) evolution strategy.

The tuner sees only a Descriptor's knobs, never the controller behind them (spec Q11). It searches
the unit cube of sim/bench: each knob maps to [0, 1] between its lo and hi, on a log or linear
scale (``to_x``/``from_x``, sim/bench/bench.py:82 and :90).

- ``propose(history)`` returns the firmware defaults while no result exists. After that it takes
  the best valid result so far (the incumbent) and adds a Gaussian step of size ``sigma`` in the
  unit cube. With results but none valid, it restarts from the defaults with a smaller step.
- ``record(params, J, valid)`` stores one flight's result and adapts ``sigma`` by the 1/5 success
  rule: grow on an improvement over the incumbent, shrink otherwise.

Invalid results and non-finite J never become the incumbent. The per-flight change limit
(spec Q10 max_step) belongs to the campaign runner, not here.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from ground_station.analysis.controller_descriptor import Descriptor, Knob

SIGMA0 = 0.2  # initial step in the unit cube, as sim/bench/bench.py:20
SIGMA_MIN, SIGMA_MAX = 0.01, 0.5
# 1/5 success rule: one success (x GROW) balances four failures (x SHRINK), so sigma is steady
# when 20 % of proposals improve on the incumbent, grows above that rate and shrinks below it.
GROW, SHRINK = math.exp(1 / 3), math.exp(-1 / 12)


@dataclass(frozen=True)
class Result:
    """One evaluated parameter set. ``params`` holds a value for every knob."""

    params: Mapping[str, float]
    J: float
    valid: bool

    @property
    def usable(self) -> bool:
        """Only valid results with a finite cost may become the incumbent."""
        return self.valid and math.isfinite(self.J)


class Tuner:
    """Proposes the next knob values from past results; deterministic for a given seed."""

    def __init__(self, descriptor: Descriptor, seed: int) -> None:
        self._knobs: tuple[Knob, ...] = descriptor.knobs
        self._rng = np.random.default_rng(seed)
        self._sigma = SIGMA0
        self._history: list[Result] = []   # from earlier campaigns, as passed to the last propose()
        self._recorded: list[Result] = []  # from this tuner's record() calls

    @property
    def sigma(self) -> float:
        """Current step size in the unit cube."""
        return self._sigma

    @property
    def best(self) -> Result | None:
        """The incumbent: the usable result with the lowest J (the earliest on a tie), or None."""
        usable = [r for r in self._history + self._recorded if r.usable]
        return min(usable, key=lambda r: r.J) if usable else None

    def propose(self, history: Sequence[Mapping]) -> dict[str, float]:
        """Next knob values, keyed by knob symbol in descriptor order.

        ``history`` holds results from earlier campaigns as ``{"params": {...}, "J": float,
        "valid": bool}``; pass the same list on every call ([] when there is none). A knob missing
        from ``params`` counts as its default; symbols that are not knobs are ignored.
        """
        self._history = [self._result(item["params"], item["J"], item["valid"]) for item in history]
        if not self._history and not self._recorded:
            return self._defaults()
        best = self.best
        if best is None:
            centre, sigma = self._defaults(), min(self._sigma, SIGMA0 / 2)
        else:
            centre, sigma = best.params, self._sigma
        x = np.array([_to_unit(k, centre[k.symbol]) for k in self._knobs])
        x = np.clip(x + self._rng.normal(0.0, sigma, size=x.size), 0.0, 1.0)
        return {k.symbol: _from_unit(k, float(xi)) for k, xi in zip(self._knobs, x)}

    def record(self, params: Mapping[str, float], J: float, valid: bool) -> None:
        """Store one evaluated proposal and adapt sigma by the 1/5 success rule."""
        result = self._result(params, J, valid)
        best = self.best
        improved = result.usable and (best is None or result.J < best.J)
        factor = GROW if improved else SHRINK
        self._sigma = min(max(self._sigma * factor, SIGMA_MIN), SIGMA_MAX)
        self._recorded.append(result)

    def _defaults(self) -> dict[str, float]:
        return {k.symbol: k.default for k in self._knobs}

    def _result(self, params: Mapping[str, float], J: float, valid: bool) -> Result:
        """A Result with one value per knob: missing knobs take their default, extra keys drop."""
        values = {k.symbol: float(params.get(k.symbol, k.default)) for k in self._knobs}
        return Result(values, float(J), bool(valid))


def _to_unit(knob: Knob, value: float) -> float:
    """Knob value -> [0, 1] (sim/bench/bench.py:82 ``to_x``). A value outside [lo, hi], e.g. from
    an older campaign with another range, maps to the nearer edge."""
    value = min(max(value, knob.lo), knob.hi)
    if knob.scale == "log":
        return math.log(value / knob.lo) / math.log(knob.hi / knob.lo)
    return (value - knob.lo) / (knob.hi - knob.lo)


def _from_unit(knob: Knob, x: float) -> float:
    """[0, 1] -> knob value (sim/bench/bench.py:90 ``from_x``), clamped to [lo, hi] because the
    log form can round one ulp past hi."""
    if knob.scale == "log":
        value = knob.lo * (knob.hi / knob.lo) ** x
    else:
        value = knob.lo + (knob.hi - knob.lo) * x
    return min(max(value, knob.lo), knob.hi)
