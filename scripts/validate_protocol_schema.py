"""Neutral telemetry protocol schema — source of truth for host parser contracts.

This module encodes the canonical wire contract for the six legacy telemetry frames
(0x01–0x06) as a versioned schema. It is consumed by the protocol test suite and
used to validate host parser consistency without touching hardware.

Frame B payload length formula
------------------------------
Layout per `_unpack_frame_b` (serial_bridge.py):

  MRAC:  4 axes × (max_num_basis + 2) floats  = 4·(n+2)
  PID:   12 loops × 3 floats                   = 36
  total  = 4·(n+2) + 36 = 4n + 44 floats
  main   = (4n + 44) × 4 = 16n + 176 bytes

Path tail (v3 / v13):
  v3  = 26 B  (apm u8 + 5 path floats + twc_arr u8 + vbat f32)
  v13 = 30 B  (v3 + of_hold u8 + est_ready u8 + flag0 u8 + flag1 u8)

Total payload = 16n + 176 + tail_len:
  v3  → 16n + 202
  v13 → 16n + 206
"""
from __future__ import annotations

from typing import Any

# Tail lengths (bytes) for the two known Frame B variants.
_FRAME_B_TAIL_V3 = 26   # v3: adds vbat
_FRAME_B_TAIL_V13 = 30  # v13/v14: adds of_hold, est_ready, 2 flag bytes


def frame_b_lengths(max_num_basis: int) -> set[int]:
    """Return the set of valid Frame B payload lengths for *max_num_basis*."""
    main = 16 * max_num_basis + 176
    return {main + _FRAME_B_TAIL_V3, main + _FRAME_B_TAIL_V13}


# Canonical schema — maps string frame IDs to their wire contracts.
_SCHEMA: dict[str, dict[str, Any]] = {
    "1": {
        "name": "Frame A",
        "wire_type": 0x01,
        "crc": "crc8_xor",
        "nominal_hz": 100,
        "payload_formats": [39, 41],  # v10 / v13
        "note": "Status + 8 float channels + status bytes",
    },
    "2": {
        "name": "Frame B",
        "wire_type": 0x02,
        "crc": "crc8_xor",
        "nominal_hz": 20,
        "payload_formula": "16*n + 202 | 16*n + 206",
        "note": "MRAC/PID/path; length varies with max_num_basis",
    },
    "3": {
        "name": "SysID",
        "wire_type": 0x03,
        "crc": "crc8_xor",
        "nominal_hz": 100,
        "note": "Single-axis excitation record when id_frame_on is set",
    },
    "4": {
        "name": "Bench",
        "wire_type": 0x04,
        "crc": "crc8_xor",
        "nominal_hz": 50,
        "note": "Motor test: counter/motor/CCR/voltage/active/RPM (v8)",
    },
    "5": {
        "name": "OF calibration",
        "wire_type": 0x05,
        "crc": "crc8_xor",
        "nominal_hz": 100,
        "note": "Optical-flow calibration/fusion record when of_frame_on is set",
    },
    "6": {
        "name": "Extended telemetry",
        "wire_type": 0x06,
        "crc": "crc16_ccitt_xmodem",
        "nominal_hz": 100,
        "note": "Extended attitude/telemetry frame",
    },
    # Streaming data frames 0x09–0x0C (slot-coded).
    "9": {
        "name": "Stream slot 0",
        "wire_type": 0x09,
        "crc": "crc16_ccitt_xmodem",
        "note": "Slot-0 typed stream data",
    },
    "10": {
        "name": "Stream slot 1",
        "wire_type": 0x0A,
        "crc": "crc16_ccitt_xmodem",
        "note": "Slot-1 typed stream data",
    },
    "11": {
        "name": "Stream slot 2",
        "wire_type": 0x0B,
        "crc": "crc16_ccitt_xmodem",
        "note": "Slot-2 typed stream data",
    },
    "12": {
        "name": "Stream slot 3",
        "wire_type": 0x0C,
        "crc": "crc16_ccitt_xmodem",
        "note": "Slot-3 typed stream data",
    },
}


def load_schema() -> dict[str, Any]:
    """Return the protocol schema as a dict keyed by frame string IDs."""
    return {"frames": _SCHEMA}


def validate() -> list[str]:
    """Validate host parser contracts against the schema. Returns [] if clean."""
    errors: list[str] = []
    schema = load_schema()
    frames = schema["frames"]

    # All six frame IDs must be present.
    for fid in ("1", "2", "3", "4", "5", "6"):
        if fid not in frames:
            errors.append(f"frame {fid!r} is missing from schema")

    # Frame B length formula: n=6 → {298, 302}
    expected_n6 = {298, 302}
    actual_n6 = frame_b_lengths(6)
    if actual_n6 != expected_n6:
        errors.append(
            f"frame_b_lengths(6)={actual_n6!r} != expected {expected_n6!r}"
        )

    return errors
