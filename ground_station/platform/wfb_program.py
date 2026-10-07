"""Onboard preset program (CMD 0x1C): segment records, CRC and the sticky-field upload.

Mirrors API/wfb_prog.h. A program is a list of segments; each segment is one record of F_COUNT float32 fields
(atom, profile, v, a, j, v_in, v_out, P0..P11). The firmware keeps a sticky staging record that BEGIN zeroes,
so only the fields that differ from the previous segment are sent before each PUSH. The firmware generates the
dense setpoints itself and validates the whole program at COMMIT. Design: docs/workflow-c/onboard-preset-program.md.
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from enum import IntEnum
from typing import Protocol, Sequence

from ground_station.platform.trajectory_upload import UploadResult
from ground_station.platform.wfb_commands import ProgIdx

REPO = Path(__file__).resolve().parents[2]
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


ERR_NAMES = ("none", "state", "range", "count", "crc", "time", "bounds", "endpoint", "speed", "safety")  # wfb_err_t


@dataclass(frozen=True)
class Preview:
    """What the firmware's own evaluator does with a program: the COMMIT verdict and, when it passes, the path."""

    err: int  # wfb_err_t of the upload; 0 = COMMIT passes
    err_seg: int  # failing segment, -1 = none
    t_total_s: float
    segs: tuple[tuple[float, float], ...]  # (t0_s, dur_s) per segment
    points: tuple[tuple[float, float, float, float, float], ...]  # (t_s, x_m, y_m, z_m, yaw_deg rel. to START)

    @property
    def ok(self) -> bool:
        return self.err == 0

    @property
    def error(self) -> str | None:
        if self.ok:
            return None
        name = ERR_NAMES[self.err] if 0 <= self.err < len(ERR_NAMES) else str(self.err)
        return name if self.err_seg < 0 else f"{name} at segment {self.err_seg}"


def preview(segments: Sequence[Segment], hover_z_m: float, dt_s: float = 0.05) -> Preview:
    """Run the program through API/wfb_prog.c on the host (tools/wfb_prog_preview.c, gcc, cached build).

    Same code, limits and caps as the flight firmware, so the duration, the path and the COMMIT error are the
    ones the drone will produce. Raises RuntimeError when gcc is missing or the build fails.
    """
    from tools import exe_cache

    gcc = shutil.which("gcc")
    if gcc is None:
        raise RuntimeError("program preview needs gcc on PATH (it runs the firmware evaluator API/wfb_prog.c)")
    args = ["-std=c99", "-O2", "-IAPI", "tools/wfb_prog_preview.c", "API/wfb_prog.c", "API/wfb_traj.c"]
    with tempfile.TemporaryDirectory() as tmp:
        b = exe_cache.build(gcc, "wfb_prog_preview", args, Path(tmp))
        if not b.ok:
            raise RuntimeError("wfb_prog_preview build failed: " + b.stderr.strip()[-400:])
        text = f"{float(hover_z_m)!r} {float(dt_s)!r}\n" + "".join(
            " ".join(repr(v) for v in s.record()) + "\n" for s in segments)
        res = subprocess.run([str(b.exe)], input=text, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"wfb_prog_preview exit {res.returncode}: {res.stderr.strip()}")
    head, *rest = res.stdout.splitlines()
    _, err, err_seg, t_total = head.split()
    segs = tuple((float(f[2]), float(f[3])) for f in (ln.split() for ln in rest) if f[0] == "seg")
    pts = tuple(tuple(float(v) for v in f[1:]) for f in (ln.split() for ln in rest) if f[0] == "pt")
    return Preview(int(err), int(err_seg), float(t_total), segs, pts)  # type: ignore[arg-type]
