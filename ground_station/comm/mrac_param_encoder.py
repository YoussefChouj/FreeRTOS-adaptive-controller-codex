"""Ground-side encoder for the MRAC per-element parameter commands.

Firmware handler: TASK/send_data.c, Process_GroundStation_Command, which routes both encodings
below to MracElemParamApply(). Every command is the legacy 9-byte frame

    [0xCC][0xDD][CMD_ID][INDEX][VALUE float32 LE][CRC8-XOR over bytes 2..7]

and the two encodings differ only in how (field, axis, elem) map onto CMD_ID and INDEX:

    legacy  CMD_ID 0x02 gamma / 0x05 What_limit / 0x08 What_tol
            INDEX  (axis << 4) | elem          4-bit elem, so elem 0..15
    wide    CMD_ID 0x20 + (field << 2) + axis  block 0x20..0x2B
            INDEX  elem                        8-bit elem, so elem 0..255

The legacy commands stay byte-for-byte what they were. `encode_mrac_param` picks the legacy
command for elem < 16 and the wide one above, so a frame for an old-range element never changes.

The firmware ignores an element at or past the build's `mrac_n_features` (silently, like every
out-of-range or invalid value). The count is not known here; read it from the ELF with
`ground_station.livewatch.mrac_features.read_mrac_n_features`.
"""
from __future__ import annotations

import struct
from typing import Tuple


# ── Wire constants ──────────────────────────────────────────────────────────

COMMAND_SYNC_0: int = 0xCC
COMMAND_SYNC_1: int = 0xDD

N_AXES: int = 4
AXIS_NAMES: Tuple[str, ...] = ("pitch", "roll", "yaw", "z_rate")   # axis index = position

# Field order is the wide block's field number: id = WIDE_CMD_BASE + 4 * field_number + axis.
FIELD_NAMES: Tuple[str, ...] = ("gamma", "What_limit", "What_tol")
LEGACY_CMD_IDS: Tuple[int, ...] = (0x02, 0x05, 0x08)               # per field, same order

WIDE_CMD_BASE: int = 0x20
WIDE_CMD_LAST: int = WIDE_CMD_BASE + 4 * len(FIELD_NAMES) - 1      # 0x2B
LEGACY_ELEM_LIMIT: int = 16      # the legacy INDEX has a 4-bit element field
WIDE_ELEM_LIMIT: int = 256       # the wide INDEX is the element


# ── Argument checks ─────────────────────────────────────────────────────────

def _field_number(field: str) -> int:
    try:
        return FIELD_NAMES.index(field)
    except ValueError:
        raise ValueError(f"unknown MRAC param field {field!r}; expected one of {FIELD_NAMES}") from None


def _check_axis(axis: int) -> None:
    if not 0 <= axis < N_AXES:
        raise ValueError(f"MRAC axis {axis} out of range 0..{N_AXES - 1}")


def _check_elem(elem: int, limit: int, encoding: str) -> None:
    if not 0 <= elem < limit:
        raise ValueError(f"MRAC element {elem} out of range 0..{limit - 1} for the {encoding} command")


# ── Encoders: (field, axis, elem, value) -> (command_id, index, value) ──────

def encode_mrac_param_legacy(field: str, axis: int, elem: int, value: float) -> Tuple[int, int, float]:
    """Legacy encoding: CMD 0x02 / 0x05 / 0x08, INDEX = (axis << 4) | elem, elem 0..15."""
    number = _field_number(field)
    _check_axis(axis)
    _check_elem(elem, LEGACY_ELEM_LIMIT, "legacy")
    return LEGACY_CMD_IDS[number], (axis << 4) | elem, value


def encode_mrac_param_wide(field: str, axis: int, elem: int, value: float) -> Tuple[int, int, float]:
    """Wide encoding: CMD 0x20..0x2B carries field and axis, INDEX = elem, elem 0..255."""
    number = _field_number(field)
    _check_axis(axis)
    _check_elem(elem, WIDE_ELEM_LIMIT, "wide")
    return WIDE_CMD_BASE + (number << 2) + axis, elem, value


def encode_mrac_param(field: str, axis: int, elem: int, value: float) -> Tuple[int, int, float]:
    """Encode one element update, legacy command for elem < 16 and wide command from 16 up.

    Returns the (command_id, index, value) triple that `service.submit_command()` or the
    bridge's `send_transaction()` takes.
    """
    if 0 <= elem < LEGACY_ELEM_LIMIT:
        return encode_mrac_param_legacy(field, axis, elem, value)
    return encode_mrac_param_wide(field, axis, elem, value)


# ── Frame assembly ──────────────────────────────────────────────────────────

def build_command_frame(command_id: int, index: int, value: float) -> bytes:
    """Build the 9-byte 0xCC 0xDD command frame; CRC is the XOR of bytes 2..7."""
    body = bytes([COMMAND_SYNC_0, COMMAND_SYNC_1, command_id & 0xFF, index & 0xFF]) + struct.pack("<f", value)
    crc = 0
    for byte in body[2:]:
        crc ^= byte
    return body + bytes([crc])


def encode_mrac_param_frame(field: str, axis: int, elem: int, value: float) -> bytes:
    """Encode one element update straight into a wire frame (same command choice as `encode_mrac_param`)."""
    return build_command_frame(*encode_mrac_param(field, axis, elem, value))
