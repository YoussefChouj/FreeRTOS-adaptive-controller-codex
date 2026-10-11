"""Ground-side encoder for firmware CMD 0x2C EXT_POS (live camera position fix, shadow mode).

Firmware handler: TASK/send_data.c Cmd_ExtPos; decode API/ekf_of.c ext_pos_decode (keep the two in step).
Design: docs/video-truth/live_camera_fusion_design_2026-10-11.md.
Wire format: [0xCC][0xDD][0x2C][INDEX][VALUE float32 LE][CRC8-XOR]
  INDEX 0..253: a fix; INDEX = fix age in 10 ms units (capture to send), VALUE = exact integer code
                (qx + 2048) * 4096 + (qy + 2048), q = round(cm / 0.5) clamped to [-2048, 2047] (+-1023.5 cm).
                An integer below 2^24 is exact in float32, so the code never forms a NaN pattern.
  INDEX 254:    fixed link delay, VALUE ms (0..1000).
  INDEX 255:    fusion on/off, VALUE 0/1.
Positions are in the EKF earth frame, cm (ano_of.earth_x / earth_y).
"""
from __future__ import annotations

from ground_station.comm.mrac_param_encoder import build_command_frame

EXT_POS_CMD_ID: int = 0x2C
EXT_POS_Q_CM: float = 0.5
EXT_POS_IDX_DELAY: int = 0xFE
EXT_POS_IDX_ON: int = 0xFF
EXT_POS_AGE_MAX_10MS: int = 0xFD
EXT_POS_DELAY_MAX_MS: float = 1000.0


def _q(cm: float) -> int:
    return max(-2048, min(2047, int(round(cm / EXT_POS_Q_CM))))


def ext_pos_encode(x_cm: float, y_cm: float) -> float:
    """Position (cm, EKF earth frame) -> the exact integer VALUE code."""
    return float((_q(x_cm) + 2048) * 4096 + (_q(y_cm) + 2048))


def ext_pos_decode(value: float) -> tuple[float, float] | None:
    """Inverse of ext_pos_encode, same rules as the firmware: None for a code that is not an integer in [0, 2^24)."""
    if not (0.0 <= value < 16777216.0) or value != int(value):
        return None
    c = int(value)
    return ((c >> 12) - 2048) * EXT_POS_Q_CM, ((c & 0xFFF) - 2048) * EXT_POS_Q_CM


def ext_pos_fix_frame(x_cm: float, y_cm: float, age_s: float) -> bytes:
    """One fix frame. age_s = ground-station time from frame capture to send; clamped to 0..2.53 s."""
    age = max(0, min(EXT_POS_AGE_MAX_10MS, int(round(age_s * 100.0))))
    return build_command_frame(EXT_POS_CMD_ID, age, ext_pos_encode(x_cm, y_cm))


def ext_pos_on_frame(on: bool) -> bytes:
    return build_command_frame(EXT_POS_CMD_ID, EXT_POS_IDX_ON, 1.0 if on else 0.0)


def ext_pos_delay_frame(delay_ms: float) -> bytes:
    if not 0.0 <= delay_ms <= EXT_POS_DELAY_MAX_MS:
        raise ValueError(f"delay_ms {delay_ms} outside 0..{EXT_POS_DELAY_MAX_MS:.0f}")
    return build_command_frame(EXT_POS_CMD_ID, EXT_POS_IDX_DELAY, float(delay_ms))
