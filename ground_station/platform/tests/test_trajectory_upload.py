"""Tests for trajectory upload with retry (Workflow B, Task G4)."""

from __future__ import annotations

import dataclasses
import math
import struct
from typing import Callable, Sequence

import pytest

from ground_station.platform.trajectory_upload import (
    TRAJ_MAX_POINTS,
    UploadResult,
    upload,
)
from ground_station.platform.transactions import Outcome
from ground_station.platform.wfb_commands import (
    CMD_TRAJ,
    TrajIdx,
    WfbClient,
    encode,
)
from ground_station.service.fake_drone import FakeDrone, TrajState
from ground_station.service.trajectory_pipeline import Profile, TrajPoint, generate


def decode_frame(frame: bytes) -> tuple[int, int, int, float]:
    """Decode a CMD frame into (cmd, idx, txid, value) per interfaces.md section 1."""
    txid = struct.unpack_from("<H", frame, 4)[0]
    cmd = frame[6]
    idx = frame[7]
    val = struct.unpack_from("<f", frame, 10)[0]
    return cmd, idx, txid, val


class TransportSpy:
    """Wrapper around drone.send to record, inspect, and hook frames."""

    def __init__(
        self,
        drone: FakeDrone,
        hook: Callable[[bytes, int, int, int, float, int, int], bytes | int | None] | None = None,
    ) -> None:
        self.drone = drone
        self.hook = hook
        self.sent_frames: list[bytes] = []
        self.begin_frames: list[bytes] = []
        self.append_frames: list[bytes] = []
        self.crc_hi_frames: list[bytes] = []
        self.commit_frames: list[bytes] = []
        self.attempt: int = 0
        self.append_idx_in_attempt: int = 0

    def send(self, frame: bytes) -> int:
        self.sent_frames.append(frame)
        cmd, idx, txid, val = decode_frame(frame)

        cur_idx = -1
        if cmd == CMD_TRAJ and idx == TrajIdx.BEGIN:
            self.attempt += 1
            self.append_idx_in_attempt = 0
            self.begin_frames.append(frame)
        elif cmd == CMD_TRAJ and idx == TrajIdx.APPEND:
            self.append_frames.append(frame)
            cur_idx = self.append_idx_in_attempt
            self.append_idx_in_attempt += 1
        elif cmd == CMD_TRAJ and idx == TrajIdx.CRC_HI:
            self.crc_hi_frames.append(frame)
        elif cmd == CMD_TRAJ and idx == TrajIdx.COMMIT:
            self.commit_frames.append(frame)

        if self.hook is not None:
            res = self.hook(frame, cmd, idx, txid, val, self.attempt, cur_idx)
            if isinstance(res, int):
                return res
            if isinstance(res, bytes):
                return self.drone.send(res)

        return self.drone.send(frame)


@pytest.fixture
def valid_points() -> list[TrajPoint]:
    """Return a 3-point trajectory starting and ending at hover_z (0.5 m)."""
    return [
        TrajPoint(x=0.0, y=0.0, z=0.5, yaw_deg=0.0, t=0.0),
        TrajPoint(x=0.05, y=0.0, z=0.5, yaw_deg=0.0, t=1.0),
        TrajPoint(x=0.0, y=0.0, z=0.5, yaw_deg=0.0, t=2.0),
    ]


def test_1_happy_path() -> None:
    """1. Happy path: ok True, attempts 1, error None; FakeDrone status shows trajectory state READY; APPEND frames sent == 5N."""
    drone = FakeDrone()
    spy = TransportSpy(drone)
    client = WfbClient(spy.send)

    points = generate("circle", {"radius_m": 0.3}, Profile(0.3, 0.5, 0.02, 0.5))
    n = len(points)

    result = upload(points, client)

    assert result.ok is True
    assert result.attempts == 1
    assert result.error is None

    st = drone.status()
    assert st["traj_state"] == float(TrajState.READY)
    assert len(spy.append_frames) == 5 * n
    assert len(spy.begin_frames) == 1


def test_2_first_attempt_corrupted(valid_points: list[TrajPoint]) -> None:
    """2. First attempt corrupted (one APPEND value changed on attempt 1 only): COMMIT rejected, attempt 2 ok; result ok True, attempts 2."""
    def corrupt_attempt_1(
        frame: bytes, cmd: int, idx: int, txid: int, val: float, attempt: int, append_idx: int
    ) -> bytes | None:
        if attempt == 1 and cmd == CMD_TRAJ and idx == TrajIdx.APPEND and append_idx == 0:
            return encode(cmd, idx, val + 0.05, txid)
        return None

    drone = FakeDrone()
    spy = TransportSpy(drone, hook=corrupt_attempt_1)
    client = WfbClient(spy.send)

    result = upload(valid_points, client, attempts=3)

    assert result.ok is True
    assert result.attempts == 2
    assert result.error is None

    st = drone.status()
    assert st["traj_state"] == float(TrajState.READY)
    assert len(spy.begin_frames) == 2


def test_3_always_corrupted(valid_points: list[TrajPoint]) -> None:
    """3. Always corrupted: ok False, attempts 3, error "commit rejected", exactly 3 BEGIN frames sent."""
    def corrupt_every_attempt(
        frame: bytes, cmd: int, idx: int, txid: int, val: float, attempt: int, append_idx: int
    ) -> bytes | None:
        if cmd == CMD_TRAJ and idx == TrajIdx.APPEND and append_idx == 0:
            return encode(cmd, idx, val + 0.05, txid)
        return None

    drone = FakeDrone()
    spy = TransportSpy(drone, hook=corrupt_every_attempt)
    client = WfbClient(spy.send)

    result = upload(valid_points, client, attempts=3)

    assert result.ok is False
    assert result.attempts == 3
    assert result.error == "commit rejected"
    assert len(spy.begin_frames) == 3


def test_4_attempts_1_with_corruption(valid_points: list[TrajPoint]) -> None:
    """4. attempts=1 with corruption: ok False, attempts 1."""
    def corrupt_every_attempt(
        frame: bytes, cmd: int, idx: int, txid: int, val: float, attempt: int, append_idx: int
    ) -> bytes | None:
        if cmd == CMD_TRAJ and idx == TrajIdx.APPEND and append_idx == 0:
            return encode(cmd, idx, val + 0.05, txid)
        return None

    drone = FakeDrone()
    spy = TransportSpy(drone, hook=corrupt_every_attempt)
    client = WfbClient(spy.send)

    result = upload(valid_points, client, attempts=1)

    assert result.ok is False
    assert result.attempts == 1
    assert result.error == "commit rejected"
    assert len(spy.begin_frames) == 1


def test_5_append_rejected_at_float_10_on_attempt_1(valid_points: list[TrajPoint]) -> None:
    """5. Wrapper returns REJECTED for APPEND float 10 on attempt 1: no APPEND after it in attempt 1; attempt 2 ok."""
    append_calls_attempt_1: int = 0

    def reject_float_10_attempt_1(
        frame: bytes, cmd: int, idx: int, txid: int, val: float, attempt: int, append_idx: int
    ) -> int | None:
        nonlocal append_calls_attempt_1
        if attempt == 1 and cmd == CMD_TRAJ and idx == TrajIdx.APPEND:
            append_calls_attempt_1 += 1
            if append_idx == 10:
                return int(Outcome.REJECTED)
        return None

    drone = FakeDrone()
    spy = TransportSpy(drone, hook=reject_float_10_attempt_1)
    client = WfbClient(spy.send)

    result = upload(valid_points, client, attempts=3)

    assert result.ok is True
    assert result.attempts == 2
    assert result.error is None

    # In attempt 1: floats 0..10 were attempted (11 frames total), no float after 10
    assert append_calls_attempt_1 == 11
    # Attempt 2 sent all 5 * 3 = 15 floats
    assert len(spy.append_frames) == 11 + 15
    assert len(spy.begin_frames) == 2
    assert drone.status()["traj_state"] == float(TrajState.READY)


def test_6_begin_rejected_on_every_attempt(valid_points: list[TrajPoint]) -> None:
    """6. BEGIN rejected on every attempt: error "begin rejected", no APPEND frame sent at all."""
    def reject_all_begin(
        frame: bytes, cmd: int, idx: int, txid: int, val: float, attempt: int, append_idx: int
    ) -> int | None:
        if cmd == CMD_TRAJ and idx == TrajIdx.BEGIN:
            return int(Outcome.REJECTED)
        return None

    drone = FakeDrone()
    spy = TransportSpy(drone, hook=reject_all_begin)
    client = WfbClient(spy.send)

    result = upload(valid_points, client, attempts=3)

    assert result.ok is False
    assert result.attempts == 3
    assert result.error == "begin rejected"
    assert len(spy.append_frames) == 0
    assert len(spy.begin_frames) == 3


def test_7_prechecks_count_1_point() -> None:
    """7a. Pre-checks: 1 point -> "count"; zero frames sent."""
    drone = FakeDrone()
    spy = TransportSpy(drone)
    client = WfbClient(spy.send)

    pts_1 = [TrajPoint(0.0, 0.0, 0.5, 0.0, 0.0)]
    result = upload(pts_1, client)

    assert result.ok is False
    assert result.attempts == 0
    assert result.error == "count"
    assert len(spy.sent_frames) == 0


def test_7_prechecks_count_601_points() -> None:
    """7b. Pre-checks: 601 points -> "count"; zero frames sent."""
    drone = FakeDrone()
    spy = TransportSpy(drone)
    client = WfbClient(spy.send)

    pts_601 = [TrajPoint(0.0, 0.0, 0.5, 0.0, float(i)) for i in range(TRAJ_MAX_POINTS + 1)]
    result = upload(pts_601, client)

    assert result.ok is False
    assert result.attempts == 0
    assert result.error == "count"
    assert len(spy.sent_frames) == 0


@pytest.mark.parametrize("field_name", ["x", "y", "z", "yaw_deg", "t"])
def test_7_prechecks_nan_fields(field_name: str) -> None:
    """7c. Pre-checks: NaN in each of the five fields -> "nonfinite" (parametrize); zero frames sent."""
    drone = FakeDrone()
    spy = TransportSpy(drone)
    client = WfbClient(spy.send)

    kwargs0 = {"x": 0.0, "y": 0.0, "z": 0.5, "yaw_deg": 0.0, "t": 0.0}
    kwargs1 = {"x": 0.0, "y": 0.0, "z": 0.5, "yaw_deg": 0.0, "t": 1.0}
    kwargs1[field_name] = float("nan")

    pts = [TrajPoint(**kwargs0), TrajPoint(**kwargs1)]
    result = upload(pts, client)

    assert result.ok is False
    assert result.attempts == 0
    assert result.error == "nonfinite"
    assert len(spy.sent_frames) == 0


@pytest.mark.parametrize("invalid_attempts", [0, -1, -5])
def test_7_prechecks_attempts_less_than_1(
    valid_points: list[TrajPoint], invalid_attempts: int
) -> None:
    """7d. Pre-checks: attempts < 1 raises ValueError."""
    drone = FakeDrone()
    client = WfbClient(drone.send)

    with pytest.raises(ValueError, match="attempts"):
        upload(valid_points, client, attempts=invalid_attempts)


def test_8_point_with_x_point_one() -> None:
    """8. A point with x = 0.1 (not exact in float32) still commits (CRC over float32 values)."""
    drone = FakeDrone()
    client = WfbClient(drone.send)

    points = [
        TrajPoint(x=0.0, y=0.0, z=0.5, yaw_deg=0.0, t=0.0),
        TrajPoint(x=0.1, y=0.0, z=0.5, yaw_deg=0.0, t=1.0),
        TrajPoint(x=0.0, y=0.0, z=0.5, yaw_deg=0.0, t=2.0),
    ]

    result = upload(points, client)

    assert result.ok is True
    assert result.attempts == 1
    assert result.error is None
    st = drone.status()
    assert st["traj_state"] == float(TrajState.READY)


def test_9_upload_result_frozen() -> None:
    """9. UploadResult is frozen (assigning a field raises dataclasses.FrozenInstanceError)."""
    res = UploadResult(ok=True, attempts=1, error=None)

    with pytest.raises(dataclasses.FrozenInstanceError):
        res.ok = False  # type: ignore[misc]

    with pytest.raises(dataclasses.FrozenInstanceError):
        res.attempts = 2  # type: ignore[misc]

    with pytest.raises(dataclasses.FrozenInstanceError):
        res.error = "err"  # type: ignore[misc]


def test_transport_exception_propagates(valid_points: list[TrajPoint]) -> None:
    """Exceptions from the client or transport propagate uncaught."""
    def error_send(frame: bytes) -> int:
        raise ConnectionResetError("link dropped")

    client = WfbClient(error_send)
    with pytest.raises(ConnectionResetError, match="link dropped"):
        upload(valid_points, client)


def test_tuple_sequence_of_points(valid_points: list[TrajPoint]) -> None:
    """upload accepts any Sequence[TrajPoint], such as a tuple."""
    drone = FakeDrone()
    client = WfbClient(drone.send)

    result = upload(tuple(valid_points), client)

    assert result.ok is True
    assert result.attempts == 1
    assert result.error is None
    assert drone.status()["traj_state"] == float(TrajState.READY)
