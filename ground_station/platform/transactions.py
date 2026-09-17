"""Versioned command transactions and structured event frames.

The legacy ``0xCC 0xDD`` command remains supported by the bridge. New callers
use this envelope so a command can be retried safely and its outcome can be
correlated without guessing from telemetry.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum

from ground_station.livewatch.transport import LiveTransportError


COMMAND_SYNC = b"\xCC\xDF"
EVENT_SYNC = b"\xAA\xBB"
VERSION = 1
MAX_PAYLOAD = 64


class Outcome(IntEnum):
    ACK = 0
    REJECTED = 1
    APPLIED = 2


class RejectReason(IntEnum):
    NONE = 0
    BAD_VERSION = 1
    BAD_LENGTH = 2
    BAD_CRC = 3
    UNKNOWN_COMMAND = 4
    INVALID_ARGUMENT = 5
    SAFETY_INTERLOCK = 6
    DUPLICATE = 7
    QUEUE_FULL = 8


@dataclass(frozen=True)
class Command:
    transaction_id: int
    command_id: int
    index: int = 0
    value: float = 0.0
    flags: int = 0
    version: int = VERSION


@dataclass(frozen=True)
class Result:
    transaction_id: int
    outcome: Outcome
    command_id: int
    index: int
    reason: RejectReason = RejectReason.NONE
    detail: str = ""
    version: int = VERSION


def _xor(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
    return crc


def build_command(command: Command) -> bytes:
    """Encode a transaction command with the legacy float command payload."""
    if not 0 <= command.transaction_id <= 0xFFFF:
        raise ValueError("transaction_id must fit uint16")
    if not 0 <= command.command_id <= 0xFF or not 0 <= command.index <= 0xFF:
        raise ValueError("command_id and index must fit uint8")
    body = struct.pack(
        "<BBHBBHf", command.version, command.flags, command.transaction_id,
        command.command_id, command.index, 4, command.value,
    )
    return COMMAND_SYNC + body + bytes((_xor(body),))


def parse_command(frame: bytes) -> Command:
    if len(frame) < 2 + 8 + 4 + 1 or frame[:2] != COMMAND_SYNC:
        raise LiveTransportError("invalid transaction command envelope")
    body = frame[2:-1]
    if frame[-1] != _xor(body):
        raise LiveTransportError("transaction command CRC mismatch")
    version, flags, txid, command_id, index, payload_len = struct.unpack_from("<BBHBBH", body)
    if version != VERSION:
        raise LiveTransportError(f"unsupported transaction version {version}")
    if payload_len != 4 or len(body) != 8 + payload_len:
        raise LiveTransportError("invalid transaction command payload length")
    value = struct.unpack_from("<f", body, 8)[0]
    return Command(txid, command_id, index, value, flags, version)


def build_result(result: Result) -> bytes:
    detail = result.detail.encode("utf-8")[:48]
    body = struct.pack(
        "<BBHBBB", result.version, int(result.outcome), result.transaction_id,
        result.command_id, result.index, int(result.reason),
    ) + bytes((len(detail),)) + detail
    envelope = bytes((0x30 + int(result.outcome), (len(body) >> 8) & 0xFF, len(body) & 0xFF)) + body
    return EVENT_SYNC + envelope + bytes((_xor(envelope),))


def parse_result(frame: bytes) -> Result:
    if len(frame) < 2 + 4 + 7 + 1 or frame[:2] != EVENT_SYNC:
        raise LiveTransportError("invalid transaction result envelope")
    frame_type, hi, lo = frame[2], frame[3], frame[4]
    if frame_type not in (0x30, 0x31, 0x32):
        raise LiveTransportError(f"unknown transaction result frame 0x{frame_type:02X}")
    length = (hi << 8) | lo
    body = frame[5:5 + length]
    envelope = frame[2:5] + body
    if len(body) != length or len(frame) != 6 + length or frame[-1] != _xor(envelope):
        raise LiveTransportError("invalid transaction result length or CRC")
    version, outcome, txid, command_id, index, reason, detail_len = struct.unpack_from("<BBHBBBB", body)
    if version != VERSION or outcome != frame_type - 0x30:
        raise LiveTransportError("inconsistent transaction result version/outcome")
    detail = body[8:8 + detail_len].decode("utf-8", errors="replace")
    return Result(txid, Outcome(outcome), command_id, index, RejectReason(reason), detail, version)


def parse_result_parts(frame_type: int, version: int, payload: bytes) -> Result:
    """Decode the body returned by :func:`pop_frame`.

    ``pop_frame`` exposes the envelope's sixth byte separately as ``version``
    and returns the remaining body as ``payload``.  Keeping this adapter
    separate avoids making every caller reconstruct a frame solely to parse a
    transaction event.
    """
    if frame_type not in (0x30, 0x31, 0x32):
        raise LiveTransportError(f"unknown transaction result frame 0x{frame_type:02X}")
    body = bytes((version,)) + bytes(payload)
    if len(body) < 8:
        raise LiveTransportError("invalid transaction result body length")
    outcome = frame_type - 0x30
    parsed_version, body_outcome, txid, command_id, index, reason, detail_len = \
        struct.unpack_from("<BBHBBBB", body)
    if parsed_version != VERSION or body_outcome != outcome:
        raise LiveTransportError("inconsistent transaction result version/outcome")
    if detail_len > len(body) - 8:
        raise LiveTransportError("invalid transaction result detail length")
    detail = body[8:8 + detail_len].decode("utf-8", errors="replace")
    try:
        outcome_value = Outcome(outcome)
        reason_value = RejectReason(reason)
    except ValueError as exc:
        raise LiveTransportError("invalid transaction result enum") from exc
    return Result(txid, outcome_value, command_id, index, reason_value, detail, parsed_version)


class TransactionLedger:
    """Bounded idempotence cache for command retries."""

    def __init__(self, capacity: int = 64):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._items: dict[int, Result] = {}
        self._order: list[int] = []

    def get(self, transaction_id: int) -> Result | None:
        return self._items.get(transaction_id)

    def remember(self, result: Result) -> None:
        txid = result.transaction_id
        if txid in self._items:
            self._order.remove(txid)
        self._items[txid] = result
        self._order.append(txid)
        while len(self._order) > self.capacity:
            del self._items[self._order.pop(0)]
