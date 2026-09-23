"""Replay recorded commands through plant + PID, producing a predicted response.

Ported concept from the original project's sim replay workflow.

Usage:
  1. Create a ``Run`` with captured commands (trajectory).
  2. Instantiate ``Replay`` with plant + PID.
  3. Call ``run(captures)`` to simulate through the captured trajectory.
  4. Returns a dict of time series: timestamps, setpoints, plant responses,
     PID outputs, and the predicted response ("xm_physics" = the PID-only
     reference, the plant's predicted output without adaptation).

The adaptive-layer hook is a callable ``u_ad(t, state) -> dict[str, float]``,
default zero (no adaptive correction).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np

from .plant import IdentifiedPlant, AxisModel
from .reference_model import ReferenceModel
from .baseline import CascadedPID
from .constants import DEFAULT_DT

# Axis command key -> plant state key (mirrors plant.py internal mapping)
_RATE_KEY = {"roll": "p", "pitch": "q", "yaw": "r"}


@dataclass
class ReplayResult:
    """Time-series results from a replay simulation."""
    t: list[float] = field(default_factory=list)
    setpoint: dict[str, list[float]] = field(default_factory=dict)
    response: dict[str, list[float]] = field(default_factory=dict)
    pid_output: dict[str, list[float]] = field(default_factory=dict)
    xm_physics: dict[str, list[float]] = field(default_factory=dict)
    u_ad: dict[str, list[float]] = field(default_factory=dict)
    rate_fb: dict[str, list[float]] = field(default_factory=dict)


class Replay:
    """Replay recorded commands through plant + PID.

    Args:
        plant: the identified plant to simulate through.
        pid: the cascaded PID controller.
        ref_models: per-axis reference models (for xm_physics).
        dt: simulation timestep.
        u_ad: adaptive-layer callable ``f(t, state) -> {axis: correction}``.
              Default zero (no adaptive correction).
    """

    def __init__(self,
                 plant: IdentifiedPlant,
                 pid: CascadedPID,
                 ref_models: Optional[dict[str, ReferenceModel]] = None,
                 dt: float = DEFAULT_DT,
                 u_ad: Optional[Callable[[float, dict[str, float]],
                                         dict[str, float]]] = None) -> None:
        self.plant = plant
        self.pid = pid
        self.ref_models = ref_models or {}
        self.dt = dt
        self.u_ad = u_ad or (lambda t, state: {})

    def run(self, captures: list[dict[str, Any]],
            attitude_sp: Optional[dict[str, Callable[[float], float]]] = None,
            rate_sp: Optional[dict[str, Callable[[float], float]]] = None) -> ReplayResult:
        """Replay captured commands through plant + PID.

        Args:
            captures: list of dicts with at least ``{'t': float, ...}`` entries.
                Each entry may carry per-axis rate setpoints (rad/s) or
                attitude setpoints (deg). Keys like ``'roll_sp'``, ``'roll_att_sp'``
                are recognised.
            attitude_sp: optional per-axis callable ``(t -> deg)`` for attitude
                setpoint trajectory. If provided, the cascaded PID outer loop
                is used.
            rate_sp: optional per-axis callable ``(t -> rad/s)`` for direct
                rate setpoint trajectory. If provided, the inner PID loop is
                used directly (bypasses attitude outer loop).

        Returns:
            ReplayResult with time series for all signals.
        """
        result = ReplayResult()
        self.plant.reset()
        self.pid.reset()

        # Reset reference models
        for rm in self.ref_models.values():
            rm.reset()

        n = len(captures)
        if n == 0:
            return result

        # Initialize list collectors for all axes
        axes = ("roll", "pitch", "yaw")
        for ax in axes:
            result.response[ax] = []
            result.pid_output[ax] = []
            result.u_ad[ax] = []
            result.rate_fb[ax] = []
        for ax in self.ref_models:
            result.xm_physics[ax] = []

        for i, cap in enumerate(captures):
            t = cap.get("t", i * self.dt)
            result.t.append(t)

            # Gather setpoints from captures (separate from callable params
            # to avoid variable shadowing: the dict must not be reassigned
            # to a callable, which would make the elif rate_sp branch call
            # a float instead of iterating dict items).
            captured_att_sp: dict[str, float] = {}
            captured_rate_sp: dict[str, float] = {}
            for axis in axes:
                att_key = f"{axis}_att_sp"
                rate_key = f"{axis}_sp"
                if att_key in cap:
                    captured_att_sp[axis] = float(cap[att_key])
                if rate_key in cap:
                    captured_rate_sp[axis] = float(cap[rate_key])

            # Merge trajectory callables on top of captured setpoints
            att_sp_merged: dict[str, float] = {}
            rate_sp_merged: dict[str, float] = {}
            if attitude_sp:
                for axis, fn in attitude_sp.items():
                    att_sp_merged[axis] = fn(t)
            if rate_sp:
                for axis, fn in rate_sp.items():
                    rate_sp_merged[axis] = fn(t)

            # Compute adaptive correction
            state = {"p": 0.0, "q": 0.0, "r": 0.0}
            u_ad = self.u_ad(t, state)

            # Determine which control mode based on what setpoints are
            # available (from captures or from callable trajectories).
            if captured_att_sp or att_sp_merged:
                # Cascaded: attitude loop -> rate setpoint -> rate PID
                attitude_fb: dict[str, float] = {}
                for axis in axes:
                    attitude_fb[axis] = state.get(f"{axis}_att", 0.0)
                attitude_input = captured_att_sp or att_sp_merged
                pid_out = self.pid.step_with_rate_feedback(
                    attitude_input,
                    attitude_fb,
                    {axis: state.get(_RATE_KEY[axis], 0.0) for axis in axes}
                )
                # Apply adaptive correction to plant input
                plant_input: dict[str, float] = {}
                for axis in axes:
                    plant_input[axis] = pid_out.get(axis, 0.0) + u_ad.get(axis, 0.0)
                plant_state = self.plant.step(plant_input)
                for axis in axes:
                    rk = _RATE_KEY[axis]
                    val = plant_state.get(rk, 0.0)
                    result.response[axis].append(val)
                    result.rate_fb[axis].append(val)
                    result.pid_output[axis].append(pid_out.get(axis, 0.0))
                    result.u_ad[axis].append(u_ad.get(axis, 0.0))
            elif captured_rate_sp or rate_sp_merged:
                # Direct rate control: rate setpoint -> plant
                plant_input: dict[str, float] = {}
                for axis in axes:
                    sp = captured_rate_sp.get(axis, 0.0) + rate_sp_merged.get(axis, 0.0)
                    plant_input[axis] = sp + u_ad.get(axis, 0.0)
                plant_state = self.plant.step(plant_input)
                for axis in axes:
                    rk = _RATE_KEY[axis]
                    val = plant_state.get(rk, 0.0)
                    result.response[axis].append(val)
                    result.rate_fb[axis].append(val)
                    result.pid_output[axis].append(plant_input.get(axis, 0.0))
                    result.u_ad[axis].append(u_ad.get(axis, 0.0))
            else:
                # No setpoint -- just plant step with zero input
                plant_state = self.plant.step({})
                for axis in axes:
                    rk = _RATE_KEY[axis]
                    val = plant_state.get(rk, 0.0)
                    result.response[axis].append(val)
                    result.rate_fb[axis].append(val)
                    result.pid_output[axis].append(0.0)
                    result.u_ad[axis].append(0.0)

            # Reference model xm_physics (scalar per step)
            for axis, rm in self.ref_models.items():
                sp = captured_att_sp.get(axis, 0.0) + att_sp_merged.get(axis, 0.0)
                if not sp:
                    sp = captured_rate_sp.get(axis, 0.0) + rate_sp_merged.get(axis, 0.0)
                xm = rm.step(sp, result.response.get(axis, [0.0])[-1])
                result.xm_physics[axis].append(xm)

        return result
