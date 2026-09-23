"""Discrete rate-plant per axis: G(s) = K/(s*(1+s/p))*e^(-sT) for roll/pitch,
G(s) = K/s for yaw (pure integrator).

Ported from the original project's sim/plant.py IdentifiedPlant / _AxisSim.
The plant is discrete at a configurable rate (default 500 Hz).

Plant boundary: ``step(u_dict) -> state_dict``, ``reset()``.
Command units are the firmware u (u_nom + u_ad), not SI Nm: the identified K
folds in torque effectiveness and 1/J, so feeding the same command the
firmware computes reproduces the same rate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.signal import cont2discrete

from .constants import (
    ROLL_K, ROLL_POLE, ROLL_DELAY,
    PITCH_K, PITCH_POLE, PITCH_DELAY,
    YAW_K,
    DEFAULT_DT,
)


@dataclass(frozen=True)
class AxisModel:
    """Identified per-axis rate plant ``K/(s*(1+s/p))*e^(-s*T)``.

    ``pole=None`` selects the pure-integrator ``K/s`` realisation (yaw).
    ``delay`` is the transport delay T in seconds (0 = no delay).
    """
    K: float
    pole: Optional[float] = None
    delay: float = 0.0


# Per-axis models keyed by axis name.
_AXIS_MODELS: dict[str, AxisModel] = {
    "roll": AxisModel(K=ROLL_K, pole=ROLL_POLE, delay=ROLL_DELAY),
    "pitch": AxisModel(K=PITCH_K, pole=PITCH_POLE, delay=PITCH_DELAY),
    "yaw": AxisModel(K=YAW_K, pole=None, delay=0.0),
}

_RATE_KEY = {"roll": "p", "pitch": "q", "yaw": "r"}


class _AxisSim:
    """Single-axis discrete state-space + integer transport-delay buffer."""

    def __init__(self, model: AxisModel, dt: float) -> None:
        self.dt = dt
        if model.pole is None:
            # K/s : ZOH discretisation of integrator: Ad=1, Bd=dt, C=K
            A = np.array([[0.0]])
            B = np.array([[1.0]])
            C = np.array([[model.K]])
        else:
            # K/(s*(1+s/p)) = K*p / (s^2 + p*s); controllable canonical form
            Kp = model.K * model.pole
            A = np.array([[0.0, 1.0], [0.0, -model.pole]])
            B = np.array([[0.0], [1.0]])
            C = np.array([[Kp, 0.0]])
        D = np.zeros((1, 1))
        Ad, Bd, Cd, _, _ = cont2discrete((A, B, C, D), dt, method="zoh")
        self.Ad = Ad
        self.Bd = Bd
        self.Cd = Cd
        # N = round(T/dt) integer-sample transport delay
        self.N = int(round(model.delay / dt)) if model.delay > 0 else 0
        self._delay_buf: list[float] = [0.0] * max(self.N, 1)
        self._delay_pos = 0
        self.reset()

    def reset(self) -> None:
        self.x = np.zeros((self.Ad.shape[0], 1))
        self._delay_buf = [0.0] * max(self.N, 1)
        self._delay_pos = 0

    def step(self, u: float) -> float:
        # output reflects current state (y = C x), then state advances
        y = float((self.Cd @ self.x).item())
        # apply transport delay
        if self.N > 0:
            self._delay_buf[self._delay_pos] = u
            u_eff = self._delay_buf[self._delay_pos]
            self._delay_pos = (self._delay_pos + 1) % self.N
            u_eff = self._delay_buf[self._delay_pos]
        else:
            u_eff = u
        self.x = self.Ad @ self.x + self.Bd * u_eff
        return y


class IdentifiedPlant:
    """Per-axis identified linear rate plants.

    ``step(u_dict) -> state_dict`` where u_dict carries per-axis command keys
    ``{'roll', 'pitch', 'yaw'}`` (any subset, default 0).
    Returns state dict with keys ``{'p', 'q', 'r'}`` (body angular rates rad/s).
    """

    def __init__(self, dt: float = DEFAULT_DT,
                 axes: Optional[dict[str, AxisModel]] = None) -> None:
        unknown = set(axes or _AXIS_MODELS) - _RATE_KEY.keys()
        if unknown:
            raise ValueError(f"unknown axes: {sorted(unknown)}")
        self.dt = dt
        self._axes = axes or _AXIS_MODELS
        self._sims = {ax: _AxisSim(m, dt) for ax, m in self._axes.items()}

    def step(self, u: dict[str, float]) -> dict[str, float]:
        return {
            _RATE_KEY[ax]: sim.step(float(u.get(ax, 0.0)))
            for ax, sim in self._sims.items()
        }

    def reset(self) -> None:
        for sim in self._sims.values():
            sim.reset()

    @property
    def dt(self) -> float:
        return self._dt

    @dt.setter
    def dt(self, val: float) -> None:
        self._dt = val
