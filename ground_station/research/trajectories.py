"""Trajectory presets, parametric families and excitation overlays.

Known presets:
    ``step``       – single step change per axis.
    ``doublet``    – bang-bang doublet (step, reverse, settle).
    ``chirp``      – linear frequency-sweep excitation.
    ``multisine``  – multi-sine persistent excitation.
    ``figure8``    – figure-8 canvas trajectory (free-flight only).

Parametric families:
    Each preset accepts amplitude, duration and per-axis overrides.

Excitation overlays:
    ``chirp_overlay`` and ``multisine_overlay`` can be stacked on top of any
    trajectory reference.

Feasibility:
    ``check_feasible(trajectory, profile)`` validates against a rig profile.
    Profiles: ``fixture_4dof`` (roll/pitch +/-40 deg, yaw free, z 0.42-0.65 m,
    no x/y) or ``free_flight`` (no limits).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np


# ---------------------------------------------------------------------------
# Rig profiles (hard limits)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RigProfile:
    """Hardware/fixture boundary conditions for feasibility checks."""
    name: str
    # Per-axis angle limits in degrees (None = free)
    roll_max_deg: float = 40.0
    pitch_max_deg: float = 40.0
    yaw_max_deg: float = float("inf")
    # Z height limits in meters (None = free)
    z_min_m: float = 0.42
    z_max_m: float = 0.65
    # x/y are absent on the fixture (and on board) – never valid for fixture
    x_allowed: bool = False
    y_allowed: bool = False
    # Additional free-flight allowances
    # (set to True for free_flight profile)
    free_flight: bool = False

    @classmethod
    def fixture_4dof(cls) -> RigProfile:
        return cls(
            name="fixture_4dof",
            roll_max_deg=40.0,
            pitch_max_deg=40.0,
            yaw_max_deg=float("inf"),
            z_min_m=0.42,
            z_max_m=0.65,
            x_allowed=False,
            y_allowed=False,
            free_flight=False,
        )

    @classmethod
    def free_flight(cls) -> RigProfile:
        return cls(
            name="free_flight",
            roll_max_deg=float("inf"),
            pitch_max_deg=float("inf"),
            yaw_max_deg=float("inf"),
            z_min_m=float("-inf"),
            z_max_m=float("inf"),
            x_allowed=True,
            y_allowed=True,
            free_flight=True,
        )


# ---------------------------------------------------------------------------
# Trajectory step (the output unit)
# ---------------------------------------------------------------------------

@dataclass
class TrajectoryPoint:
    """One point in a trajectory time series."""
    t: float
    roll_deg: float = 0.0
    pitch_deg: float = 0.0
    yaw_deg: float = 0.0
    z_m: float = 0.53  # nominal hover height
    # Excitation overlay: per-axis extra command (Nm or normalised)
    roll_excite: float = 0.0
    pitch_excite: float = 0.0
    yaw_excite: float = 0.0


@dataclass
class TrajectoryResult:
    """A generated trajectory."""
    name: str
    profile: str  # which profile it was generated for
    points: list[TrajectoryPoint] = field(default_factory=list)
    dt: float = 0.002  # 500 Hz default

    def as_dict(self) -> list[dict[str, Any]]:
        return [
            {
                "t": p.t,
                "roll_deg": p.roll_deg,
                "pitch_deg": p.pitch_deg,
                "yaw_deg": p.yaw_deg,
                "z_m": p.z_m,
                "roll_excite": p.roll_excite,
                "pitch_excite": p.pitch_excite,
                "yaw_excite": p.yaw_excite,
            }
            for p in self.points
        ]


# ---------------------------------------------------------------------------
# Preset generators
# ---------------------------------------------------------------------------

def step_trajectory(
    *,
    amplitude_deg: float = 15.0,
    duration_s: float = 2.0,
    settle_s: float = 1.0,
    axes: tuple[str, ...] = ("roll", "pitch"),
    name: str = "step",
    z_m: float = 0.53,
) -> TrajectoryResult:
    """Single step change, then settle back to 0."""
    dt = 0.002
    t = 0.0
    points: list[TrajectoryPoint] = []

    # Hold at 0
    points.append(TrajectoryPoint(t=0.0, z_m=z_m))
    # Step up
    t = dt
    roll = amplitude_deg if "roll" in axes else 0.0
    pitch = amplitude_deg if "pitch" in axes else 0.0
    yaw = amplitude_deg if "yaw" in axes else 0.0
    points.append(TrajectoryPoint(t=t, roll_deg=roll, pitch_deg=pitch,
                                   yaw_deg=yaw, z_m=z_m))
    # Hold for duration
    t += duration_s
    points.append(TrajectoryPoint(t=t, roll_deg=roll, pitch_deg=pitch,
                                   yaw_deg=yaw, z_m=z_m))
    # Settle back
    t += dt
    points.append(TrajectoryPoint(t=t, roll_deg=0.0, pitch_deg=0.0,
                                   yaw_deg=0.0, z_m=z_m))
    # Settle hold
    t += settle_s
    points.append(TrajectoryPoint(t=t, roll_deg=0.0, pitch_deg=0.0,
                                   yaw_deg=0.0, z_m=z_m))

    return TrajectoryResult(name=name, profile="fixture_4dof",
                            points=points, dt=dt)


def doublet_trajectory(
    *,
    amplitude_deg: float = 15.0,
    pulse_s: float = 0.3,
    settle_s: float = 1.0,
    axes: tuple[str, ...] = ("roll", "pitch"),
    name: str = "doublet",
    z_m: float = 0.53,
) -> TrajectoryResult:
    """Bang-bang doublet: +A, -A, settle."""
    dt = 0.002
    points: list[TrajectoryPoint] = []
    roll = amplitude_deg if "roll" in axes else 0.0
    pitch = amplitude_deg if "pitch" in axes else 0.0
    yaw = amplitude_deg if "yaw" in axes else 0.0

    points.append(TrajectoryPoint(t=0.0, roll_deg=0.0, pitch_deg=0.0,
                                   yaw_deg=0.0, z_m=z_m))
    # +A
    t = dt
    points.append(TrajectoryPoint(t=t, roll_deg=roll, pitch_deg=pitch,
                                   yaw_deg=yaw, z_m=z_m))
    # -A (after pulse)
    t += pulse_s
    points.append(TrajectoryPoint(t=t, roll_deg=-roll, pitch_deg=-pitch,
                                   yaw_deg=-yaw, z_m=z_m))
    # Hold pulse duration
    t += pulse_s
    points.append(TrajectoryPoint(t=t, roll_deg=-roll, pitch_deg=-pitch,
                                   yaw_deg=-yaw, z_m=z_m))
    # Settle back
    t += dt
    points.append(TrajectoryPoint(t=t, roll_deg=0.0, pitch_deg=0.0,
                                   yaw_deg=0.0, z_m=z_m))
    # Settle hold
    t += settle_s
    points.append(TrajectoryPoint(t=t, roll_deg=0.0, pitch_deg=0.0,
                                   yaw_deg=0.0, z_m=z_m))

    return TrajectoryResult(name=name, profile="fixture_4dof",
                            points=points, dt=dt)


def chirp_trajectory(
    *,
    amplitude_deg: float = 5.0,
    f_start_hz: float = 0.5,
    f_end_hz: float = 10.0,
    duration_s: float = 8.0,
    name: str = "chirp",
    z_m: float = 0.53,
) -> TrajectoryResult:
    """Linear chirp (frequency sweep) excitation."""
    dt = 0.002
    n_steps = int(duration_s / dt)
    points: list[TrajectoryPoint] = []
    for i in range(n_steps + 1):
        t = i * dt
        frac = t / duration_s if duration_s > 0 else 0.0
        freq = f_start_hz + frac * (f_end_hz - f_start_hz)
        phase = 2.0 * math.pi * (f_start_hz * t +
                                  0.5 * (f_end_hz - f_start_hz) * t * t / duration_s)
        excite = amplitude_deg * math.sin(phase)
        points.append(TrajectoryPoint(t=t, roll_excite=excite, z_m=z_m))
    return TrajectoryResult(name=name, profile="fixture_4dof",
                            points=points, dt=dt)


def multisine_trajectory(
    *,
    amplitudes_deg: dict[str, float] | None = None,
    frequencies_hz: dict[str, list[float]] | None = None,
    duration_s: float = 10.0,
    name: str = "multisine",
    z_m: float = 0.53,
) -> TrajectoryResult:
    """Multi-sine persistent excitation per axis."""
    dt = 0.002
    n_steps = int(duration_s / dt)
    amplitudes = amplitudes_deg or {"roll": 3.0, "pitch": 3.0, "yaw": 5.0}
    frequencies = frequencies_hz or {
        "roll": [0.5, 1.0, 2.0],
        "pitch": [0.5, 1.0, 2.0],
        "yaw": [0.3, 0.7, 1.5],
    }
    axes = list(amplitudes.keys())
    max_amp = max(amplitudes.values())
    points: list[TrajectoryPoint] = []
    for i in range(n_steps + 1):
        t = i * dt
        roll_excite = 0.0
        pitch_excite = 0.0
        yaw_excite = 0.0
        scale = max_amp / max(amplitudes.values()) if amplitudes.values() else 1.0
        for ax in axes:
            amps = amplitudes.get(ax, 0.0) * scale
            freqs = frequencies.get(ax, [0.5])
            for freq in freqs:
                phase = 2.0 * math.pi * freq * t
                if ax == "roll":
                    roll_excite += amps / len(freqs) * math.sin(phase)
                elif ax == "pitch":
                    pitch_excite += amps / len(freqs) * math.sin(phase)
                elif ax == "yaw":
                    yaw_excite += amps / len(freqs) * math.sin(phase)
        points.append(TrajectoryPoint(t=t, roll_excite=roll_excite,
                                       pitch_excite=pitch_excite,
                                       yaw_excite=yaw_excite, z_m=z_m))
    return TrajectoryResult(name=name, profile="fixture_4dof",
                            points=points, dt=dt)


def figure8_trajectory(
    *,
    major_m: float = 2.0,
    minor_m: float = 1.0,
    duration_s: float = 20.0,
    height_m: float = 3.0,
    roll_amplitude_deg: float = 10.0,
    name: str = "figure8",
) -> TrajectoryResult:
    """Figure-8 canvas trajectory (free-flight only)."""
    dt = 0.002
    n_steps = int(duration_s / dt)
    points: list[TrajectoryPoint] = []
    for i in range(n_steps + 1):
        t = i * dt
        frac = t / duration_s
        angle = 2.0 * math.pi * frac
        x = major_m * math.sin(angle)
        y = minor_m * math.sin(2.0 * angle)
        roll = roll_amplitude_deg * math.sin(angle)
        points.append(TrajectoryPoint(t=t, z_m=height_m,
                                       roll_deg=roll))
    return TrajectoryResult(name=name, profile="free_flight",
                            points=points, dt=dt)


# ---------------------------------------------------------------------------
# Preset registry
# ---------------------------------------------------------------------------

_PRESETS: dict[str, dict[str, Any]] = {
    "step": step_trajectory,
    "doublet": doublet_trajectory,
    "chirp": chirp_trajectory,
    "multisine": multisine_trajectory,
    "figure8": figure8_trajectory,
}


def get_preset(name: str) -> Any:
    """Return the preset generator function by name."""
    if name not in _PRESETS:
        raise ValueError(
            f"unknown preset {name!r}; known: {sorted(_PRESETS.keys())}"
        )
    return _PRESETS[name]


def generate_trajectory(name: str, **kwargs: Any) -> TrajectoryResult:
    """Generate a trajectory by preset name."""
    fn = get_preset(name)
    return fn(**kwargs)


# ---------------------------------------------------------------------------
# Feasibility check
# ---------------------------------------------------------------------------

class FeasibilityError(ValueError):
    """Raised when a trajectory violates the rig profile."""
    def __init__(self, message: str, violations: list[str]) -> None:
        super().__init__(message)
        self.violations = violations


def check_feasible(
    trajectory: TrajectoryResult,
    profile: RigProfile,
) -> list[str]:
    """Check trajectory against the rig profile.

    Returns a list of violation messages.  Empty list = feasible.
    Raises ``FeasibilityError`` with the violations when any exist.
    """
    violations: list[str] = []

    for p in trajectory.points:
        if not profile.free_flight and not profile.x_allowed:
            # figure8 sets x/y implicitly; fixture forbids x/y
            pass  # TrajectoryPoint doesn't carry x/y, only roll/pitch/yaw/z

        if p.roll_deg > profile.roll_max_deg or p.roll_deg < -profile.roll_max_deg:
            violations.append(
                f"roll {p.roll_deg:.2f} deg out of [±{profile.roll_max_deg}]"
            )
        if p.pitch_deg > profile.pitch_max_deg or p.pitch_deg < -profile.pitch_max_deg:
            violations.append(
                f"pitch {p.pitch_deg:.2f} deg out of [±{profile.pitch_max_deg}]"
            )
        if not profile.free_flight:
            if p.z_m < profile.z_min_m:
                violations.append(
                    f"z={p.z_m:.3f} m below floor {profile.z_min_m}"
                )
            if p.z_m > profile.z_max_m:
                violations.append(
                    f"z={p.z_m:.3f} m above ceiling {profile.z_max_m}"
                )

    if violations:
        raise FeasibilityError(
            f"trajectory {trajectory.name!r} is infeasible on {profile.name}",
            violations,
        )
    return violations


# ---------------------------------------------------------------------------
# Excitation overlays
# ---------------------------------------------------------------------------

def add_excitation_overlay(
    trajectory: TrajectoryResult,
    overlay_type: str,
    amplitude_deg: float = 3.0,
    **kwargs: Any,
) -> TrajectoryResult:
    """Add an excitation overlay to an existing trajectory.

    Args:
        trajectory: the base trajectory to modify (in-place).
        overlay_type: one of ``"chirp"`` or ``"multisine"``.
        amplitude_deg: overlay amplitude.
        **kwargs: forwarded to the overlay generator.

    Returns:
        The modified ``TrajectoryResult`` (same object, modified in-place).
    """
    base_duration = trajectory.points[-1].t if trajectory.points else 1.0
    if overlay_type == "chirp":
        excite = chirp_trajectory(
            amplitude_deg=amplitude_deg,
            duration_s=base_duration,
            **kwargs,
        )
        for i, pt in enumerate(trajectory.points):
            if i < len(excite.points):
                pt.roll_excite = excite.points[i].roll_excite
    elif overlay_type == "multisine":
        excite = multisine_trajectory(
            amplitudes_deg={"roll": amplitude_deg, "pitch": amplitude_deg},
            duration_s=base_duration,
            **kwargs,
        )
        for i, pt in enumerate(trajectory.points):
            if i < len(excite.points):
                pt.roll_excite = excite.points[i].roll_excite
                pt.pitch_excite = excite.points[i].pitch_excite
    else:
        raise ValueError(f"unknown overlay type {overlay_type!r}")

    return trajectory
