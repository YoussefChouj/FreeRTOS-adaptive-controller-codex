"""Canonical physical constants for the sim/replay harness.

All values are ported from the ORIGINAL project (read-only source of truth).
Every number carries a comment citing source file:line context.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Per-axis identified rate plants
# Source: sim/plant.py CANONICAL_MODELS (sim/plant.py lines ~80-84)
#   docs/sysid_results.md (Current best estimates table, lines ~15-25)
# Model structure: G(s) = K / (s*(1 + s/p)) * e^(-s*T)
#   yaw is a pure integrator: G(s) = K / s
# ---------------------------------------------------------------------------

# Roll: K=165, pole=19.8 rad/s, delay=0.015 s
# Source: sim/plant.py CANONICAL_MODELS["roll"], docs/sysid_results.md roll row
ROLL_K = 165.0         # rad/s per Nm (lumped input->output gain)
ROLL_POLE = 19.8       # rad/s (first-order lag pole)
ROLL_DELAY = 0.015     # seconds (transport delay)
ROLL_REF_BW = 44.0     # rad/s (closed-loop ref model bandwidth)
ROLL_REF_ZETA = 0.8    # damping ratio for ref model

# Pitch: K=185, pole=16.3 rad/s, delay=0.012 s
# Source: sim/plant.py CANONICAL_MODELS["pitch"], docs/sysid_results.md pitch row
PITCH_K = 185.0        # rad/s per Nm
PITCH_POLE = 16.3      # rad/s
PITCH_DELAY = 0.012    # seconds
PITCH_REF_BW = 44.0    # rad/s
PITCH_REF_ZETA = 0.8   # damping ratio

# Yaw: pure integrator K=37, no pole, no delay
# Source: sim/plant.py CANONICAL_MODELS["yaw"], docs/sysid_results.md yaw row
YAW_K = 37.0           # rad/s per Nm (pure integrator, rel-degree 1)
YAW_REF_BW = 30.0      # rad/s (first-order ref model bandwidth)
YAW_REF_ZETA = 0.8     # damping ratio

# ---------------------------------------------------------------------------
# Airframe / physical constants
# Source: sim/plant.py CANONICAL_AIRFRAME (sim/plant.py ~102-106)
#   sim/plant.py GRAVITY (sim/plant.py ~97)
# ---------------------------------------------------------------------------

GRAVITY = 9.80665      # m/s^2 (NED positive-down convention)
AIRFRAME_MASS = 1.2961 # kg (total airframe mass with 485 g battery)
Ixx = 0.00839          # kg m^2 (roll inertia)
Iyy = 0.00930          # kg m^2 (pitch inertia)
Izz = 0.01485          # kg m^2 (yaw inertia)
R_MOTOR = 0.200        # m (motor-to-CG arm length, X-frame)

# ---------------------------------------------------------------------------
# Motor / ESC parameters
# Source: sim/plant.py DEFAULT_MOTOR_TAU (sim/plant.py ~300)
#   sim/plant.py DEFAULT_MRAC_TO_MIXER (sim/plant.py ~284-289)
# ---------------------------------------------------------------------------

MOTOR_TAU = 0.025      # seconds (1st-order ESC lag time constant)
MIXER_R_P = 1170.0     # mrac_to_mixer gain for roll/pitch (mrac.h:36-40, LIGHT)
MIXER_YAW = 1872.0     # mrac_to_mixer gain for yaw
MIXER_Z = 222.0        # mrac_to_mixer gain for z (thrust)

# ---------------------------------------------------------------------------
# Default simulation parameters
# ---------------------------------------------------------------------------

DEFAULT_DT = 1.0 / 500.0  # 500 Hz default sampling rate

# Angle conversion (from sim/baseline.py, pid.c)
DEG2RAD = 0.0174533  # degrees to radians
RAD2DEG = 1.0 / DEG2RAD  # radians to degrees
