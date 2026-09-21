from __future__ import annotations

import struct

import pytest

from ground_station.platform.transactions import (
    Command, Outcome, RejectReason, Result, TransactionLedger,
    build_command, build_result, parse_command, parse_result,
    parse_result_parts,
)
from ground_station.livewatch.transport import LiveTransportError


def test_command_round_trip():
    command = Command(0x1234, 0x0D, index=0, value=1.0, flags=1)
    assert parse_command(build_command(command)) == command


def test_command_crc_and_version_reject():
    frame = bytearray(build_command(Command(1, 1)))
    frame[-1] ^= 0x01
    with pytest.raises(LiveTransportError, match="CRC"):
        parse_command(bytes(frame))
    frame = bytearray(build_command(Command(1, 1)))
    frame[2] = 2
    frame[-1] = 0
    for byte in frame[2:-1]:
        frame[-1] ^= byte
    with pytest.raises(LiveTransportError, match="version"):
        parse_command(bytes(frame))


def test_result_round_trip_and_outcome_type():
    result = Result(42, Outcome.REJECTED, 0x0E, 0, RejectReason.SAFETY_INTERLOCK, "disarmed only")
    decoded = parse_result(build_result(result))
    assert decoded == result
    assert build_result(Result(42, Outcome.ACK, 0x0E, 0)).hex().startswith("aabb3000")


def test_result_parts_matches_wire_splitter_contract():
    expected = Result(0x4243, Outcome.REJECTED, 0xFE, 0,
                      RejectReason.UNKNOWN_COMMAND, "command rejected")
    frame = build_result(expected)
    decoded = parse_result_parts(frame[2], frame[5], frame[6:-1])
    assert decoded == expected


def test_motor_bench_click_frame_bytes():
    """Exact 0xCC 0xDF frame bytes the motor-bench panel (cmd 0x16) produces.

    The panel click send sequence is select M1, CCR=2000 (off), enable=1. The
    envelope is versioned S3 (0xCC 0xDF); firmware/command_protocol.c parses it
    with command_id at offset 6, index at 7, IEEE-754 float value at [10..13],
    and an XOR-of-body CRC. Lock the exact bytes so a regression in the wire
    format is caught here rather than on the bench.
    """
    def xor(data: bytes) -> int:
        acc = 0
        for byte in data:
            acc ^= byte
        return acc

    # version=1, flags=0, txid=1, cmd=0x16, idx=1, payload_len=4, value=1.0
    frame = build_command(Command(transaction_id=1, command_id=0x16, index=1,
                                  value=1.0, flags=0))
    assert frame == bytes([0xCC, 0xDF, 0x01, 0x00, 0x01, 0x00,
                           0x16, 0x01, 0x04, 0x00]) + struct.pack("<f", 1.0) \
        + bytes((xor(frame[2:-1]),))
    assert len(frame) == 15
    # Firmware command_protocol.c field offsets.
    assert frame[0:2] == b"\xCC\xDF"
    assert frame[6] == 0x16 and frame[7] == 1
    assert struct.unpack("<f", frame[10:14])[0] == 1.0
    assert frame[-1] == xor(frame[2:-1])


def test_motor_bench_ack_and_applied_result_bytes():
    """Ack handling: the FC's ACK (0x30) then APPLIED (0x32) result envelopes."""
    ack = Result(1, Outcome.ACK, 0x16, 1, RejectReason.NONE, "queued")
    applied = Result(1, Outcome.APPLIED, 0x16, 1, RejectReason.NONE, "applied")
    rejected = Result(9, Outcome.REJECTED, 0x16, 0,
                      RejectReason.SAFETY_INTERLOCK, "arm state unknown")
    assert build_result(ack).hex().startswith("aabb30")
    assert build_result(applied).hex().startswith("aabb32")
    assert build_result(rejected).hex().startswith("aabb31")
    # parse_result_parts matches the wifi_bridge wire splitter (frame_type is the
    # sixth byte's frame_type decoded by pop_frame: 0x30+outcome).
    for r in (ack, applied, rejected):
        frame = build_result(r)
        assert parse_result_parts(frame[2], frame[5], frame[6:-1]) == r
    # Reject reason survives the round trip so the panel can surface it.
    assert parse_result(build_result(rejected)).reason == RejectReason.SAFETY_INTERLOCK


def test_ledger_is_idempotent_and_bounded():
    ledger = TransactionLedger(capacity=2)
    a = Result(1, Outcome.ACK, 1, 0)
    b = Result(2, Outcome.APPLIED, 1, 0)
    c = Result(3, Outcome.REJECTED, 1, 0, RejectReason.INVALID_ARGUMENT)
    ledger.remember(a)
    ledger.remember(b)
    ledger.remember(c)
    assert ledger.get(1) is None
    assert ledger.get(2) == b
    ledger.remember(c)
    assert ledger.get(3) == c
