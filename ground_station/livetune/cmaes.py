"""Dependency-free CMA-ES in numpy (pycma is not installed), ask/tell, seeded and deterministic.

Default parameters follow Hansen, "The CMA Evolution Strategy: A Tutorial" (arXiv:1604.00772): population
lambda = 4 + floor(3 ln n), mu = lambda // 2 log weights, CSA step size, rank-one + rank-mu covariance update.

Mirrored sampling (Brockhoff et al. 2010): the population comes in pairs m + sigma*y, m - sigma*y. Only the better of
each pair enters the recombination (pairwise selection, Auger/Brockhoff/Hansen 2011), which removes the step-size
shrink that plain mirroring causes under weighted recombination.

Box bounds by repair: a sample is clipped into [lower, upper]; the clipped point is what gets evaluated and what the
update learns from, its Mahalanobis length capped at sqrt(n) + 2n/(n+2) as for injected solutions (Hansen 2011,
arXiv:1110.4181). An infinite or NaN f marks an infeasible candidate: it ranks last and never enters the recombination.
The three papers are cited from memory; WP-28 did not fetch them.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


class CMAES:
    """Minimise f over R^n (or a box). ask() -> (lambda, n) candidates, tell(X, f) with f per row."""

    def __init__(self, x0, sigma0: float, lower=None, upper=None, popsize: int | None = None, seed: int = 0) -> None:
        x0 = np.asarray(x0, dtype=float).ravel()
        n = x0.size
        if n < 1 or not np.all(np.isfinite(x0)):
            raise ValueError("x0 must be a non-empty finite vector")
        if not (math.isfinite(sigma0) and sigma0 > 0.0):
            raise ValueError(f"sigma0 must be > 0 (got {sigma0})")
        self.n = n
        self.lower = np.full(n, -np.inf) if lower is None else np.broadcast_to(np.asarray(lower, float), (n,)).copy()
        self.upper = np.full(n, np.inf) if upper is None else np.broadcast_to(np.asarray(upper, float), (n,)).copy()
        if np.any(self.lower >= self.upper):
            raise ValueError("lower must be < upper in every coordinate")
        self.lam = int(popsize) if popsize else 4 + int(math.floor(3.0 * math.log(n)))
        if self.lam < 2:
            raise ValueError(f"popsize must be >= 2 (got {self.lam})")
        self.mu = self.lam // 2
        w = math.log(self.mu + 0.5) - np.log(np.arange(1, self.mu + 1))
        self.weights = w / w.sum()
        mueff = 1.0 / float(np.sum(self.weights ** 2))
        self.cs = (mueff + 2.0) / (n + mueff + 5.0)
        self.ds = 1.0 + 2.0 * max(0.0, math.sqrt((mueff - 1.0) / (n + 1.0)) - 1.0) + self.cs
        self.cc = (4.0 + mueff / n) / (n + 4.0 + 2.0 * mueff / n)
        self.c1 = 2.0 / ((n + 1.3) ** 2 + mueff)
        self.cmu = min(1.0 - self.c1, 2.0 * (mueff - 2.0 + 1.0 / mueff) / ((n + 2.0) ** 2 + mueff))
        self.chi_n = math.sqrt(n) * (1.0 - 1.0 / (4.0 * n) + 1.0 / (21.0 * n * n))
        self.mean = np.clip(x0, self.lower, self.upper)
        self.sigma = float(sigma0)
        self.C = np.eye(n)
        self.ps = np.zeros(n)
        self.pc = np.zeros(n)
        self.gen = 0
        self.evals = 0
        self.best_x: np.ndarray | None = None
        self.best_f = math.inf
        self._rng = np.random.default_rng(seed)
        self._eigen()

    def _eigen(self) -> None:
        self.C = (self.C + self.C.T) / 2.0
        vals, self.B = np.linalg.eigh(self.C)
        self.D = np.sqrt(np.maximum(vals, 1e-20))
        self.invsqrtC = (self.B / self.D) @ self.B.T

    def ask(self) -> np.ndarray:
        """lambda repaired candidates; row i and row i + ceil(lambda/2) are mirrors (the last row may be unpaired)."""
        half = (self.lam + 1) // 2
        z = self._rng.standard_normal((half, self.n))
        y = np.vstack([z, -z])[: self.lam] @ (self.B * self.D).T
        return np.clip(self.mean + self.sigma * y, self.lower, self.upper)

    def tell(self, X, f) -> None:
        """Update from one full population X (as returned by ask) and its f values."""
        X = np.asarray(X, dtype=float)
        f = np.asarray(f, dtype=float).ravel()
        if X.shape != (self.lam, self.n) or f.shape != (self.lam,):
            raise ValueError(f"tell expects X {(self.lam, self.n)} and f ({self.lam},)")
        f = np.where(np.isnan(f), np.inf, f)
        self.gen += 1
        self.evals += self.lam
        i_best = int(np.argmin(f))
        if f[i_best] < self.best_f:
            self.best_f, self.best_x = float(f[i_best]), X[i_best].copy()

        half = (self.lam + 1) // 2
        pairs = [i + half if i + half < self.lam and f[i + half] < f[i] else i for i in range(half)]
        sel = [k for k in sorted(pairs, key=lambda k: f[k]) if math.isfinite(f[k])][: self.mu]
        if not sel:  # nothing feasible to learn from; the caller shrinks sigma
            return
        w = self.weights[: len(sel)] / self.weights[: len(sel)].sum()
        mueff = 1.0 / float(np.sum(w ** 2))
        n = self.n
        y = (X[sel] - self.mean) / self.sigma
        cap = math.sqrt(n) + 2.0 * n / (n + 2.0)
        zn = np.linalg.norm(y @ self.invsqrtC.T, axis=1)
        y *= np.minimum(1.0, cap / np.maximum(zn, 1e-300))[:, None]
        y_w = w @ y
        self.mean = self.mean + self.sigma * y_w
        self.ps = (1.0 - self.cs) * self.ps + math.sqrt(self.cs * (2.0 - self.cs) * mueff) * (self.invsqrtC @ y_w)
        ps_norm = float(np.linalg.norm(self.ps))
        hsig = ps_norm / math.sqrt(1.0 - (1.0 - self.cs) ** (2 * self.gen)) / self.chi_n < 1.4 + 2.0 / (n + 1.0)
        self.pc = (1.0 - self.cc) * self.pc + hsig * math.sqrt(self.cc * (2.0 - self.cc) * mueff) * y_w
        rank_mu = (w[:, None] * y).T @ y
        c1a = self.c1 * (1.0 - (1.0 - hsig) * self.cc * (2.0 - self.cc))
        self.C = (1.0 - c1a - self.cmu) * self.C + self.c1 * np.outer(self.pc, self.pc) + self.cmu * rank_mu
        self.sigma *= math.exp((self.cs / self.ds) * (ps_norm / self.chi_n - 1.0))
        self._eigen()

    def shrink(self, factor: float) -> None:
        """Scale the step size (the safety supervisor shrinks it after a tripped candidate)."""
        self.sigma *= float(factor)

    def state_dict(self) -> dict[str, Any]:
        """JSON-ready state, so a run can resume on the next flight."""
        return {
            "x0": self.mean.tolist(), "sigma": self.sigma, "lower": self.lower.tolist(), "upper": self.upper.tolist(),
            "popsize": self.lam, "C": self.C.tolist(), "ps": self.ps.tolist(), "pc": self.pc.tolist(), "gen": self.gen,
            "evals": self.evals, "best_x": None if self.best_x is None else self.best_x.tolist(),
            "best_f": self.best_f, "rng": self._rng.bit_generator.state,
        }

    @classmethod
    def from_state(cls, st: dict[str, Any]) -> CMAES:
        es = cls(st["x0"], st["sigma"], st["lower"], st["upper"], st["popsize"])
        es.C, es.ps, es.pc = np.asarray(st["C"], float), np.asarray(st["ps"], float), np.asarray(st["pc"], float)
        es.gen, es.evals, es.best_f = int(st["gen"]), int(st["evals"]), float(st["best_f"])
        es.best_x = None if st["best_x"] is None else np.asarray(st["best_x"], float)
        es._rng.bit_generator.state = st["rng"]
        es._eigen()
        return es
