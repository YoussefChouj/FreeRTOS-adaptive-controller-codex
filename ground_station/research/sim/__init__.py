"""Sim/replay harness for Phase-0 validation and workflow dry runs.

Packages:
  - ``constants`` — canonical physical constants from the original project
  - ``plant`` — discrete identified rate-plant per axis
  - ``reference_model`` — per-axis reference models (xm)
  - ``baseline`` — cascaded attitude->rate PID (firmware parity)
  - ``replay`` — replay captured commands through plant + PID
  - ``dryrun`` — dry_run(workflow, params) -> Run
"""
from __future__ import annotations

from .constants import (
    ROLL_K, ROLL_POLE, ROLL_DELAY, ROLL_REF_BW, ROLL_REF_ZETA,
    PITCH_K, PITCH_POLE, PITCH_DELAY, PITCH_REF_BW, PITCH_REF_ZETA,
    YAW_K, YAW_REF_BW, YAW_REF_ZETA,
    GRAVITY, AIRFRAME_MASS, Ixx, Iyy, Izz, R_MOTOR,
    MOTOR_TAU, MIXER_R_P, MIXER_YAW, MIXER_Z,
    DEFAULT_DT,
)
from .plant import IdentifiedPlant, AxisModel
from .reference_model import ReferenceModel
from .baseline import CascadedPID, RatePID, RatePIDConfig, AttitudePID, AttitudePIDConfig
from .replay import Replay, ReplayResult
from .dryrun import dry_run

__all__ = [
    "constants",
    "IdentifiedPlant", "AxisModel",
    "ReferenceModel",
    "CascadedPID", "RatePID", "RatePIDConfig", "AttitudePID", "AttitudePIDConfig",
    "Replay", "ReplayResult",
    "dry_run",
    # constants
    "ROLL_K", "ROLL_POLE", "ROLL_DELAY", "ROLL_REF_BW", "ROLL_REF_ZETA",
    "PITCH_K", "PITCH_POLE", "PITCH_DELAY", "PITCH_REF_BW", "PITCH_REF_ZETA",
    "YAW_K", "YAW_REF_BW", "YAW_REF_ZETA",
    "GRAVITY", "AIRFRAME_MASS", "Ixx", "Iyy", "Izz", "R_MOTOR",
    "MOTOR_TAU", "MIXER_R_P", "MIXER_YAW", "MIXER_Z",
    "DEFAULT_DT",
]
