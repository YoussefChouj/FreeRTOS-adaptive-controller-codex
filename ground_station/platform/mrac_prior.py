"""CMD 0x2F..0x34 MRAC_PRIOR: load the MRAC L3 prior (Theta_prior, sigma_prior, pull / warm bits) over WiFi, disarmed.

One id per axis, 0x2F + MRAC_Axis_e (pitch, roll, yaw, z, x, y). INDEX 0..n_features-1 writes Theta_prior[axis][idx],
0xF0 sigma_prior (shared), 0xF1 / 0xF2 this axis's bit of mrac_prior_pull / mrac_prior_warm (value >= 0.5 sets it).
The firmware refuses the whole range unless disarmed (interlock 6) and a bad idx or value with 0x41, nothing written;
every accepted write re-seeds the warm axes at the next disarmed tick (spec docs/design/2026-10-22-mrac-experiment-stack.md
item 3). Writes are idempotent, so a write whose outcome is unknown on the lossy link is safe to resend.

    prior = prior_from_columns(df, last_n=500)            # median of the last 500 Theta samples of a previous flight
    steps = plan(prior, sigma=0.5, pull=("pitch", "roll"), warm=("pitch", "roll"))
    failed = send(client, steps)                          # WfbClient; [] when every write was APPLIED

APPLIED is the handler's own outcome; the seeded Theta reads back in the next flight's telemetry
(mrac_state.<axis>.Theta[i]), not here.
"""

from __future__ import annotations

import math
from typing import Iterable, Mapping, Sequence

import numpy as np

CMD_BASE: int = 0x2F
AXES: tuple[str, ...] = ("pitch", "roll", "yaw", "z", "x", "y")   # MRAC_Axis_e order (API/mrac.h)
IDX_SIGMA: int = 0xF0
IDX_PULL: int = 0xF1
IDX_WARM: int = 0xF2
# MRAC_N_FEATURES of the MULTI build (= ground_station/analysis/mrac_xt.py N_FEATURES); the firmware checks its own.
N_FEATURES: int = 46


class PriorError(ValueError):
    """A prior write the firmware would refuse; nothing was sent."""


def cmd_for(axis: str) -> int:
    if axis not in AXES:
        raise PriorError(f"axis {axis!r} is not one of {', '.join(AXES)}")
    return CMD_BASE + AXES.index(axis)


def check(axis: str, idx: int, value: float, n_features: int = N_FEATURES) -> tuple[int, int, float]:
    """(cmd, idx, value) for one write, or PriorError on what Cmd_MracPrior would REJECT with 0x41."""
    cmd = cmd_for(axis)
    value = float(value)
    if not math.isfinite(value):
        raise PriorError(f"{axis} idx {idx:#04x}: value {value} is not finite")
    if idx == IDX_SIGMA:
        if value < 0.0:
            raise PriorError(f"sigma_prior {value} < 0")
    elif idx not in (IDX_PULL, IDX_WARM) and not 0 <= idx < n_features:
        raise PriorError(f"{axis} idx {idx} outside 0..{n_features - 1} and not 0xF0..0xF2")
    return cmd, idx, value


def prior_from_columns(cols: Mapping[str, Sequence[float]], last_n: int,
                       n_features: int = N_FEATURES) -> dict[str, list[float]]:
    """Per axis, the median of the last `last_n` finite samples of each mrac_state.<axis>.Theta[i] column.

    Takes a DataFrame or any column mapping. An axis with no Theta column is left out; a missing or all-NaN
    feature column of a present axis is 0.0 (the boot prior)."""
    out: dict[str, list[float]] = {}
    for axis in AXES:
        names = [f"mrac_state.{axis}.Theta[{i}]" for i in range(n_features)]
        if not any(n in cols for n in names):
            continue
        row = []
        for n in names:
            x = np.asarray(cols[n], dtype=float) if n in cols else np.empty(0)
            x = x[np.isfinite(x)][-last_n:]
            row.append(float(np.median(x)) if x.size else 0.0)
        out[axis] = row
    return out


def plan(prior: Mapping[str, Sequence[float]], sigma: float | None = None, pull: Iterable[str] | None = None,
         warm: Iterable[str] | None = None, n_features: int = N_FEATURES) -> list[tuple[int, int, float]]:
    """The writes, all checked: elements, then sigma, then every axis's pull bit, then every warm bit last, so the
    final re-seed sees the whole prior. pull / warm = the axes whose bit is set (None leaves the bits alone)."""
    steps = []
    for axis, row in prior.items():
        if len(row) > n_features:
            raise PriorError(f"{axis}: {len(row)} values > {n_features} features")
        steps += [check(axis, i, v, n_features) for i, v in enumerate(row)]
    if sigma is not None:
        steps.append(check(AXES[0], IDX_SIGMA, sigma, n_features))
    for idx, axes in ((IDX_PULL, pull), (IDX_WARM, warm)):
        if axes is None:
            continue
        on = set(axes)
        for a in on:
            cmd_for(a)
        steps += [check(a, idx, 1.0 if a in on else 0.0, n_features) for a in AXES]
    return steps


def send(client, steps: Iterable[tuple[int, int, float]]) -> list[tuple[int, int, float]]:
    """Send each write through client.mrac_prior; the steps that were not APPLIED (refused or unknown)."""
    return [(cmd, idx, v) for cmd, idx, v in steps if not client.mrac_prior(AXES[cmd - CMD_BASE], idx, v)]
