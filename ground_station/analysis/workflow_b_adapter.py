"""Workflow B flight scoring: a captured session -> bench rows -> the bench objective J.

- ``flight_rows(session_dir)`` reads the session's manifest.json and slot CSVs (written by the live
  capture, ground_station/livewatch/campaign_capture.py) and returns one row per segment with the keys
  traj, rmse, rmse_xy, rmse_z (m), sat, n, diverged, computed as sim/bench/bench.py ``metrics`` does.
- ``score(rows)`` is bench ``objective(rows)``, imported rather than copied so a flight and a simulated
  run are scored by the same code.

Alignment: the slot holding the first position feedback is the base timeline. Every other column takes
its nearest sample by host time (``t_host_s``) within MAX_ALIGN_GAP_S, else NaN, so a telemetry gap is
never filled in. A segment covers t0 <= t < t1.

A segment is diverged when it has fewer than MIN_SAMPLES samples or any non-finite tracking error: a
flight the tuner could not see is never scored as good. Like bench, a diverged row has rmse = inf but
still reports rmse_xy and rmse_z. ``sat`` counts finite motor samples only and is NaN when there are
none, which makes J NaN (the tuner treats a non-finite J as unusable).

This module holds no symbol or controller names: the columns come from campaign_capture.
"""
from __future__ import annotations

import csv
import importlib
import math
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np

from ground_station.livewatch.campaign_capture import MOTORS, POSITION_AXES, ManifestError, read_manifest

BENCH_DIR = Path(__file__).resolve().parents[2] / "sim" / "bench"
MAX_ALIGN_GAP_S = 0.1  # farther than this from any sample of a column -> NaN, no interpolation
MIN_SAMPLES = 10  # fewer samples in a segment -> diverged

_COLUMNS = tuple(name for axis in POSITION_AXES for name in (axis.feedback, axis.reference)) + MOTORS


def flight_rows(session_dir: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """One bench-style row per manifest segment, in start-time order (see the module docstring)."""
    session = Path(session_dir)
    manifest = read_manifest(session)
    t_host, columns = _aligned_columns(session, manifest["slots"])
    rows = []
    for segment in manifest["segments"]:
        inside = (t_host >= segment["t0_host_s"]) & (t_host < segment["t1_host_s"])
        rows.append(_segment_row(segment["name"], {name: values[inside] for name, values in columns.items()}))
    return rows


def score(rows: Sequence[Mapping[str, Any]]) -> float:
    """The bench objective J over ``rows`` (sim/bench/bench.py ``objective``); lower is better."""
    if not rows:
        raise ValueError("score needs at least one row")
    return float(_bench().objective(list(rows)))


def _bench() -> ModuleType:
    """sim/bench/bench.py, imported on first use: it loads the plant model (about 6 s cold)."""
    if str(BENCH_DIR) not in sys.path:
        sys.path.insert(0, str(BENCH_DIR))
    return importlib.import_module("bench")


def _segment_row(name: str, columns: Mapping[str, np.ndarray]) -> dict[str, Any]:
    """bench ``metrics`` for one segment: tracking error in metres and motor saturation."""
    error = np.stack(
        [(columns[a.feedback] - columns[a.reference]) * a.to_m for a in POSITION_AXES], axis=1
    )  # (n, 3): x, y, z
    n = len(error)
    diverged = n < MIN_SAMPLES or not np.isfinite(error).all()
    motors = np.concatenate([columns[m] for m in MOTORS])
    motors = motors[np.isfinite(motors)]
    bench = _bench()
    saturated = (motors >= bench.SAT_HI) | (motors <= bench.SAT_LO)
    return {
        "traj": name,
        "rmse": math.inf if diverged else _rms(np.linalg.norm(error, axis=1)),
        "rmse_xy": _rms(np.linalg.norm(error[:, :2], axis=1)),
        "rmse_z": _rms(error[:, 2]),
        "sat": float(saturated.mean()) if motors.size else math.nan,
        "n": n,
        "diverged": diverged,
    }


def _rms(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(values ** 2))) if values.size else math.nan


def _aligned_columns(
    session: Path, slots: Sequence[Mapping[str, Any]]
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Host times of the base slot and every scoring column on that timeline."""
    slot_of = {var: slot for slot in slots for var in slot["vars"]}
    missing = [name for name in _COLUMNS if name not in slot_of]
    if missing:
        raise ManifestError(f"{session}: the session did not record {missing}")
    used = {slot_of[name]["slot"]: slot_of[name] for name in _COLUMNS}  # slot id -> slot, read once each
    tables = {slot_id: _read_slot(session / slot["csv"]) for slot_id, slot in used.items()}
    base_id = slot_of[POSITION_AXES[0].feedback]["slot"]
    t_base = tables[base_id]["t_host_s"]
    columns = {}
    for name in _COLUMNS:
        slot_id = slot_of[name]["slot"]
        table = tables[slot_id]
        if name not in table:
            raise ManifestError(f"{session / used[slot_id]['csv']}: no column {name!r}")
        columns[name] = table[name] if slot_id == base_id else _nearest(t_base, table["t_host_s"], table[name])
    return t_base, columns


def _read_slot(path: Path) -> dict[str, np.ndarray]:
    """One stream_log slot CSV (header ``t_src_ms,t_host_s,seq`` + columns) -> column arrays.

    Empty cells read as NaN. A row with the wrong number of cells (e.g. cut off by a crash) is an error.
    """
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            raise ManifestError(f"{path}: empty file, expected a header row")
        values = []
        for row in reader:
            if len(row) != len(header):
                raise ManifestError(f"{path}:{reader.line_num}: {len(row)} cells, the header has {len(header)}")
            values.append([float(cell) if cell else math.nan for cell in row])
    data = np.array(values, dtype=float).reshape(len(values), len(header))
    return {name: data[:, i] for i, name in enumerate(header)}


def _nearest(t_base: np.ndarray, t_src: np.ndarray, values: np.ndarray) -> np.ndarray:
    """``values`` sampled at ``t_src``, read at ``t_base``: the nearest sample within
    MAX_ALIGN_GAP_S, else NaN."""
    if t_src.size == 0:
        return np.full(t_base.shape, math.nan)
    order = np.argsort(t_src, kind="stable")
    t_src, values = t_src[order], values[order]
    after = np.searchsorted(t_src, t_base)  # first sample at or after each base time
    lo = np.clip(after - 1, 0, t_src.size - 1)
    hi = np.clip(after, 0, t_src.size - 1)
    nearest = np.where(np.abs(t_base - t_src[lo]) <= np.abs(t_src[hi] - t_base), lo, hi)
    within = np.abs(t_src[nearest] - t_base) <= MAX_ALIGN_GAP_S
    return np.where(within, values[nearest], math.nan)
