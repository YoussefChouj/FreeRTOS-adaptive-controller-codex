"""Adaptive law — PARITY: API/mrac.c:93-276 (MRAC_ProjectGradient + MRAC_UpdateAxis).

One axis of the MRAC weight update. The reference-model update (xm and the scalar
gain P) is done in sim/reference_model.py; this module consumes the tracking error
e, that P, and the regressor Phi (sim/regressor.py) and produces u_ad.

Ported under the active firmware build flags:
  * FIX_LEAKAGE_NORMALIZATION = 1  -> leakage terms are NOT divided by denom.
  * ENABLE_PERFORMANCE_RECOVERY = 1 -> u_ad is a 1st-order LPF of Theta.Phi.

Swapping the adaptive law (sigma-mod / e-mod / DF-MRAC / NN ...) is meant to be a
one-file change here (ADR-0006), so the variants are toggled by AdaptiveFlags /
config exactly like mrac_flags / mrac_config_* in firmware.

Firmware quirk replicated for parity: What_lower_limit is never explicitly set for
slots 1-5, so those slots default to 0 (weights clipped at 0). Slot 0 is unlocked
to -What_limit[0] for pitch/roll/yaw to match the asymmetric fix in mrac.c:353-355;
z keeps slot 0 at 0 to match firmware (z has no bias unlock).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from sim.drive import for_law

NUM_BASIS = 6


@dataclass
class AdaptiveFlags:
    """Mirrors the adaptive-relevant subset of MRAC_FeatureFlags_t."""
    adaptation_on: bool = True
    projection_on: bool = True
    deadzone_on: bool = True
    hard_freeze_on: bool = True
    tanh_saturation_on: bool = True
    e_modification_on: bool = True
    l1_filtering_on: bool = False
    # σ_prior attractor (ADR-0013 D5). When True and AdaptiveLaw has a
    # non-None ``theta_prior`` plus ``sigma_prior > 0``, an extra leak
    # term ``-sigma_prior * (Theta - theta_prior)`` is added alongside
    # the existing σ-mod leak. Default off → bit-identical behaviour to
    # the pre-change code on every existing scenario.
    sigma_prior_on: bool = False


@dataclass
class AxisAdaptiveConfig:
    """Mirrors the adaptive-relevant subset of MRAC_AxisConfig_t (mrac.c MRAC_Init)."""
    gamma: Sequence[float]
    What_limit: Sequence[float]
    What_tol: Sequence[float]
    What_lower_limit: Sequence[float] = field(
        default_factory=lambda: [0.0] * NUM_BASIS)  # firmware never sets it -> 0
    sigma: float = 0.01
    sigma_lf: float = 0.0
    gam_f: float = 16.0
    omega_u: float = 30.0
    e_deadzone: float = 0.05
    e_freeze: float = 0.0
    e_sat: float = 0.0
    k_e: float = 0.05
    # σ_prior attractor (ADR-0013 D5). The dimensionless prior weight
    # ``theta_prior`` is opt-in; ``sigma_prior=0`` (default) keeps every
    # pre-existing call site bit-identical.
    sigma_prior: float = 0.0
    theta_prior: Optional[np.ndarray] = None

    # per-axis firmware defaults (mrac.c:284-368)
    _PR_GAMMA = [1.5, 0.2, 0.05, 0.05, 0.1, 0.1]
    _PR_WLIM = [0.15, 0.05, 0.02, 0.05, 0.20, 0.15]
    _PR_WTOL = [0.03, 0.01, 0.005, 0.01, 0.04, 0.03]
    _Z_GAMMA = [2.0, 0.5, 0.10, 0.10, 0.2, 0.2]
    _Z_WLIM = [1.00, 0.10, 0.05, 0.05, 0.20, 0.20]
    _Z_WTOL = [0.20, 0.02, 0.01, 0.01, 0.04, 0.04]

    @classmethod
    def for_axis(cls, axis: str) -> "AxisAdaptiveConfig":
        if axis in ("pitch", "roll"):
            lower = [-cls._PR_WLIM[0]] + [0.0] * (NUM_BASIS - 1)  # slot 0 only (mrac.c:354)
            return cls(gamma=list(cls._PR_GAMMA), What_limit=list(cls._PR_WLIM),
                       What_tol=list(cls._PR_WTOL), What_lower_limit=lower,
                       sigma=0.01, sigma_lf=0.8, gam_f=16.0, omega_u=30.0,
                       e_deadzone=0.05, e_freeze=1.2, e_sat=0.5, k_e=0.05)
        if axis == "yaw":
            pr_wlim = cls._PR_WLIM
            lower = [-pr_wlim[0] * 0.6] + [0.0] * (NUM_BASIS - 1)  # slot 0 only (mrac.c:355)
            return cls(gamma=[1.0, 0.1, 0.05, 0.05, 0.1, 0.1],
                       What_limit=[v * 0.6 for v in pr_wlim],
                       What_tol=[v * 0.6 for v in cls._PR_WTOL],
                       What_lower_limit=lower,
                       sigma=0.01, sigma_lf=1.0, gam_f=16.0, omega_u=20.0,
                       e_deadzone=0.05, e_freeze=1.0, e_sat=0.7, k_e=0.05)
        if axis == "z":
            # z has NO bias unlock in firmware (mrac.c:353-355 only covers pitch/roll/yaw).
            # Explicitly set all slots to 0.0 to prevent a future reader from
            # "fixing" this to match the other axes.
            lower = [0.0] * 6
            return cls(gamma=list(cls._Z_GAMMA), What_limit=list(cls._Z_WLIM),
                       What_tol=list(cls._Z_WTOL), What_lower_limit=lower,
                       sigma=0.01, sigma_lf=0.0, gam_f=16.0, omega_u=20.0,
                       e_deadzone=0.05, e_freeze=1.2, e_sat=0.4, k_e=0.05)
        raise ValueError(f"unknown axis {axis!r}")


def _project_gradient(grad, theta, limit, tol, lower):
    """Scale/zero the gradient at the projection boundary (mrac.c:93-143)."""
    g = grad.copy()
    for i in range(len(g)):
        upper, low, band, gi, w = limit[i], lower[i], tol[i], g[i], theta[i]
        if band <= 0.0:
            if (w >= upper and gi > 0.0) or (w <= low and gi < 0.0):
                g[i] = 0.0
            continue
        if gi > 0.0:
            if w >= upper:
                gi = 0.0
            elif w > upper - band:
                scale = (upper - w) / band
                gi *= scale if scale > 0.0 else 0.0
        elif gi < 0.0:
            if w <= low:
                gi = 0.0
            elif w < low + band:
                scale = (w - low) / band
                gi *= scale if scale > 0.0 else 0.0
        g[i] = gi
    return g


class AdaptiveLaw:
    """One-axis MRAC weight update + adaptive output u_ad."""

    def __init__(self, config: AxisAdaptiveConfig, flags: AdaptiveFlags,
                 dt: float = 0.005, perf_recovery: bool = True,
                 num_basis: int = NUM_BASIS, state_space: bool = False,
                 wc_edot: float = 30.0):
        self.cfg = config
        self.flags = flags
        self.dt = dt
        self.perf_recovery = perf_recovery  # ENABLE_PERFORMANCE_RECOVERY
        # state_space=True -> 2nd-order matrix-P Lyapunov drive s = e*Pe + e_dot*Pedot
        # (ADR-0007); False -> scalar heuristic drive s = e*P (ADR-0003, 1st/passthrough).
        self.state_space = state_space
        self._drive = for_law(state_space)  # Lyapunov drive seam (sim/drive.py)
        self.wc_edot = wc_edot              # [rad/s] LPF cutoff for the rate-derivative
        self.n = num_basis
        # cached as arrays for the elementwise update
        self._gamma = np.asarray(config.gamma, float)
        self._limit = np.asarray(config.What_limit, float)
        self._tol = np.asarray(config.What_tol, float)
        self._lower = np.asarray(config.What_lower_limit, float)
        # σ_prior (ADR-0013 D5). The default ``sigma_prior=0`` makes the
        # leak contribution identically zero on every existing call site.
        self._sigma_prior = float(getattr(config, "sigma_prior", 0.0))
        prior = getattr(config, "theta_prior", None)
        if prior is None:
            self._theta_prior = None
        else:
            prior_arr = np.asarray(prior, dtype=float)
            if prior_arr.shape != (self.n,):
                raise ValueError(
                    f"theta_prior shape {prior_arr.shape} does not match "
                    f"num_basis={self.n}"
                )
            self._theta_prior = prior_arr
        self.reset()

    def reset(self) -> None:
        self.Theta = np.zeros(self.n)
        self.Whatf = np.zeros(self.n)
        self.u_ad = 0.0
        self._x_prev = 0.0      # previous plant rate (finite-difference derivative)
        self._xdot_f = 0.0      # LPF'd plant-rate derivative (angular-accel estimate)
        self.e_dot = 0.0        # tracking-error derivative e_dot = xdot_f - xm_dot

    def update(self, e: float, P: float, phi, *, x: float = 0.0,
               xm_dot: float = 0.0, Pe: float = 0.0, Pedot: float = 0.0) -> float:
        """Advance the weights one tick and return u_ad (mrac.c:195-275).

        Scalar law: pass the scalar gain ``P`` (drive s = e*P). State-space law
        (``state_space=True``, 2nd-order matrix P): pass ``Pe``/``Pedot`` (= 2nd
        column of P) plus the plant rate ``x`` and reference velocity ``xm_dot`` so
        the filtered rate-derivative error e_dot can be formed (drive s = e*Pe +
        e_dot*Pedot). ADR-0007.
        """
        cfg, fl = self.cfg, self.flags
        phi = np.asarray(phi, float)
        denom = 1.0 + float(phi @ phi)

        do_adapt = (not fl.deadzone_on) or (abs(e) >= cfg.e_deadzone)

        # Filtered finite-difference of the plant rate -> angular-accel estimate,
        # then the tracking-error derivative. Runs every tick (kept warm even in
        # freeze/deadzone) exactly as the firmware does. Mirror: mrac.c MRAC_UpdateAxis.
        raw_xdot = (x - self._x_prev) / self.dt
        self._xdot_f += self.dt * self.wc_edot * (raw_xdot - self._xdot_f)
        self._x_prev = x
        self.e_dot = self._xdot_f - xm_dot

        # hard freeze: zero output, preserve weights
        if fl.hard_freeze_on and cfg.e_freeze > 0.0 and abs(e) > cfg.e_freeze:
            self.u_ad = 0.0
            return self.u_ad

        PBe = e
        if fl.tanh_saturation_on and cfg.e_sat > 0.0:
            PBe = cfg.e_sat * math.tanh(PBe / cfg.e_sat)

        # Lyapunov drive signal s (= e_v^T P B), via the selected Drive (sim/drive.py).
        # e_dot is LPF-bounded and not separately tanh-saturated in Phase 1 (e is, via PBe).
        s = self._drive(PBe=PBe, P=P, e_dot=self.e_dot, Pe=Pe, Pedot=Pedot)

        if fl.adaptation_on and do_adapt:
            sigma_e = cfg.k_e * abs(e) if fl.e_modification_on else 0.0
            sigma_eff = cfg.sigma + sigma_e
            sigma_lf = cfg.sigma_lf if fl.l1_filtering_on else 0.0

            grad = (-s * phi) / denom
            if fl.projection_on:
                grad = _project_gradient(grad, self.Theta, self._limit,
                                         self._tol, self._lower)
            # FIX_LEAKAGE_NORMALIZATION=1: leakage terms not divided by denom
            # σ_prior attractor (ADR-0013 D5) — opt-in. When
            # ``sigma_prior_on`` is False or ``self._theta_prior is None``
            # or ``self._sigma_prior == 0``, this term is identically
            # zero and the update is bit-identical to the pre-change code.
            sigma_prior_term = 0.0
            if (fl.sigma_prior_on
                    and self._theta_prior is not None
                    and self._sigma_prior != 0.0):
                sigma_prior_term = self._sigma_prior * (
                    self.Theta - self._theta_prior)
            y = self._gamma * (grad
                               - sigma_lf * (self.Theta - self.Whatf)
                               - sigma_eff * self.Theta
                               - sigma_prior_term)
            self.Theta += self.dt * y
            if fl.l1_filtering_on:
                self.Whatf += self.dt * cfg.gam_f * (self.Theta - self.Whatf)

        raw_u_ad = float(self.Theta @ phi)
        if self.perf_recovery:
            self.u_ad += self.dt * cfg.omega_u * (raw_u_ad - self.u_ad)
        else:
            self.u_ad = raw_u_ad
        return self.u_ad
