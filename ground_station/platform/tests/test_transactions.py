from __future__ import annotations

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
