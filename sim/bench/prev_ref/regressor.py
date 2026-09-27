"""Structured regressor Phi(x) — PARITY: API/mrac.c:65-91 (MRAC_GenerateStructuredBasis).

Hand-ported (grill Option A) under the active firmware flags
USE_STRUCTURED_UNCERTAINTY=1 + INCLUDE_CONTROL_IN_REGRESSOR=1 => MAX_NUM_BASIS=6.
The golden-vector test (tests/test_regressor.py) pins these formulas; if mrac.c
drifts, that test fails. Keep this file and mrac.c:65-91 in lockstep.

Six terms: [bias, x, x*tanh x, cross_coupling, u_nom, xm], where the cross slot
is the gyroscopic coupling for pitch/roll and deliberately 0 for yaw/z (u_nom
already occupies slot 4, so a non-zero slot 3 would be collinear and drift).

ADR-0014 D3 promotes each basis function from a hand-derived slot to a
declared object carrying (input, dimension, normalising scale). The current
six-slot regressor is the pinned baseline (``BASIS_DEFAULT``); new variants
register their own basis lists via :class:`BasisDeclaration`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from sim.firmware_parity import FIRMWARE_REGRESSOR
from sim.priors import RegressorVariant

# The active firmware declaration determines the sim vector shape.
NUM_BASIS = FIRMWARE_REGRESSOR.basis_count

# axes whose slot-3 carries gyroscopic cross-coupling (others keep it empty)
_CROSS_AXES = ("pitch", "roll")


@dataclass(frozen=True)
class BasisDeclaration:
    """Per-basis declaration (ADR-0014 D3).

    Each basis function declares:
      * ``name``        : identifier ("bias", "rate", "drag", ...)
      * ``input``       : which regressor input the slot consumes
      * ``dimension``   : physical dimension symbol (e.g. "1", "rad/s")
      * ``normalise``   : characteristic scale that renders the slot O(1)

    The dimensionless form is computed mechanically from the declaration;
    a future regressor with new basis slots declares them the same way.
    """
    name: str
    input: str
    dimension: str
    normalise: float


# Pinned firmware baseline (mrac.c:65-91). Slot order is load-bearing — the
# golden-vector test in tests/test_regressor.py and the parity test against
# MRAC_GenerateStructuredBasis both index by position. Do not reorder.
BASIS_DEFAULT: tuple[BasisDeclaration, ...] = (
    BasisDeclaration(name="bias", input="const", dimension="1", normalise=1.0),
    BasisDeclaration(name="rate", input="x", dimension="rad/s", normalise=1.0),
    BasisDeclaration(name="drag", input="x", dimension="1", normalise=1.0),
    BasisDeclaration(name="cross", input="cross", dimension="rad^2/s^2",
                      normalise=1.0),
    BasisDeclaration(name="u_nom", input="u_nom", dimension="Nm", normalise=1.0),
    BasisDeclaration(name="xm", input="xm", dimension="rad/s", normalise=1.0),
)
assert len(BASIS_DEFAULT) == NUM_BASIS


def _scale_phi(phi: np.ndarray, bases: Sequence[BasisDeclaration]) -> np.ndarray:
    """Affine-rescale a regressor vector against each basis's normalise scale.

    Slot ``i`` is multiplied by ``1.0 / bases[i].normalise`` so a unit
    magnitude at the declared characteristic scale maps to ``1.0`` in
    the dimensionless space. When all normalise scales are ``1.0`` this is
    the identity, preserving bit-identical parity with the firmware baseline.
    """
    out = np.empty_like(phi)
    for i, b in enumerate(bases):
        if b.normalise == 1.0:
            out[i] = phi[i]
        else:
            out[i] = phi[i] / b.normalise
    return out


def structured_regressor(axis: str, *, x: float, u_nom: float, xm: float,
                         cross: float = 0.0,
                         variant: RegressorVariant | None = None,
                         ) -> np.ndarray:
    """Build the 6-element Phi for one axis, mirroring mrac.c:65-91.

    ``cross`` is used only for pitch/roll; it is ignored for yaw/z (slot 3 = 0).
    ``variant`` selects a declared regressor variant (ADR-0014 D4). The default
    is the pinned firmware baseline; variants with non-trivial normalise scales
    produce rescaled outputs that are sim-only until promoted with a parity test.
    """
    phi = np.empty(NUM_BASIS)
    phi[0] = 1.0                 # bias
    phi[1] = x                   # damping
    phi[2] = x * math.tanh(x)    # bounded nonlinear drag
    phi[3] = cross if axis in _CROSS_AXES else 0.0
    phi[4] = u_nom               # control scaling
    phi[5] = xm                  # reference feedforward
    if variant is None or variant.name == "default":
        return phi
    if variant.num_basis != NUM_BASIS:
        raise ValueError(
            f"variant {variant.name!r} expects num_basis={variant.num_basis}, "
            f"structured_regressor returns {NUM_BASIS}"
        )
    return _scale_phi(phi, BASIS_DEFAULT)


def cross_coupling(axis: str, *, pitch_rate: float, roll_rate: float,
                   yaw_rate: float) -> float:
    """Gyroscopic cross term for an axis (mrac.c:445-446).

    Firmware aliases p=pitch, q=roll, r=yaw; cross_pitch=q*r, cross_roll=p*r.
    """
    if axis == "pitch":
        return roll_rate * yaw_rate
    if axis == "roll":
        return pitch_rate * yaw_rate
    return 0.0  # yaw, z


# ---------------------------------------------------------------------------
# ADR-0014 D3 example variant: "inertia_scaled"
#
# Demonstrates the per-basis declaration seam end-to-end. The dimensionless
# rescale uses a characteristic rate ``ref_eff`` (rad/s) to pull the rate
# and reference slots toward O(1). Sim-only — no firmware parity test.
# ---------------------------------------------------------------------------
_INERTIA_SCALED_REF = 20.0  # rad/s, rough closed-loop inner-rate bandwidth

BASIS_INERTIA_SCALED: tuple[BasisDeclaration, ...] = (
    BasisDeclaration(name="bias", input="const", dimension="1", normalise=1.0),
    BasisDeclaration(name="rate", input="x", dimension="rad/s",
                      normalise=_INERTIA_SCALED_REF),
    BasisDeclaration(name="drag", input="x", dimension="1",
                      normalise=_INERTIA_SCALED_REF),
    BasisDeclaration(name="cross", input="cross", dimension="rad^2/s^2",
                      normalise=_INERTIA_SCALED_REF * _INERTIA_SCALED_REF),
    BasisDeclaration(name="u_nom", input="u_nom", dimension="Nm", normalise=1.0),
    BasisDeclaration(name="xm", input="xm", dimension="rad/s",
                      normalise=_INERTIA_SCALED_REF),
)

# Register the inertia-scaled variant. The pinned baseline is registered
# from sim.priors at import time as "default".
RegressorVariant.register(name="inertia_scaled",
                          num_basis=len(BASIS_INERTIA_SCALED))


def regressor_inertia_scaled(axis: str, *, x: float, u_nom: float,
                             xm: float, cross: float = 0.0,
                             ref_eff: float = _INERTIA_SCALED_REF) -> np.ndarray:
    """Affine-rescaled variant (ADR-0014 D3 example).

    Slot 1, 2, 3, 5 are rescaled by ``ref_eff`` so a typical 20 rad/s
    response maps to slot magnitudes near 1. Sim-only.
    """
    phi = structured_regressor(axis, x=x, u_nom=u_nom, xm=xm, cross=cross)
    scales = np.array([
        1.0, ref_eff, ref_eff, ref_eff * ref_eff, 1.0, ref_eff,
    ])
    return phi / scales