"""Typed command client for Workflow B firmware commands.

Encodes flight primitive and trajectory commands using the binary envelope
defined in ground_station.platform.transactions and docs/workflow-b/interfaces.md.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Callable

from ground_station.platform.transactions import Command, Outcome, build_command


CMD_KILL: int = 0x0D
CMD_ARM: int = 0x0E
CMD_PRIM: int = 0x1A
CMD_TRAJ: int = 0x1B


class PrimIdx(IntEnum):
    TAKEOFF = 0
    LAND = 1
    HEARTBEAT = 2
    SET_HOVER_Z = 3


class TrajIdx(IntEnum):
    BEGIN = 0
    APPEND = 1
    CRC_HI = 2
    COMMIT = 3
    START = 4
    STOP = 5
    CLEAR = 6


class ArmIdx(IntEnum):
    ARM = 0
    IDLE = 1


def encode(cmd: int, idx: int, value: float, txid: int) -> bytes:
    """Build a versioned transaction frame for a typed command."""
    return build_command(
        Command(
            transaction_id=txid,
            command_id=cmd,
            index=idx,
            value=float(value),
        )
    )


class WfbClient:
    """Client for dispatching Workflow B drone commands with sequential transaction IDs."""

    def __init__(self, send: Callable[[bytes], int], first_txid: int = 1) -> None:
        self._send = send
        self._next_txid: int = first_txid
        self._last_txid: int = 0

    @property
    def last_txid(self) -> int:
        """Transaction ID of the most recently sent frame (0 before first send)."""
        return self._last_txid

    def _next_id(self) -> int:
        txid = self._next_txid
        self._last_txid = txid
        self._next_txid += 1
        if self._next_txid > 65535:
            self._next_txid = 1
        return txid

    def _send_cmd(self, cmd: int, idx: int, value: float) -> bool:
        txid = self._next_id()
        frame = encode(cmd, idx, value, txid)
        result = self._send(frame)
        return result == Outcome.APPLIED

    def arm(self) -> bool:
        """Arm the drone."""
        return self._send_cmd(CMD_ARM, ArmIdx.ARM, 1.0)

    def idle(self) -> bool:
        """Set motors to idle speed."""
        return self._send_cmd(CMD_ARM, ArmIdx.IDLE, 1.0)

    def kill(self) -> bool:
        """Operator emergency abort: motors cut off immediately."""
        return self._send_cmd(CMD_KILL, 0, 0.0)

    def takeoff(self) -> bool:
        """Command takeoff to hover height."""
        return self._send_cmd(CMD_PRIM, PrimIdx.TAKEOFF, 0.0)

    def land(self) -> bool:
        """Command vehicle to return, settle, and land."""
        return self._send_cmd(CMD_PRIM, PrimIdx.LAND, 0.0)

    def heartbeat(self) -> bool:
        """Send periodic ground-station heartbeat."""
        return self._send_cmd(CMD_PRIM, PrimIdx.HEARTBEAT, 0.0)

    def set_hover_z(self, z: float) -> bool:
        """Configure target hover altitude."""
        return self._send_cmd(CMD_PRIM, PrimIdx.SET_HOVER_Z, float(z))

    def traj_begin(self, n: int) -> bool:
        """Begin uploading an n-point trajectory."""
        if isinstance(n, bool) or not isinstance(n, int):
            raise ValueError(f"n must be an integer (not bool), got {n!r}")
        return self._send_cmd(CMD_TRAJ, TrajIdx.BEGIN, float(n))

    def traj_append(self, v: float) -> bool:
        """Append the next float to the uploading trajectory buffer."""
        return self._send_cmd(CMD_TRAJ, TrajIdx.APPEND, float(v))

    def traj_commit(self, crc: int) -> bool:
        """Commit uploaded trajectory with CRC32 verification (CRC_HI followed by COMMIT)."""
        if isinstance(crc, bool) or not isinstance(crc, int) or not (0 <= crc <= 0xFFFFFFFF):
            raise ValueError(f"crc must be an integer in 0..0xFFFFFFFF, got {crc!r}")
        hi_ok = self._send_cmd(CMD_TRAJ, TrajIdx.CRC_HI, float((crc >> 16) & 0xFFFF))
        if not hi_ok:
            return False
        return self._send_cmd(CMD_TRAJ, TrajIdx.COMMIT, float(crc & 0xFFFF))

    def traj_start(self) -> bool:
        """Start executing the committed trajectory."""
        return self._send_cmd(CMD_TRAJ, TrajIdx.START, 0.0)

    def traj_stop(self) -> bool:
        """Stop executing the current trajectory and return to hover point."""
        return self._send_cmd(CMD_TRAJ, TrajIdx.STOP, 0.0)

    def traj_clear(self) -> bool:
        """Clear the trajectory buffer."""
        return self._send_cmd(CMD_TRAJ, TrajIdx.CLEAR, 0.0)
