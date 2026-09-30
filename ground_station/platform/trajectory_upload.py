"""Trajectory uploader for Workflow B autonomous tuning flights (Task G4).

Uploads trajectory points to the drone flight controller over CMD 0x1B,
verifying integrity via CRC32 and handling retries if frames or commits
are rejected. Pure logic, standard library only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from ground_station.platform.wfb_commands import WfbClient
from ground_station.service.trajectory_pipeline import TrajPoint, crc32

# interfaces.md WFB_TRAJ_MAX_POINTS
TRAJ_MAX_POINTS: int = 600


@dataclass(frozen=True)
class UploadResult:
    ok: bool
    attempts: int  # attempts made (0 when a pre-check refused before sending anything)
    error: str | None  # None when ok, else the last failure text


def upload(
    points: Sequence[TrajPoint],
    client: WfbClient,
    attempts: int = 3,
) -> UploadResult:
    """Upload trajectory points to the drone via CMD 0x1B with retry.

    Args:
        points: Sequence of trajectory points to upload.
        client: WfbClient connected to drone command transport.
        attempts: Maximum number of upload attempts (must be >= 1).

    Returns:
        UploadResult indicating success/failure, number of attempts, and error text.

    Raises:
        ValueError: If attempts < 1.
    """
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise ValueError(f"attempts must be an integer >= 1, got {attempts!r}")

    n = len(points)
    if n < 2 or n > TRAJ_MAX_POINTS:
        return UploadResult(ok=False, attempts=0, error="count")

    for pt in points:
        if not (
            math.isfinite(pt.x)
            and math.isfinite(pt.y)
            and math.isfinite(pt.z)
            and math.isfinite(pt.yaw_deg)
            and math.isfinite(pt.t)
        ):
            return UploadResult(ok=False, attempts=0, error="nonfinite")

    crc = crc32(points if isinstance(points, list) else list(points))

    last_error: str | None = None
    for attempt in range(1, attempts + 1):
        if not client.traj_begin(n):
            last_error = "begin rejected"
            continue

        append_failed = False
        k = 0
        for pt in points:
            for v in (pt.x, pt.y, pt.z, pt.yaw_deg, pt.t):
                if not client.traj_append(v):
                    last_error = f"append rejected at float {k}"
                    append_failed = True
                    break
                k += 1
            if append_failed:
                break

        if append_failed:
            continue

        if not client.traj_commit(crc):
            last_error = "commit rejected"
            continue

        return UploadResult(ok=True, attempts=attempt, error=None)

    return UploadResult(ok=False, attempts=attempts, error=last_error)
