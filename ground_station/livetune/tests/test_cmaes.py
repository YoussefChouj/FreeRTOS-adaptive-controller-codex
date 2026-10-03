"""CMA-ES: converges on sphere and Rosenbrock, deterministic per seed, repairs into the box, resumes from state."""

from __future__ import annotations

import json

import numpy as np
import pytest

from ground_station.livetune.cmaes import CMAES


def sphere(x):
    return float(np.sum(x ** 2))


def rosenbrock(x):
    return float(np.sum(100.0 * (x[1:] - x[:-1] ** 2) ** 2 + (1.0 - x[:-1]) ** 2))


def run(es, f, max_evals, target):
    while es.evals < max_evals and es.best_f > target:
        X = es.ask()
        es.tell(X, [f(x) for x in X])
    return es


def test_popsize_is_hansen_default_and_population_is_mirrored():
    es = CMAES(np.zeros(3), 0.5, seed=1)
    assert es.lam == 4 + int(np.floor(3 * np.log(3))) == 7
    X = es.ask()
    half = (es.lam + 1) // 2
    for i in range(es.lam - half):
        assert np.allclose(X[i] + X[i + half], 2 * es.mean)


def test_sphere_converges():
    es = run(CMAES(np.full(5, 2.0), 1.0, seed=3), sphere, 4000, 1e-10)
    assert es.best_f <= 1e-10
    assert np.allclose(es.mean, 0.0, atol=1e-4)


def test_rosenbrock_converges():
    es = run(CMAES(np.zeros(3), 0.5, seed=5), rosenbrock, 8000, 1e-8)
    assert es.best_f <= 1e-8
    assert np.allclose(es.best_x, 1.0, atol=1e-3)


def test_same_seed_same_run_other_seed_differs():
    a = run(CMAES(np.full(4, 1.0), 0.3, seed=7), sphere, 400, 0.0)
    b = run(CMAES(np.full(4, 1.0), 0.3, seed=7), sphere, 400, 0.0)
    c = run(CMAES(np.full(4, 1.0), 0.3, seed=8), sphere, 400, 0.0)
    assert np.array_equal(a.mean, b.mean) and a.sigma == b.sigma and a.best_f == b.best_f
    assert not np.array_equal(a.mean, c.mean)


def test_box_repair_keeps_every_candidate_inside_and_finds_the_bound_optimum():
    lo, hi = np.full(3, 0.5), np.full(3, 2.0)  # sphere optimum 0 lies outside; box optimum is the corner 0.5
    es = CMAES(np.full(3, 1.5), 1.0, lo, hi, seed=2)
    for _ in range(200):
        X = es.ask()
        assert np.all(X >= lo) and np.all(X <= hi)
        es.tell(X, [sphere(x) for x in X])
    assert np.allclose(es.best_x, 0.5, atol=1e-3)


def test_infeasible_candidates_never_move_the_mean_and_all_infeasible_is_a_no_op():
    es = CMAES(np.zeros(2), 0.5, seed=4)
    m0 = es.mean.copy()
    X = es.ask()
    es.tell(X, [np.inf] * es.lam)
    assert np.array_equal(es.mean, m0) and es.gen == 1
    X = es.ask()
    f = [np.inf if x[0] > 0 else sphere(x) for x in X]
    es.tell(X, f)
    assert es.mean[0] <= m0[0] + 1e-12  # only x0 <= 0 points were feasible


def test_state_round_trip_continues_identically():
    a = run(CMAES(np.full(3, 1.0), 0.4, seed=9), sphere, 140, 0.0)
    b = CMAES.from_state(json.loads(json.dumps(a.state_dict())))
    for es in (a, b):
        run(es, sphere, 280, 0.0)
    assert np.allclose(a.mean, b.mean) and a.sigma == pytest.approx(b.sigma)


def test_rejects_bad_arguments():
    with pytest.raises(ValueError):
        CMAES([], 1.0)
    with pytest.raises(ValueError):
        CMAES([0.0], 0.0)
    with pytest.raises(ValueError):
        CMAES([0.0, 0.0], 1.0, lower=[1.0, 0.0], upper=[0.0, 1.0])
