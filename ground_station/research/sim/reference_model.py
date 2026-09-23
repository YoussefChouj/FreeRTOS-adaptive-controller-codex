"""Reference model — per-axis desired rate response xm the plant is asked to
track.

Ported from the original project's sim/reference_model.py. Two model types:
  * SECOND_ORDER: 2nd-order ``wn^2*(r-xm) - 2*zeta*wn*xm_dot`` (roll/pitch)
  * FIRST_ORDER:  1st-order ``bw*(r-xm)`` (yaw)

step(r) advances xm THEN returns it (mirrors firmware mrac.c:168-196).
reset(x0) is the bumpless snap xm=x0, xm_dot=0.
"""
from __future__ import annotations

from typing import Optional


# Per-axis firmware-configured reference models.
# Source: sim/reference_model.py _AXIS_CFG
#   roll/pitch: 2nd order, bw=44.0 rad/s, zeta=0.8
#   yaw: 1st order, bw=30.0 rad/s, zeta=0.8
_AXIS_CFG = {
    "roll": ("second_order", 44.0, 0.8),
    "pitch": ("second_order", 44.0, 0.8),
    "yaw": ("first_order", 30.0, 0.8),
}


class ReferenceModel:
    """One axis' reference model, integrated as mrac.c does.

    SECOND_ORDER uses semi-implicit Euler (2nd order).
    FIRST_ORDER uses forward Euler (1st order).
    """

    def __init__(self, kind: str, bw: float = 0.0, zeta: float = 0.8,
                 dt: float = 1.0 / 500.0) -> None:
        if kind not in ("second_order", "first_order"):
            raise ValueError(f"unknown ref model kind: {kind!r}")
        self.kind = kind
        self.bw = bw
        self.zeta = zeta
        self.dt = dt
        self.reset()

    @classmethod
    def for_axis(cls, axis: str, dt: float = 1.0 / 500.0) -> "ReferenceModel":
        """Build the reference model for roll/pitch/yaw."""
        try:
            kind, bw, zeta = _AXIS_CFG[axis]
        except KeyError:
            raise ValueError(f"no reference-model config for axis {axis!r}")
        return cls(kind, bw=bw, zeta=zeta, dt=dt)

    def reset(self, x0: float = 0.0) -> None:
        """Bumpless snap: align the reference to the current plant state."""
        self.xm = x0
        self.xm_dot = 0.0

    def step(self, r: float, x: float = 0.0) -> float:
        """Advance one tick and return the updated desired rate xm.

        ``x`` is the measured plant rate; it is unused for open-loop
        reference models (l1=l2=0), so we accept it for API compatibility.
        """
        dt = self.dt
        if self.kind == "second_order":
            wn = self.bw
            acc = (wn * wn * (r - self.xm)
                   - 2.0 * self.zeta * wn * self.xm_dot)
            self.xm_dot += dt * acc
            self.xm += dt * self.xm_dot
        elif self.kind == "first_order":
            dx = self.bw * (r - self.xm)
            self.xm += dt * dx
            self.xm_dot = dx
        return self.xm

    def error(self, x: float) -> float:
        """Tracking error e = x - xm."""
        return x - self.xm
