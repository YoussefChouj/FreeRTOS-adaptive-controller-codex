"""Cascaded attitude->rate PID matching the original's baseline.

Ported from the original project's sim/baseline.py and sim/plant.py RatePID.

UNIT CHAIN (firmware's, reproduced exactly):
    setpoint/feedback : deg/s        (PID inputs)
    PID output  U     : mixer units  (clamped to +/-UMax)
    u_nom = U / mrac_to_mixer       : Nm

The PID is the firmware's *positional* form with its exact quirks:
  * conditional integration: integrate only when not output-saturated AND |E| < EMin
  * every term clamped independently (Up/Ui/Ud) then the sum clamped
  * derivative on error, raw first difference Kd*(E - PreE)

Cascaded structure: outer attitude loop produces a rate setpoint,
inner rate loop produces mixer-unit torque commands.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .constants import (
    MIXER_R_P, MIXER_YAW, DEG2RAD, RAD2DEG,
)


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


@dataclass
class RatePIDConfig:
    """One inner rate-loop PID config (mirrors firmware pid.c Ctrler[] row)."""
    Kp: float
    Ki: float
    Kd: float
    UMax: float
    UpMax: float
    UiMax: float
    UdMax: float
    SumEMax: float
    EMin: float
    mrac_to_mixer: float

    @classmethod
    def for_roll(cls) -> "RatePIDConfig":
        # gyrox: Kp=5.0, Ki=0.01, Kd=10.0 (from sim/baseline.py / pid.c)
        return cls(Kp=5.0, Ki=0.01, Kd=10.0, UMax=300.0, UpMax=300.0,
                   UiMax=20.0, UdMax=100.0, SumEMax=1000.0, EMin=2.0,
                   mrac_to_mixer=MIXER_R_P)

    @classmethod
    def for_pitch(cls) -> "RatePIDConfig":
        # gyroy: identical to gyrox
        return cls(Kp=5.0, Ki=0.01, Kd=10.0, UMax=300.0, UpMax=300.0,
                   UiMax=20.0, UdMax=100.0, SumEMax=1000.0, EMin=2.0,
                   mrac_to_mixer=MIXER_R_P)

    @classmethod
    def for_yaw(cls) -> "RatePIDConfig":
        # gyroz: Kp=8.0, Ki=0.001, Kd=0.02
        return cls(Kp=8.0, Ki=0.001, Kd=0.02, UMax=250.0, UpMax=250.0,
                   UiMax=60.0, UdMax=10.0, SumEMax=2000.0, EMin=20.0,
                   mrac_to_mixer=MIXER_YAW)


class RatePID:
    """Firmware ComputePID (positional form, deg/s in -> mixer U out)."""

    def __init__(self, config: RatePIDConfig) -> None:
        self.cfg = config
        self.reset()

    def reset(self) -> None:
        self.SumE = 0.0
        self.PreE = 0.0
        self.U = 0.0
        self.Up = 0.0
        self.Ui = 0.0
        self.Ud = 0.0

    def step(self, des: float, fb: float) -> float:
        """One tick: setpoint/feedback in deg/s -> clamped mixer output U."""
        c = self.cfg
        E = des - fb
        # conditional integration (anti-windup + integrate-near-setpoint band)
        if (((self.U <= c.UMax and E > 0.0) or (self.U >= -c.UMax and E < 0.0))
                and abs(E) < c.EMin):
            self.SumE += E
        self.SumE = _clamp(self.SumE, -c.SumEMax, c.SumEMax)
        self.Ui = _clamp(c.Ki * self.SumE, -c.UiMax, c.UiMax)
        self.Up = _clamp(c.Kp * E, -c.UpMax, c.UpMax)
        self.Ud = _clamp(c.Kd * (E - self.PreE), -c.UdMax, c.UdMax)
        self.U = _clamp(self.Up + self.Ui + self.Ud, -c.UMax, c.UMax)
        self.PreE = E
        return self.U

    def u_nom(self) -> float:
        """Latest output mapped to Nm: u_nom = U / mrac_to_mixer."""
        return self.U / self.cfg.mrac_to_mixer


@dataclass
class AttitudePIDConfig:
    """Outer attitude-loop PID gains (deg error -> deg/s rate setpoint)."""
    Kp: float
    Ki: float
    Kd: float
    UMax: float = 90.0  # rate setpoint limit in deg/s


# Default attitude PID gains — tuned for the identified plants.
# These produce a well-damped attitude response that the rate loop tracks.
ATTITUDE_GAINS = {
    "roll": AttitudePIDConfig(Kp=3.0, Ki=0.0, Kd=1.5),
    "pitch": AttitudePIDConfig(Kp=3.0, Ki=0.0, Kd=1.5),
    "yaw": AttitudePIDConfig(Kp=4.0, Ki=0.0, Kd=1.0),
}


class AttitudePID:
    """Outer attitude loop: attitude error (deg) -> rate setpoint (deg/s)."""

    def __init__(self, config: AttitudePIDConfig) -> None:
        self.cfg = config
        self.Integral = 0.0
        self.PreE = 0.0

    def reset(self) -> None:
        self.Integral = 0.0
        self.PreE = 0.0

    def step(self, des: float, fb: float) -> float:
        """One tick: attitude setpoint/feedback in deg -> rate setpoint in deg/s."""
        E = des - fb
        self.Integral += E
        # anti-windup clamp
        self.Integral = _clamp(self.Integral, -self.cfg.UMax * 10.0,
                               self.cfg.UMax * 10.0)
        rate_sp = (self.cfg.Kp * E
                   + self.cfg.Ki * self.Integral
                   + self.cfg.Kd * (E - self.PreE))
        self.PreE = E
        return _clamp(rate_sp, -self.cfg.UMax, self.cfg.UMax)


class CascadedPID:
    """Cascaded attitude->rate PID controller.

    Structure:
      outer: AttitudePID (deg error -> deg/s rate setpoint)
      inner: RatePID for each axis (deg/s error -> mixer unit torque)
    """

    def __init__(self, dt: float = 1.0 / 500.0) -> None:
        self.dt = dt
        self._inner: dict[str, RatePID] = {}
        self._outer: dict[str, AttitudePID] = {}
        self._initialized = False

    def _ensure(self, axis: str) -> None:
        if self._initialized:
            return
        if axis == "yaw":
            self._inner[axis] = RatePID(RatePIDConfig.for_yaw())
            self._outer[axis] = AttitudePID(ATTITUDE_GAINS["yaw"])
        elif axis in ("roll", "pitch"):
            self._inner[axis] = RatePID(RatePIDConfig.for_roll())
            self._outer[axis] = AttitudePID(ATTITUDE_GAINS[axis])

    def reset(self) -> None:
        for pid in self._inner.values():
            pid.reset()
        for pid in self._outer.values():
            pid.reset()

    def step(self, attitude_sp: dict[str, float],
             attitude_fb: dict[str, float],
             rate_fb: Optional[dict[str, float]] = None) -> dict[str, float]:
        """One control tick.

        Args:
            attitude_sp: attitude setpoints in deg for {'roll', 'pitch', 'yaw'}.
            attitude_fb: attitude feedback in deg for {'roll', 'pitch', 'yaw'}.
            rate_fb: optional rate feedback in rad/s for the inner rate loop.
                If None, rate feedback defaults to 0.0 (open-loop).

        Returns:
            dict with per-axis mixer-unit outputs {'roll': U, 'pitch': U, 'yaw': U}.
        """
        result: dict[str, float] = {}
        for axis in ("roll", "pitch", "yaw"):
            self._ensure(axis)
            # outer loop: attitude error -> rate setpoint (deg/s)
            att_sp_deg = attitude_sp.get(axis, 0.0)
            att_fb_deg = attitude_fb.get(axis, 0.0)
            rate_sp_deg = self._outer[axis].step(att_sp_deg, att_fb_deg)
            # inner loop: rate PID
            if rate_fb is not None:
                rate_fb_deg = rate_fb.get(axis, 0.0) * RAD2DEG  # rad/s -> deg/s
                U = self._inner[axis].step(rate_sp_deg, rate_fb_deg)
            else:
                U = self._inner[axis].step(rate_sp_deg, 0.0)
            result[axis] = U
        return result

    def step_with_rate_feedback(self, attitude_sp: dict[str, float],
                                attitude_fb: dict[str, float],
                                rate_fb: dict[str, float]) -> dict[str, float]:
        """One control tick with explicit rate feedback.

        The outer loop produces a rate setpoint from attitude error,
        the inner loop uses that rate setpoint and the measured rate
        feedback to produce mixer-unit torque commands.

        Returns dict with per-axis mixer-unit outputs.
        """
        result: dict[str, float] = {}
        for axis in ("roll", "pitch", "yaw"):
            self._ensure(axis)
            att_sp_deg = attitude_sp.get(axis, 0.0)
            att_fb_deg = attitude_fb.get(axis, 0.0)
            rate_sp_deg = self._outer[axis].step(att_sp_deg, att_fb_deg)
            rate_fb_deg = rate_fb.get(axis, 0.0) * RAD2DEG  # plant returns rad/s
            U = self._inner[axis].step(rate_sp_deg, rate_fb_deg)
            result[axis] = U
        return result
