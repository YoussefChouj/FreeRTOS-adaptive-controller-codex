"""Ground-side encoder for firmware CMD 0x19 (MRAC Simplex config).

Firmware handler: TASK/send_data.c, Process_GroundStation_Command, id == 0x19.
Wire format: [0xCC][0xDD][CMD_ID=0x19][INDEX][VALUE float32 LE][CRC8-XOR]

Named params mapping (used by executor / DashboardBackend to resolve
plan-level ``simplex.*`` names to wire-level (command_id, index, value)).
"""
from __future__ import annotations

import struct
from typing import Any, Dict, Tuple


# ── Wire constants ──────────────────────────────────────────────────────────

SIMPLEX_CMD_ID: int = 0x19
COMMAND_SYNC_0: int = 0xCC
COMMAND_SYNC_1: int = 0xDD


# ── Index definitions ───────────────────────────────────────────────────────

# idx 0  mode          uint8_t  0=off 1=enforce 2=observe_only
# idx 1  variant       uint8_t  0=PID+MRAC 1=PID-only
# idx 2  roll_max      float    > 0 rad
# idx 3  pitch_max     float    > 0 rad
# idx 4  w_norm_max    float    > 0 rad/s
# idx 5  sat_ticks_max uint16   > 0 (sent as float32)
# idx 6  hold_ticks    uint16   >= 0 (sent as float32)
# idx 7  reset counters Any val >= 0.5 zeroes trip counters

SIMPLEX_PARAM_MAP: Dict[str, Tuple[int, str]] = {
    "simplex.mode":            (0, "0=off 1=enforce 2=observe_only"),
    "simplex.variant":         (1, "0=PID+MRAC 1=PID-only"),
    "simplex.roll_max":        (2, "max roll angle (rad) > 0"),
    "simplex.pitch_max":       (3, "max pitch angle (rad) > 0"),
    "simplex.w_norm_max":      (4, "max weight norm (rad/s) > 0"),
    "simplex.sat_ticks_max":   (5, "saturation tick threshold uint16"),
    "simplex.hold_ticks":      (6, "hold tick count uint16"),
    "simplex.reset_counters":  (7, "reset trip counters"),
}


def get_param_index(name: str) -> int:
    """Return the firmware index for a simplex param name."""
    idx, _ = SIMPLEX_PARAM_MAP[name]
    return idx


def build_simplex_frame(index: int, value: float) -> bytes:
    """Build the legacy 0xCC 0xDD command frame for a simplex param.

    Frame layout: [0xCC][0xDD][CMD_ID][INDEX][VALUE float32 LE][CRC8-XOR]
    CRC is XOR of bytes 2..7 (cmd_id, index, and 4 value bytes).
    """
    cmd_id = SIMPLEX_CMD_ID
    index_u8 = index & 0xFF
    value_bytes = struct.pack("<f", value)

    header_and_payload = bytes([COMMAND_SYNC_0, COMMAND_SYNC_1, cmd_id, index_u8]) + value_bytes
    crc = 0
    for b in header_and_payload[2:]:
        crc ^= b

    return header_and_payload + bytes([crc])


def encode_simplex(name: str, value: float) -> Tuple[int, int, float]:
    """Encode a named simplex param into (command_id, index, value).

    Returns the three fields needed to call ``service.submit_command()`` or
    the bridge's ``send_transaction()`` directly.

    Raises ``KeyError`` if *name* is not in the param map.
    """
    idx, _ = SIMPLEX_PARAM_MAP[name]
    return SIMPLEX_CMD_ID, idx, value


def encode_simplex_frame(name: str, value: float) -> bytes:
    """Encode a named simplex param into a wire frame."""
    _, index, val = encode_simplex(name, value)
    return build_simplex_frame(index, val)
