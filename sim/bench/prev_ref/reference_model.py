"""Reference model — firmware-parity runtime path (API/mrac.c:168-196).

The reference model maps a rate command r to the desired rate response xm the
plant is asked to track; the adaptive law drives e = x - xm to zero. Two distinct
things share the name "reference model" in this project — keep them apart:

  * compute_reference_model.py  — design-time CALCULATOR. Gives the continuous
    Am/Bm and the matrix Lyapunov P (scipy). Use it for analysis and to pick bw/zeta.
  * this module                 — in-loop RUNTIME. Mirrors what mrac.c actually
    executes each tick: semi-implicit Euler (2nd order) / forward Euler (1st), and
    the *scalar heuristic* gain P = 1/(2*wn) the firmware really uses for the
    adaptive law, NOT the matrix P (ADR-0003). Parity (scenario 1) requires this.

step(r) advances xm THEN returns it (firmware updates xm before forming e), the
opposite of plant.step which returns y before advancing. reset(x0) is the bumpless
snap xm=x0, xm_dot=0 (mrac.c:417-421).
"""
from __future__ import annotations

from enum import IntEnum
from typing import Optional


class RefType(IntEnum):
    """Matches the firmware CMD 0x13 ref_model_type enum (mrac.c:168)."""
    PASSTHROUGH = 0
    FIRST_ORDER = 1
    SECOND_ORDER = 2


# per-axis firmware configs (mrac.c:324-360)
_AXIS_CFG = {
    "roll": (RefType.SECOND_ORDER, 44.0, 0.8),
    "pitch": (RefType.SECOND_ORDER, 44.0, 0.8),
    "yaw": (RefType.FIRST_ORDER, 30.0, 0.8),
}


class ReferenceModel:
    """One axis' reference model, integrated exactly as mrac.c does."""

    def __init__(self, kind: RefType, bw: float = 0.0, zeta: float = 0.8,
                 dt: float = 0.005, q1: float = 1.0, q2: float = 1.0,
                 l1: float = 0.0, l2: float = 0.0):
        self.kind = RefType(kind)
        self.bw = bw
        self.zeta = zeta
        self.dt = dt
        self.q1 = q1                    # Lyapunov Q diagonal (rate-error weight)
        self.q2 = q2                    # Lyapunov Q diagonal (rate-derivative weight)
        # CRM (closed-loop reference model) feedback gain L = [l1; l2] on the measured
        # output error (x - xm). l1=l2=0 -> open-loop reference model (ADR-0007 exactly).
        self.l1 = l1
        self.l2 = l2
        # scalar Lyapunov gain doubling as the adaptive-law gain (mrac.c:175/183/191).
        # Kept for the scalar law (1st-order / passthrough) AND for telemetry/comparison.
        if self.kind is RefType.SECOND_ORDER:
            self.P = 1.0 / (2.0 * bw)
        elif self.kind is RefType.FIRST_ORDER:
            self.P = 1.0 / (2.0 * bw)
        else:
            self.P = 1.0
        # 2nd-order STATE-SPACE law (ADR-0007 + CRM): the adaptive drive is
        #   s = e_v^T P B = e*Pe + e_dot*Pedot,   B = [0;1],
        # so only the 2nd column of the Lyapunov matrix P matters. P solves the
        # Lyapunov equation for the (CRM) error-dynamics matrix
        #   A = Am - L*C = [[-l1, 1], [-(wn^2+l2), -2*zeta*wn]],   C = [1, 0],
        # with diagonal Q = diag(q1,q2). Solving A^T P + P A = -Q for the two needed
        # entries (closed form, no matrix library — keeps the firmware live-compute
        # property of ADR-0007):
        #   c = 2*zeta*wn,  k = wn^2 + l2,  alpha = l1^2 + l1*c + k
        #   Pedot (=p22) = (q1 + alpha*q2) / (2*(alpha*c + l1*k))
        #   Pe    (=p12) = c*Pedot - q2/2
        # l1=l2=0 collapses to the ADR-0007 forms Pe=q1/(2*wn^2),
        # Pedot=(q1/wn^2 + q2)/(4*zeta*wn). Note q1=wn then makes Pe=1/(2*wn) — the
        # old scalar e-channel gain.
        if self.kind is RefType.SECOND_ORDER:
            c = 2.0 * zeta * bw
            k = bw * bw + l2
            alpha = l1 * l1 + l1 * c + k
            self.Pedot = (q1 + alpha * q2) / (2.0 * (alpha * c + l1 * k))
            self.Pe = c * self.Pedot - q2 / 2.0
        else:
            self.Pe = 0.0
            self.Pedot = 0.0
        self.reset()

    @classmethod
    def for_axis(cls, axis: str, dt: float = 0.005, q1: float = 1.0,
                 q2: float = 1.0,
                 ref_model_type: Optional[int] = None,
                 l1: float = 0.0, l2: float = 0.0) -> "ReferenceModel":
        """Build the reference model for roll/pitch/yaw.

        ``ref_model_type`` mirrors the firmware ``mrac_flags.ref_model_type`` runtime
        switch (CMD 0x13): pass it to override the axis' firmware-configured order and
        run any of PASSTHROUGH(0)/FIRST_ORDER(1)/SECOND_ORDER(2) on the same axis
        (keeping that axis' bw/zeta). ``None`` keeps the per-axis firmware default
        (roll/pitch=2nd, yaw=1st), which is what the closed-loop validation exercises.
        To reproduce the as-flown power-on default, pass ``RefType.PASSTHROUGH``
        (firmware ``DEFAULT_REF_MODEL_TYPE = 0``).
        """
        try:
            kind, bw, zeta = _AXIS_CFG[axis]
        except KeyError:
            raise ValueError(f"no reference-model config for axis {axis!r}")
        if ref_model_type is not None:
            kind = RefType(ref_model_type)
        return cls(kind, bw=bw, zeta=zeta, dt=dt, q1=q1, q2=q2, l1=l1, l2=l2)

    def reset(self, x0: float = 0.0) -> None:
        """Bumpless snap: align the reference to the current plant state."""
        self.xm = x0
        self.xm_dot = 0.0

    def step(self, r: float, x: float = 0.0) -> float:
        """Advance one MRAC tick and return the updated desired rate xm.

        ``x`` is the measured plant rate; it is used only by the 2nd-order CRM
        feedback ``L*(x - xm)`` (no-op when ``l1 == l2 == 0``, i.e. the open-loop
        reference model). 1st-order / passthrough ignore it.
        """
        dt = self.dt
        if self.kind is RefType.SECOND_ORDER:
            wn = self.bw
            e_out = x - self.xm            # measured output error feeding the CRM
            acc = (wn * wn * (r - self.xm) - 2.0 * self.zeta * wn * self.xm_dot
                   + self.l2 * e_out)
            self.xm_dot += dt * acc
            self.xm += dt * (self.xm_dot + self.l1 * e_out)
        elif self.kind is RefType.FIRST_ORDER:
            dx = self.bw * (r - self.xm)
            self.xm += dt * dx
            self.xm_dot = dx
        else:  # PASSTHROUGH
            self.xm = r
            self.xm_dot = 0.0
        return self.xm

    def error(self, x: float) -> float:
        """Tracking error e = x - xm (mrac.c:196)."""
        return x - self.xm
