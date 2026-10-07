"""Onboard preset program (CMD 0x1C): segment records, CRC and the sticky-field upload.

Mirrors API/wfb_prog.h. A program is a list of segments; each segment is one record of F_COUNT float32 fields
(atom, profile, v, a, j, v_in, v_out, P0..P11). The firmware keeps a sticky staging record that BEGIN zeroes,
so only the fields that differ from the previous segment are sent before each PUSH. The firmware generates the
dense setpoints itself and validates the whole program at COMMIT. Design: docs/workflow-c/onboard-preset-program.md.
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum
from typing import Protocol, Sequence

from ground_station.platform.trajectory_upload import UploadResult
from ground_station.platform.wfb_commands import ProgIdx

PROG_MAX_SEGS: int = 64  # WFB_PROG_MAX_SEGS
P_COUNT: int = 12
F_COUNT: int = 7 + P_COUNT  # WFB_PROG_F_COUNT
F_P0: int = 7


class Atom(IntEnum):
    HOLD = 0  # P0 dwell_s
    LINE = 1  # P0 x, P1 y, P2 z (m, absolute), P3 yaw_deg (relative); v m/s
    TURN = 2  # P0 yaw_deg (relative); v deg/s, a deg/s^2, j deg/s^3
    ARC = 3  # P0 cx, P1 cy (m), P2 sweep_deg (+ = CCW), P3 dz (m), P4 yaw_mode (0 hold, 1 turn with arc)
    LISSA = 4  # P0-2 amp x/y/z, P3-5 n x/y/z, P6-8 phase x/y/z (deg), P9 cycles; v cycles/s


class Profile(IntEnum):
    TRAP = 0  # constant-accel ramps
    SCURVE = 1  # jerk-limited ramps (uses j)
    SINE = 2  # (1 - cos) ramps
    QUINTIC = 3  # min-jerk ramps, zero accel at both ends


def _f32(v: float) -> float:
    return struct.unpack("<f", struct.pack("<f", float(v)))[0]


@dataclass(frozen=True)
class Segment:
    atom: Atom
    profile: Profile = Profile.TRAP
    v: float = 0.0
    a: float = 0.0
    j: float = 0.0
    v_in: float = 0.0
    v_out: float = 0.0
    p: tuple[float, ...] = ()

    def record(self) -> tuple[float, ...]:
        """The F_COUNT float32 fields in wire order, P padded with zeros."""
        if len(self.p) > P_COUNT:
            raise ValueError(f"at most {P_COUNT} atom parameters, got {len(self.p)}")
        vals = (int(self.atom), int(self.profile), self.v, self.a, self.j, self.v_in, self.v_out, *self.p)
        return tuple(_f32(v) for v in vals) + (0.0,) * (P_COUNT - len(self.p))


class ProgClient(Protocol):
    def prog(self, idx: int, value: float) -> bool: ...


def crc32(segments: Sequence[Segment]) -> int:
    """CRC-32 (zlib polynomial, same as wfb_crc32) over the concatenated little-endian float32 records."""
    floats = [v for s in segments for v in s.record()]
    return zlib.crc32(struct.pack(f"<{len(floats)}f", *floats)) & 0xFFFFFFFF


def frames(segments: Sequence[Segment]) -> list[tuple[int, float]]:
    """(idx, value) of every CMD 0x1C frame of one upload: BEGIN, changed fields + PUSH per segment, CRC_HI, COMMIT."""
    crc = crc32(segments)
    out: list[tuple[int, float]] = [(int(ProgIdx.BEGIN), float(len(segments)))]
    prev = (0.0,) * F_COUNT
    for k, seg in enumerate(segments):
        rec = seg.record()
        out.extend((i, v) for i, v in enumerate(rec) if v != prev[i])
        out.append((int(ProgIdx.PUSH), float(k)))
        prev = rec
    out.append((int(ProgIdx.CRC_HI), float((crc >> 16) & 0xFFFF)))
    out.append((int(ProgIdx.COMMIT), float(crc & 0xFFFF)))
    return out


def upload(segments: Sequence[Segment], client: ProgClient, attempts: int = 3) -> UploadResult:
    """Upload a program over CMD 0x1C with retry; the firmware validates it at COMMIT.

    A rejected COMMIT leaves g_wfb_status.prog_err_seg naming the failing segment (-1 = CRC or count).
    """
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise ValueError(f"attempts must be an integer >= 1, got {attempts!r}")
    if not 1 <= len(segments) <= PROG_MAX_SEGS:
        return UploadResult(ok=False, attempts=0, error="count")
    try:
        recs = [s.record() for s in segments]
    except ValueError:
        return UploadResult(ok=False, attempts=0, error="params")
    if not all(math.isfinite(v) for r in recs for v in r):
        return UploadResult(ok=False, attempts=0, error="nonfinite")

    plan = frames(segments)
    last_error: str | None = None
    for attempt in range(1, attempts + 1):
        for n, (idx, val) in enumerate(plan):
            if not client.prog(idx, val):
                last_error = _name(idx) + f" rejected at frame {n}"
                break
        else:
            return UploadResult(ok=True, attempts=attempt, error=None)
    return UploadResult(ok=False, attempts=attempts, error=last_error)


def _name(idx: int) -> str:
    try:
        return ProgIdx(idx).name.lower()
    except ValueError:
        return f"field {idx}"
