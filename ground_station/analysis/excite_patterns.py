"""Host mirror of the firmware excitation pattern library (API/excite.c, CMD 0x2E).

24 rows = 6 axes x 2 layers (outer setpoint, inner rate) x 2 speeds. Each row is a Schroeder multisine on
harmonics axis+1+6*(j0+j) of f0 = 1/T, so the six axes never share a frequency and slow/fast rows of one axis
never overlap. ROWS here must equal the EXCITE_ROW table in API/excite.c (tests parse it). signal() reproduces
the firmware output tick by tick (same peak grid, same fade, same clamp), so analysis can rebuild the injected
input from (layer, speed, mask, n_periods) alone. Spec: docs/design/2026-10-22-mrac-experiment-stack.md item 1.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

CMD_EXCITE = 0x2E
IDX_LAYER, IDX_SPEED, IDX_MASK, IDX_PERIODS, IDX_GO = 0, 1, 2, 3, 4

DT_S = 0.005          # EXCITE_DT
FADE_S = 1.0          # EXCITE_FADE_S
PEAK_GRID = 512       # EXC_PEAK_GRID
MAX_PERIODS = 10
ALT_BAND_M = (0.30, 1.50)
SOFT_XY_CM = 50.0

AXES = ("x", "y", "z", "yaw", "pitch", "roll")      # Excite_Axis_e order, mask bit = index
LAYERS = ("outer", "inner")
SPEEDS = ("slow", "fast")
STATES = ("idle", "running")
REASONS = ("ok", "args", "disarmed", "mode", "ground", "rc", "alt", "xy", "angle", "sysid", "cmd", "done")


@dataclass(frozen=True)
class Row:
    axis: int
    layer: int
    speed: int
    T_s: float
    j0: int
    nh: int
    amp: float
    clamp: float


def _r(axis, layer, speed, T_s, j0, nh, amp, clamp) -> Row:
    return Row(AXES.index(axis), LAYERS.index(layer), SPEEDS.index(speed), T_s, j0, nh, amp, clamp)


# Same order as s_rows[] in API/excite.c. All values PROPOSED (10-22 spec), none measured.
ROWS: tuple[Row, ...] = (
    _r("x", "outer", "slow", 40.0, 0, 4, 15.0, 30.0),
    _r("y", "outer", "slow", 40.0, 0, 4, 15.0, 30.0),
    _r("z", "outer", "slow", 40.0, 0, 4, 0.10, 0.20),
    _r("yaw", "outer", "slow", 40.0, 0, 4, 15.0, 30.0),
    _r("pitch", "outer", "slow", 40.0, 0, 4, 3.0, 8.0),
    _r("roll", "outer", "slow", 40.0, 0, 4, 3.0, 8.0),
    _r("x", "inner", "slow", 10.0, 0, 5, 15.0, 30.0),
    _r("y", "inner", "slow", 10.0, 0, 5, 15.0, 30.0),
    _r("z", "inner", "slow", 10.0, 0, 5, 0.15, 0.40),
    _r("yaw", "inner", "slow", 10.0, 0, 5, 30.0, 60.0),
    _r("pitch", "inner", "slow", 10.0, 0, 5, 30.0, 90.0),
    _r("roll", "inner", "slow", 10.0, 0, 5, 30.0, 90.0),
    _r("x", "outer", "fast", 40.0, 4, 4, 8.0, 30.0),
    _r("y", "outer", "fast", 40.0, 4, 4, 8.0, 30.0),
    _r("z", "outer", "fast", 40.0, 4, 4, 0.05, 0.20),
    _r("yaw", "outer", "fast", 40.0, 4, 4, 8.0, 30.0),
    _r("pitch", "outer", "fast", 40.0, 4, 4, 2.0, 8.0),
    _r("roll", "outer", "fast", 40.0, 4, 4, 2.0, 8.0),
    _r("x", "inner", "fast", 10.0, 5, 5, 10.0, 30.0),
    _r("y", "inner", "fast", 10.0, 5, 5, 10.0, 30.0),
    _r("z", "inner", "fast", 10.0, 5, 5, 0.10, 0.40),
    _r("yaw", "inner", "fast", 10.0, 5, 5, 20.0, 60.0),
    _r("pitch", "inner", "fast", 10.0, 5, 5, 20.0, 90.0),
    _r("roll", "inner", "fast", 10.0, 5, 5, 20.0, 90.0),
)

_ENUM = {f"EXC_{n.upper()}": i for i, n in enumerate(AXES)}
_ENUM.update({"EXC_OUTER": 0, "EXC_INNER": 1, "EXC_SLOW": 0, "EXC_FAST": 1})
_ROW_RE = re.compile(r"^\s*EXCITE_ROW\(([^)]*)\)", re.M)


def parse_c_rows(path: Path) -> tuple[Row, ...]:
    """EXCITE_ROW(...) lines of API/excite.c (the #define line has no EXC_ names and is skipped)."""
    out = []
    for m in _ROW_RE.finditer(path.read_text(encoding="utf-8")):
        f = [s.strip().rstrip("f") for s in m.group(1).split(",")]
        if f[0] not in _ENUM:
            continue
        out.append(Row(_ENUM[f[0]], _ENUM[f[1]], _ENUM[f[2]], float(f[3]), int(f[4]), int(f[5]),
                       float(f[6]), float(f[7])))
    return tuple(out)


def find(axis: int, layer: int, speed: int) -> Row | None:
    for r in ROWS:
        if (r.axis, r.layer, r.speed) == (axis, layer, speed):
            return r
    return None


def harmonics(r: Row) -> list[int]:
    return [r.axis + 1 + 6 * (r.j0 + j) for j in range(r.nh)]


def freqs_hz(r: Row) -> list[float]:
    return [h / r.T_s for h in harmonics(r)]


def n_period(r: Row) -> int:
    return int(r.T_s / DT_S + 0.5)


def raw(r: Row, m: np.ndarray, n_p: int) -> np.ndarray:
    """Unit-amplitude Schroeder sum at samples m of a period of n_p samples (exc_raw)."""
    m = np.asarray(m, dtype=np.int64)
    s = np.zeros(m.shape, dtype=np.float64)
    for j, h in enumerate(harmonics(r)):
        k = (h * m) % n_p
        s += np.cos(2.0 * math.pi * k / n_p - math.pi * j * (j + 1) / r.nh)
    return s


def gain(r: Row) -> float:
    n_p = n_period(r)
    grid = (np.arange(PEAK_GRID, dtype=np.int64) * n_p) // PEAK_GRID
    peak = float(np.max(np.abs(raw(r, grid, n_p))))
    return r.amp / peak if peak > 0.0 else 0.0


def envelope(n: np.ndarray, n_total: int) -> np.ndarray:
    t = np.asarray(n, dtype=np.float64) * DT_S
    left = (n_total - np.asarray(n, dtype=np.float64)) * DT_S
    env = np.ones_like(t)
    head = t < FADE_S
    tail = ~head & (left < FADE_S)
    env[head] = 0.5 - 0.5 * np.cos(math.pi * t[head] / FADE_S)
    env[tail] = 0.5 - 0.5 * np.cos(math.pi * left[tail] / FADE_S)
    return env


def signal(layer: int, speed: int, mask: int, n_periods: int) -> dict[str, np.ndarray]:
    """Per-axis excite_out for the whole run, tick n = 0 .. n_total-1 (the firmware's Excite_Update output)."""
    check_args(layer, speed, mask, n_periods)
    rows = {a: find(a, layer, speed) for a in range(len(AXES)) if mask & (1 << a)}
    n_p = n_period(next(iter(rows.values())))
    n_total = n_p * n_periods
    n = np.arange(n_total, dtype=np.int64)
    env = envelope(n, n_total)
    out = {}
    for a, r in rows.items():
        u = env * gain(r) * raw(r, n % n_p, n_p)
        out[AXES[a]] = np.clip(u, -r.clamp, r.clamp)
    return out


def check_args(layer: int, speed: int, mask: int, n_periods: int) -> None:
    if layer not in (0, 1) or speed not in (0, 1):
        raise ValueError(f"layer {layer} / speed {speed} must be 0 or 1")
    if not 1 <= mask < (1 << len(AXES)):
        raise ValueError(f"mask {mask} must be 1..63")
    if not 1 <= n_periods <= MAX_PERIODS:
        raise ValueError(f"n_periods {n_periods} must be 1..{MAX_PERIODS}")


def mask_of(axes) -> int:
    return sum(1 << AXES.index(a) for a in axes)


def period_s(layer: int, speed: int) -> float:
    return next(r.T_s for r in ROWS if (r.layer, r.speed) == (layer, speed))


def active_s(layer: int, speed: int, n_periods: int) -> float:
    return period_s(layer, speed) * n_periods


def start_commands(layer: int, speed: int, mask: int, n_periods: int) -> list[tuple[int, float]]:
    """(idx, val) pairs for CMD_EXCITE; the last one starts. Read excite_state back: the link is lossy."""
    check_args(layer, speed, mask, n_periods)
    return [(IDX_LAYER, float(layer)), (IDX_SPEED, float(speed)), (IDX_MASK, float(mask)),
            (IDX_PERIODS, float(n_periods)), (IDX_GO, 1.0)]


ABORT_COMMAND: tuple[int, float] = (IDX_GO, 0.0)
