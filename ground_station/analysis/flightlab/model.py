"""Flightlab data model (spec section 4). Every loader produces a FlightLog; every plugin reads one.

Owned by the supervisor (WP0). Workers must not edit this file.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True, eq=False)
class Signal:
    name: str          # firmware var name, e.g. "Ctrler.gyroxPID.FB"
    t: np.ndarray      # float64 seconds, (t_src_ms - log.t0_src_ms) / 1000, ascending
    v: np.ndarray      # float64 values, NaN where the CSV cell is empty/unparseable
    rate_hz: float     # nominal slot rate from meta.preset.slots[i].rate
    slot: int

    def __len__(self) -> int:
        return int(self.t.size)


@dataclass
class SlotInfo:
    index: int
    rate_hz: float
    n_rows: int
    duration_s: float
    rate_measured_hz: float
    seq_drops: int
    tsrc_gaps: int
    drop_pct: float
    dt_median_ms: float
    dt_p99_ms: float
    dt_max_ms: float
    tsrc_backsteps: int
    host_latency_std_ms: float
    vars: list[str] = field(default_factory=list)


@dataclass
class FlightLog:
    name: str
    source_format: str
    source_paths: list[str]
    meta: dict
    t0_src_ms: float            # min t_src_ms over all slots
    duration_s: float
    signals: dict[str, Signal]
    slots: list[SlotInfo]

    def has(self, name: str) -> bool:
        return name in self.signals

    def get(self, name: str) -> Signal:
        return self.signals[name]   # KeyError if absent

    def find(self, pattern: str) -> list[str]:
        """fnmatch (case-sensitive) over signal names, sorted. '[' starts a character class:
        match 'Theta[0]' with the pattern 'Theta[[]0]' or use a trailing '*'."""
        return sorted(n for n in self.signals if fnmatch.fnmatchcase(n, pattern))

    def window(self, name: str, t0: float, t1: float) -> Signal:
        """Samples with t0 <= t < t1."""
        s = self.get(name)
        m = (s.t >= t0) & (s.t < t1)
        return Signal(s.name, s.t[m], s.v[m], s.rate_hz, s.slot)

    def aligned(self, names, rate_hz=None, t0=None, t1=None) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """Linear interpolation of `names` onto one uniform grid.

        Grid: t0 + k / rate_hz for t0 <= t < t1. Defaults: rate = min nominal rate of the inputs,
        t0 = earliest first sample, t1 = latest last sample (included). Each output is NaN outside
        its own signal's [first, last] sample span. NaN input samples make the adjacent grid
        points NaN (no gap filling).
        """
        names = list(names)
        if not names:
            raise ValueError("aligned() needs at least one name")
        sigs = [self.get(n) for n in names]
        rate = float(rate_hz) if rate_hz is not None else min(s.rate_hz for s in sigs)
        if rate <= 0:
            raise ValueError("rate_hz must be > 0")
        nonempty = [s for s in sigs if s.t.size]
        if t0 is None:
            t0 = min(s.t[0] for s in nonempty) if nonempty else 0.0
        if t1 is None:
            t1 = (max(s.t[-1] for s in nonempty) if nonempty else t0) + 0.5 / rate
        n = max(int(np.ceil((t1 - t0) * rate - 1e-9)), 0)
        grid = t0 + np.arange(n, dtype=np.float64) / rate
        out: dict[str, np.ndarray] = {}
        for name, s in zip(names, sigs):
            if s.t.size == 0:
                out[name] = np.full(n, np.nan)
                continue
            out[name] = np.interp(grid, s.t, s.v, left=np.nan, right=np.nan)
        return grid, out
