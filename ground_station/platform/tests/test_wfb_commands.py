"""Tests for Workflow B command encoding and WfbClient."""

from __future__ import annotations

import pytest

from ground_station.platform.transactions import Outcome, parse_command
from ground_station.platform.wfb_commands import (
    CMD_ARM,
    CMD_KILL,
    CMD_PRIM,
    CMD_TRAJ,
    ArmIdx,
    PrimIdx,
    TrajIdx,
    WfbClient,
    encode,
)


def test_1_encode_frame_format() -> None:
    """encode(0x1A, 2, 0.0, 1) == bytes.fromhex('ccdf010001001a020400000000001c') and has length 15."""
    frame = encode(0x1A, 2, 0.0, 1)
    expected = bytes.fromhex("ccdf010001001a020400000000001c")
    assert frame == expected
    assert len(frame) == 15


@pytest.mark.parametrize(
    "method_name,args,expected_cmd,expected_idx,expected_val",
    [
        ("arm", (), CMD_ARM, ArmIdx.ARM, 1.0),
        ("idle", (), CMD_ARM, ArmIdx.IDLE, 1.0),
        ("kill", (), CMD_KILL, 0, 0.0),
        ("takeoff", (), CMD_PRIM, PrimIdx.TAKEOFF, 0.0),
        ("land", (), CMD_PRIM, PrimIdx.LAND, 0.0),
        ("heartbeat", (), CMD_PRIM, PrimIdx.HEARTBEAT, 0.0),
        ("set_hover_z", (0.7,), CMD_PRIM, PrimIdx.SET_HOVER_Z, 0.7),
        ("traj_begin", (12,), CMD_TRAJ, TrajIdx.BEGIN, 12.0),
        ("traj_append", (3.14159,), CMD_TRAJ, TrajIdx.APPEND, 3.14159),
        ("traj_start", (), CMD_TRAJ, TrajIdx.START, 0.0),
        ("traj_stop", (), CMD_TRAJ, TrajIdx.STOP, 0.0),
        ("traj_clear", (), CMD_TRAJ, TrajIdx.CLEAR, 0.0),
    ],
)
def test_2_client_methods_command_fields(
    method_name: str,
    args: tuple,
    expected_cmd: int,
    expected_idx: int,
    expected_val: float,
) -> None:
    """Parametrized test: every WfbClient method sends the expected command id, index, and value."""
    recorded_frames: list[bytes] = []

    def mock_send(frame: bytes) -> int:
        recorded_frames.append(frame)
        return int(Outcome.APPLIED)

    client = WfbClient(mock_send)
    method = getattr(client, method_name)
    result = method(*args)

    assert result is True
    assert len(recorded_frames) == 1
    cmd = parse_command(recorded_frames[0])
    assert cmd.command_id == expected_cmd
    assert cmd.index == expected_idx
    assert cmd.value == pytest.approx(expected_val, abs=1e-5)


@pytest.mark.parametrize("outcome,expected_ret", [
    (Outcome.APPLIED, True),
    (Outcome.ACK, False),
    (Outcome.REJECTED, False),
])
def test_3_client_outcome_returns(outcome: Outcome, expected_ret: bool) -> None:
    """A method returns True for APPLIED and False for ACK and for REJECTED."""
    client = WfbClient(lambda _: int(outcome))
    assert client.arm() is expected_ret
    assert client.takeoff() is expected_ret
    assert client.traj_start() is expected_ret


def test_4_transaction_id_sequence() -> None:
    """Transaction ids: 1, 2, 3 for three frames; first_txid=65535 gives 65535 then 1; last_txid follows."""
    frames: list[bytes] = []
    client = WfbClient(lambda f: (frames.append(f), int(Outcome.APPLIED))[1])

    assert client.last_txid == 0

    client.arm()
    assert client.last_txid == 1
    assert parse_command(frames[0]).transaction_id == 1

    client.takeoff()
    assert client.last_txid == 2
    assert parse_command(frames[1]).transaction_id == 2

    client.land()
    assert client.last_txid == 3
    assert parse_command(frames[2]).transaction_id == 3

    # Wrapping test from 65535 to 1
    wrap_frames: list[bytes] = []
    wrap_client = WfbClient(
        lambda f: (wrap_frames.append(f), int(Outcome.APPLIED))[1],
        first_txid=65535,
    )
    assert wrap_client.last_txid == 0

    wrap_client.heartbeat()
    assert wrap_client.last_txid == 65535
    assert parse_command(wrap_frames[0]).transaction_id == 65535

    wrap_client.heartbeat()
    assert wrap_client.last_txid == 1
    assert parse_command(wrap_frames[1]).transaction_id == 1


def test_5_traj_commit() -> None:
    """traj_commit(0x12345678) sends CRC_HI 0x1234 then COMMIT 0x5678; rejection handling and validation."""
    frames: list[bytes] = []
    client = WfbClient(lambda f: (frames.append(f), int(Outcome.APPLIED))[1])

    ok = client.traj_commit(0x12345678)
    assert ok is True
    assert len(frames) == 2

    cmd1 = parse_command(frames[0])
    assert cmd1.command_id == CMD_TRAJ
    assert cmd1.index == TrajIdx.CRC_HI
    assert cmd1.value == pytest.approx(0x1234)

    cmd2 = parse_command(frames[1])
    assert cmd2.command_id == CMD_TRAJ
    assert cmd2.index == TrajIdx.COMMIT
    assert cmd2.value == pytest.approx(0x5678)

    # When CRC_HI is rejected, sends only one frame and returns False
    frames.clear()
    client_rej = WfbClient(lambda f: (frames.append(f), int(Outcome.REJECTED))[1])
    ok_rej = client_rej.traj_commit(0x12345678)
    assert ok_rej is False
    assert len(frames) == 1
    assert parse_command(frames[0]).index == TrajIdx.CRC_HI

    # Validation: crc outside 0..0xFFFFFFFF or not an int raises ValueError with nothing sent
    frames.clear()
    for bad_crc in (-1, 2**32, 1.5, True):
        with pytest.raises(ValueError):
            client.traj_commit(bad_crc)  # type: ignore[arg-type]
    assert len(frames) == 0


def test_6_traj_begin_validation() -> None:
    """traj_begin(2.0) and traj_begin(True) raise ValueError with nothing sent."""
    frames: list[bytes] = []
    client = WfbClient(lambda f: (frames.append(f), int(Outcome.APPLIED))[1])

    with pytest.raises(ValueError):
        client.traj_begin(2.0)  # type: ignore[arg-type]
    assert len(frames) == 0

    with pytest.raises(ValueError):
        client.traj_begin(True)  # type: ignore[arg-type]
    assert len(frames) == 0


def test_7_send_exception_propagates() -> None:
    """An exception from send propagates unchanged."""
    class CustomTransportError(Exception):
        pass

    def failing_send(_: bytes) -> int:
        raise CustomTransportError("socket failure")

    client = WfbClient(failing_send)
    with pytest.raises(CustomTransportError, match="socket failure"):
        client.arm()
