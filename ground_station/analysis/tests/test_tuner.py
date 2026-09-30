"""Tests for the (1+1)-ES tuner: defaults first, bounds, progress, incumbent rules, determinism."""
from __future__ import annotations

import math
from collections.abc import Callable

import pytest

from ground_station.analysis.controller_descriptor import CONTROLLERS_DIR, Descriptor, Knob, load
from ground_station.analysis.tuner import GROW, SHRINK, SIGMA0, SIGMA_MAX, SIGMA_MIN, Tuner

PID = load(CONTROLLERS_DIR / "pid.yaml")
# A controller the tuner has never heard of, with both scales: the tuner must only need knobs (Q11).
ZZ = Descriptor("zz", (
    Knob("zz.a", 0x01, 0, default=1.0, lo=0.25, hi=4.0, scale="log"),
    Knob("zz.b", 0x01, 1, default=0.0, lo=-1.0, hi=1.0, scale="lin"),
), shadow_outputs=())
DESCRIPTORS = pytest.mark.parametrize("d", [PID, ZZ], ids=lambda d: d.name)


def unit(knob: Knob, value: float) -> float:
    """Position of ``value`` in [lo, hi] on the knob's scale, written independently of tuner.py."""
    if knob.scale == "log":
        return math.log(value / knob.lo) / math.log(knob.hi / knob.lo)
    return (value - knob.lo) / (knob.hi - knob.lo)


def quadratic(d: Descriptor, optimum: float = 0.8) -> Callable[[dict[str, float]], float]:
    """Synthetic cost: squared distance to the point ``optimum`` on every knob's unit scale."""
    return lambda p: sum((unit(k, p[k.symbol]) - optimum) ** 2 for k in d.knobs)


def defaults(d: Descriptor) -> dict[str, float]:
    return {k.symbol: k.default for k in d.knobs}


def run(tuner: Tuner, J: Callable[[dict[str, float]], float], steps: int) -> list[dict[str, float]]:
    """Propose, evaluate and record ``steps`` times; return the proposals."""
    proposals = []
    for _ in range(steps):
        p = tuner.propose([])
        tuner.record(p, J(p), valid=True)
        proposals.append(p)
    return proposals


@DESCRIPTORS
def test_first_proposal_is_the_defaults_in_descriptor_order(d: Descriptor) -> None:
    p = Tuner(d, seed=0).propose([])
    assert p == defaults(d)
    assert list(p) == [k.symbol for k in d.knobs]


@DESCRIPTORS
def test_proposals_stay_inside_the_knob_ranges(d: Descriptor) -> None:
    for p in run(Tuner(d, seed=1), quadratic(d), steps=200):
        assert list(p) == [k.symbol for k in d.knobs]
        assert all(k.lo <= p[k.symbol] <= k.hi for k in d.knobs), p


@DESCRIPTORS
@pytest.mark.parametrize("seed", range(5))
def test_improves_on_the_defaults_within_60_flights(d: Descriptor, seed: int) -> None:
    J = quadratic(d)
    tuner = Tuner(d, seed)
    run(tuner, J, steps=60)
    assert tuner.best is not None
    assert tuner.best.J < J(defaults(d))


def test_invalid_or_non_finite_results_never_become_best() -> None:
    tuner = Tuner(ZZ, seed=0)
    good = {"zz.a": 2.0, "zz.b": 0.5}
    tuner.record(good, 1.0, valid=True)
    tuner.record({"zz.a": 3.0, "zz.b": 0.1}, 0.1, valid=False)
    tuner.record({"zz.a": 0.5, "zz.b": -0.1}, math.nan, valid=True)
    tuner.record({"zz.a": 0.3, "zz.b": -0.9}, -math.inf, valid=True)
    assert tuner.best is not None
    assert (dict(tuner.best.params), tuner.best.J) == (good, 1.0)


def test_without_a_valid_result_it_restarts_at_the_defaults_with_half_the_step() -> None:
    """Same seed, same normal draws: centred on the defaults in both cases, so the restart's unit-scale
    step is exactly min(sigma, SIGMA0 / 2) / SIGMA0 = 1/2 of the reference step."""
    reference = Tuner(ZZ, seed=0).propose([{"params": defaults(ZZ), "J": 1.0, "valid": True}])
    assert all(0 < unit(k, reference[k.symbol]) < 1 for k in ZZ.knobs), "reference step was clipped"
    restart = Tuner(ZZ, seed=0)
    restart.record(defaults(ZZ), 1.0, valid=False)
    assert restart.best is None
    proposal = restart.propose([])
    for k in ZZ.knobs:
        centre = unit(k, k.default)
        assert unit(k, proposal[k.symbol]) - centre == pytest.approx((unit(k, reference[k.symbol]) - centre) / 2)


def test_history_is_merged_with_missing_knobs_at_their_default() -> None:
    tuner = Tuner(ZZ, seed=0)
    tuner.propose([{"params": {"zz.a": 2.0, "not_a_knob": 5.0}, "J": 0.5, "valid": True}])
    assert tuner.best is not None
    assert dict(tuner.best.params) == {"zz.a": 2.0, "zz.b": 0.0}
    tuner.record(defaults(ZZ), 0.7, valid=True)  # worse than the history result
    assert tuner.best.J == 0.5


def test_sigma_follows_the_one_fifth_rule_and_stays_clamped() -> None:
    tuner = Tuner(ZZ, seed=0)
    tuner.record(defaults(ZZ), 1.0, valid=True)   # first usable result: an improvement
    assert tuner.sigma == pytest.approx(SIGMA0 * GROW)
    tuner.record(defaults(ZZ), 2.0, valid=True)   # worse
    tuner.record(defaults(ZZ), 0.5, valid=False)  # invalid
    assert tuner.sigma == pytest.approx(SIGMA0 * GROW * SHRINK ** 2)
    for _ in range(200):
        tuner.record(defaults(ZZ), 9.0, valid=True)
    assert tuner.sigma == SIGMA_MIN
    for i in range(200):
        tuner.record(defaults(ZZ), -float(i), valid=True)
    assert tuner.sigma == SIGMA_MAX


def test_same_seed_same_proposals_other_seed_other_proposals() -> None:
    J = quadratic(PID)
    first, again, other = (run(Tuner(PID, seed), J, steps=30) for seed in (7, 7, 8))
    assert first == again
    assert first != other
