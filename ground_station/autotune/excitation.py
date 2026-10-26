"""Ground-station mirror of the SysID excitation (API/sysid.c) and its CMD 0x14 wire map.

CMD 0x14 (TASK/send_data.c:1833-1872): idx 0 axis, 1 signal, 2 f0 Hz, 3 f1 Hz, 4 amplitude deg/s, 5 duration s,
6 start (1) / abort (0), 7 geofence (1 on). A start zeroes the optical-flow origin and the loc PID setpoints first,
so it is only sent while the drone holds at the hover point. Abort while IDLE is a no-op (sysid.c:226-230).

dither() rebuilds the excitation the firmware adds to gyro?PID.Des (StabilizerTask.c:1337-1340). The autotune IV
estimate uses it as the instrument when the log has no 0x03 ID frame (id.dither); a constant time offset between
the rebuilt and the real dither cancels in the ratio Phi_xd/Phi_rd.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

CMD_SYSID = 0x14
IDX_START = 6
IDX_GEOFENCE = 7
CMD_MRAC_FLAGS = 0x0F
MRAC_IDX_INJECTION = 10  # output_injection_on, send_data.c:1779

AXES: Mapping[str, int] = {"pitch": 0, "roll": 1, "yaw": 2}  # SysID_Axis_e (sysid.h:20-25)
OUTER_AXES: Mapping[str, int] = {"pitch": 0, "roll": 1, "yaw": 2, "z": 3, "x": 0, "y": 1}
TARGETS: Mapping[str, int] = {"rate": 0, "torque": 1, "angle": 2, "vel": 3, "pos": 4}
TARGET_AXES: Mapping[str, tuple[str, ...]] = {
    "rate": ("pitch", "roll", "yaw"),
    "torque": ("pitch", "roll", "yaw"),
    "angle": ("pitch", "roll", "yaw"),
    "vel": ("x", "y", "z", "pitch", "roll"),
    "pos": ("x", "y", "z", "pitch", "roll"),
}
SIGNALS: Mapping[str, int] = {"chirp": 0, "multisine": 1}   # SysID_Signal_e
STATE_IDLE, STATE_RAMP_IN, STATE_RUNNING, STATE_RAMP_OUT, STATE_RECOVERY = range(5)

DT_S = 0.005            # SYSID_DT, 200 Hz
RAMP_T_S = 1.5          # SYSID_RAMP_T
RECOVERY_T_S = 2.0      # SYSID_RECOVERY_T
MS_K = 20               # SYSID_MS_K
MS_PREEMP = 1.0         # SYSID_MS_PREEMP
AMP_MAX_DPS: Mapping[str, float] = {"pitch": 90.0, "roll": 90.0, "yaw": 60.0}  # sysid_amp_max
TARGET_AMP_MAX: Mapping[str, Mapping[str, float]] = {
    "rate": {"pitch": 90.0, "roll": 90.0, "yaw": 60.0},
    "torque": {"pitch": 50.0, "roll": 50.0, "yaw": 40.0},
    "angle": {"pitch": 10.0, "roll": 10.0, "yaw": 15.0},
    "vel": {"x": 40.0, "y": 40.0, "z": 0.30, "pitch": 40.0, "roll": 40.0},
    "pos": {"x": 25.0, "y": 25.0, "z": 0.20, "pitch": 25.0, "roll": 25.0},
}
ALT_BAND_M = (0.30, 1.50)  # SYSID_ALT_MIN_M, SYSID_ALT_MAX_M

# Excitations flown by the autotune campaigns (ground_station/service/campaigns/autotune_*.yaml); the cli rebuilds
# the dither from these unless told otherwise. Band from WP-25; amp keeps the open-loop angle swing near 5 deg
# (see angle_swing_deg). All PROPOSED, none measured on this airframe.
ID_EXCITE: Mapping[str, Any] = {"signal": "multisine", "f0": 0.5, "f1": 15.0, "amp": 60.0, "duration_s": 30.0}
VERIFY_EXCITE: Mapping[str, Any] = {"signal": "multisine", "f0": 0.5, "f1": 15.0, "amp": 40.0, "duration_s": 12.0}

# Multi-loop excitation presets (n26-sysid-chain, all PROPOSED, 3-5x bandwidth separation inner -> outer):
# - rate (0.2-40 Hz): >= 4 Welch averages at 0.2 Hz; split into low (0.2-5 Hz) + high (2-40 Hz) or 45 s single run.
# - angle: expected wc ~ 4.5 rad/s (0.72 Hz, ~1/4 of rate wc 18 rad/s) -> band 0.2-6.0 Hz.
# - vel: expected wc ~ 1.3 rad/s (0.21 Hz, ~1/3.5 of angle wc) -> band 0.15-3.0 Hz.
# - pos: expected wc ~ 0.37 rad/s (0.06 Hz, ~1/3.5 of vel wc) -> band 0.1-1.5 Hz.
LOOP_EXCITE: Mapping[str, Mapping[str, Any]] = {
    "rate": ID_EXCITE,
    "rate_wide": {"signal": "multisine", "f0": 0.2, "f1": 40.0, "amp": 60.0, "duration_s": 45.0},
    "rate_low": {"signal": "multisine", "f0": 0.2, "f1": 5.0, "amp": 45.0, "duration_s": 30.0},
    "rate_high": {"signal": "multisine", "f0": 2.0, "f1": 40.0, "amp": 60.0, "duration_s": 30.0},
    "angle": {"signal": "multisine", "f0": 0.2, "f1": 6.0, "amp": 6.0, "duration_s": 30.0},
    "vel": {"signal": "multisine", "f0": 0.15, "f1": 3.0, "amp": 25.0, "duration_s": 35.0},
    "pos": {"signal": "multisine", "f0": 0.1, "f1": 1.5, "amp": 15.0, "duration_s": 40.0},
}


def sanitize(axis: str, f0: float, f1: float, amp: float, duration_s: float,
             target: str = "rate") -> tuple[float, float, float, float]:
    """The firmware clamps of SysID_Start (sysid.c:178-185)."""
    f0 = max(f0, 0.1)
    f1 = max(f1, f0)
    cap = TARGET_AMP_MAX.get(target, AMP_MAX_DPS).get(axis, AMP_MAX_DPS.get(axis, 60.0))
    amp = min(abs(amp), cap)
    return f0, f1, amp, min(max(duration_s, 1.0), 60.0)


def active_s(duration_s: float) -> float:
    """Seconds the excitation is on: ramp in + duration + ramp out."""
    return duration_s + 2.0 * RAMP_T_S


def step_s(duration_s: float) -> float:
    """Seconds an excite step takes: the active window plus RECOVERY back to IDLE."""
    return active_s(duration_s) + RECOVERY_T_S


def start_commands(axis: str, signal: str, f0: float, f1: float, amp: float,
                   duration_s: float) -> list[tuple[int, float]]:
    """(idx, value) pairs for CMD 0x14: parameters, geofence on, then start."""
    ax_id = AXES[axis] if axis in AXES else OUTER_AXES[axis]
    return [(0, float(ax_id)), (1, float(SIGNALS[signal])), (2, float(f0)), (3, float(f1)), (4, float(amp)),
            (5, float(duration_s)), (IDX_GEOFENCE, 1.0), (IDX_START, 1.0)]


def _envelope(t: np.ndarray, duration_s: float) -> np.ndarray:
    run_end = RAMP_T_S + duration_s
    env = np.zeros_like(t)
    up = (t >= 0) & (t < RAMP_T_S)
    env[up] = 0.5 * (1.0 - np.cos(math.pi * t[up] / RAMP_T_S))
    env[(t >= RAMP_T_S) & (t < run_end)] = 1.0
    down = (t >= run_end) & (t < run_end + RAMP_T_S)
    env[down] = 0.5 * (1.0 + np.cos(math.pi * (t[down] - run_end) / RAMP_T_S))
    return env


def _multisine_tones(f0: float, f1: float, duration_s: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    k = np.arange(MS_K)
    f = f0 * (f1 / f0) ** (k / (MS_K - 1))
    phi = -math.pi * k * k / MS_K
    w = (f / f0) ** MS_PREEMP
    scan = np.arange(0.0, min(duration_s, 8.0) + 1e-9, 0.002)
    peak = max(float(np.max(np.abs(np.sin(2 * math.pi * np.outer(scan, f) + phi) @ w))), 1e-6)
    return f, phi, w, peak * 1.05


def dither(t: np.ndarray, signal: str, f0: float, f1: float, amp: float, duration_s: float) -> np.ndarray:
    """Excitation (deg/s) at times t, seconds since the start command; 0 outside the active window."""
    t = np.asarray(t, dtype=float)
    if signal == "multisine":
        f, phi, w, norm = _multisine_tones(f0, f1, duration_s)
        raw = np.sin(2 * math.pi * np.outer(t, f) + phi) @ w / norm
    else:  # log chirp: f0 during ramp in, f0 -> f1 across the run, f1 during ramp out; phase integrated per tick
        tk = np.arange(1, int(math.ceil(active_s(duration_s) / DT_S)) + 1) * DT_S
        tau = np.clip((tk - RAMP_T_S) / max(duration_s, 1e-3), 0.0, 1.0)
        phase = np.cumsum(2 * math.pi * f0 * (f1 / f0) ** tau * DT_S)
        raw = np.interp(t, tk, np.sin(phase))
    return amp * _envelope(t, duration_s) * raw


def angle_swing_deg(signal: str, f0: float, f1: float, amp: float, duration_s: float) -> float:
    """Peak angle (deg) the excitation alone integrates to over the full-amplitude run, mean removed: a sizing
    aid for amp (the outer angle loop only shrinks the low-frequency part of it)."""
    t = np.arange(RAMP_T_S, RAMP_T_S + duration_s, DT_S)
    ang = np.cumsum(dither(t, signal, f0, f1, amp, duration_s)) * DT_S
    return float(np.max(np.abs(ang - ang.mean())))
