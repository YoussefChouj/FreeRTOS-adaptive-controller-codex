"""VOFA log loader (spec section 4, Contract A).

Reads <stem>.meta.json and its associated <stem>.slot<i>.csv files.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from ..model import FlightLog, Signal, SlotInfo
from . import LoadError


_RPM_DBG_RE = re.compile(r"^rpm_dbg_period_cyc\[\d+\]$")


def load_vofa(meta_path: Path | str, gap_factor: float = 1.5) -> FlightLog:
    meta_path = Path(meta_path)
    if not meta_path.exists():
        raise LoadError(f"{meta_path.name}: file not found")

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise LoadError(f"{meta_path.name}: failed to read JSON: {exc}") from exc

    if not isinstance(meta, dict):
        raise LoadError(f"{meta_path.name}: missing preset in meta")
    if isinstance(meta.get("preset"), dict):            # VOFA Studio recorder
        slots_meta = meta["preset"].get("slots")
    elif isinstance(meta.get("slots"), dict):           # dashboard Streams logger: {"0": {...}, ...}
        slots_meta = [meta["slots"][k] for k in sorted(meta["slots"], key=int)]
    else:
        raise LoadError(f"{meta_path.name}: missing preset in meta")

    if not isinstance(slots_meta, list) or len(slots_meta) == 0:
        raise LoadError(f"{meta_path.name}: no slots in preset")

    stem = meta_path.name[:-len(".meta.json")] if meta_path.name.endswith(".meta.json") else meta_path.stem

    slot_dfs: list[pd.DataFrame | None] = []
    slot_rates: list[float] = []
    csv_paths: list[Path] = []
    slots_info: list[SlotInfo] = []
    empty_slots: list[int] = []
    masked: dict[str, int] = {}

    for i, sm in enumerate(slots_meta):
        csv = meta_path.parent / f"{stem}.slot{i}.csv"
        csv_paths.append(csv)
        if not csv.exists():
            raise LoadError(f"{csv.name}: missing")

        try:
            df = pd.read_csv(csv, dtype=str, keep_default_na=False)
        except pd.errors.EmptyDataError:
            # Header-only or completely empty: treat as empty slot
            df = pd.DataFrame()
        except OSError as exc:
            raise LoadError(f"{csv.name}: read error: {exc}") from exc

        # Check required columns exist (if any rows)
        if len(df) > 0:
            for col in ("t_src_ms", "t_host_s", "seq"):
                if col not in df.columns:
                    raise LoadError(f"{csv.name}: missing required column {col}")

        # Handle header-only / no-data case
        if len(df) == 0:
            var_cols = [v for v in sm.get("vars", [])]
            rate_hz = float(sm.get("rate", 0.0))
            slot_rates.append(rate_hz)
            empty_df = pd.DataFrame(columns=["t_src_ms", "t_host_s", "seq"] + var_cols)
            slot_dfs.append(None)  # mark as empty
            empty_slots.append(i)
            slots_info.append(
                SlotInfo(
                    index=i,
                    rate_hz=rate_hz,
                    n_rows=0,
                    duration_s=0.0,
                    rate_measured_hz=0.0,
                    seq_drops=0,
                    tsrc_gaps=0,
                    drop_pct=0.0,
                    dt_median_ms=float("nan"),
                    dt_p99_ms=float("nan"),
                    dt_max_ms=float("nan"),
                    tsrc_backsteps=0,
                    host_latency_std_ms=0.0,
                    vars=var_cols,
                )
            )
            continue

        # Coerce all columns to numeric, empty / unparseable -> NaN
        for col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(np.float64)

        # Drop rows whose t_src_ms is NaN
        df = df.dropna(subset=["t_src_ms"]).reset_index(drop=True)
        if len(df) == 0:
            var_cols = [c for c in df.columns if c not in ("t_src_ms", "t_host_s", "seq")]
            rate_hz = float(sm.get("rate", 0.0))
            slot_rates.append(rate_hz)
            slot_dfs.append(None)
            empty_slots.append(i)
            slots_info.append(
                SlotInfo(
                    index=i,
                    rate_hz=rate_hz,
                    n_rows=0,
                    duration_s=0.0,
                    rate_measured_hz=0.0,
                    seq_drops=0,
                    tsrc_gaps=0,
                    drop_pct=0.0,
                    dt_median_ms=float("nan"),
                    dt_p99_ms=float("nan"),
                    dt_max_ms=float("nan"),
                    tsrc_backsteps=0,
                    host_latency_std_ms=0.0,
                    vars=var_cols,
                )
            )
            continue

        # Mask ano_of.of_alt_cm sentinel (4294967295 = 0xFFFFFFFF)
        if "ano_of.of_alt_cm" in df.columns:
            sentinel_mask = df["ano_of.of_alt_cm"] == 4294967295
            count = int(sentinel_mask.sum())
            if count > 0:
                df.loc[sentinel_mask, "ano_of.of_alt_cm"] = np.nan
                masked["ano_of.of_alt_cm"] = count

        # Mask rpm_dbg_period_cyc[N] spikes (> 5x median of finite values)
        for col in df.columns:
            if _RPM_DBG_RE.match(col):
                finite_vals = df[col].dropna()
                if len(finite_vals) > 0:
                    med = float(finite_vals.median())
                    if med > 0:
                        spike_mask = df[col] > 5.0 * med
                        count = int(spike_mask.sum())
                        if count > 0:
                            df.loc[spike_mask, col] = np.nan
                            masked[col] = count

        rate_hz = float(sm.get("rate", 0.0))
        slot_rates.append(rate_hz)

        # Compute SlotInfo in file row order (before sorting)
        t_src_ms = df["t_src_ms"].to_numpy(dtype=np.float64)
        t_host_s = df["t_host_s"].to_numpy(dtype=np.float64)
        seq = df["seq"].to_numpy(dtype=np.float64)
        n_rows = int(len(df))

        dur_s = float((np.max(t_src_ms, axis=0) - np.min(t_src_ms, axis=0)) / 1000.0) if n_rows > 0 else 0.0
        rate_meas = float((n_rows - 1) / dur_s) if dur_s > 0.0 else 0.0

        dt = np.diff(t_src_ms)
        if n_rows >= 2:
            valid_seq = np.isfinite(seq[:-1]) & np.isfinite(seq[1:])
            diffs = np.mod(np.round(seq[1:][valid_seq] - seq[:-1][valid_seq]).astype(np.int64), 256)
            drops = np.where(diffs >= 1, diffs - 1, 0)
            seq_drops = int(np.sum(drops, axis=0))
            tsrc_gaps = int(np.sum(dt > (gap_factor * 1000.0 / rate_hz), axis=0)) if rate_hz > 0 else 0
            dt_median_ms = float(np.median(dt))
            dt_p99_ms = float(np.percentile(dt, 99))
            dt_max_ms = float(np.max(dt, axis=0))
            tsrc_backsteps = int(np.sum(dt < 0, axis=0))
        else:
            seq_drops = 0
            tsrc_gaps = 0
            dt_median_ms = float("nan")  # undefined with < 2 samples -> null in JSON
            dt_p99_ms = float("nan")
            dt_max_ms = float("nan")
            tsrc_backsteps = 0

        drop_pct = float(100.0 * seq_drops / (n_rows + seq_drops)) if (n_rows + seq_drops) > 0 else 0.0

        latency_diff = t_host_s * 1000.0 - t_src_ms
        finite_latency = latency_diff[np.isfinite(latency_diff)]
        host_latency_std_ms = float(np.std(finite_latency, ddof=0, axis=0)) if finite_latency.size > 0 else 0.0

        var_cols = [c for c in df.columns if c not in ("t_src_ms", "t_host_s", "seq")]

        slots_info.append(
            SlotInfo(
                index=i,
                rate_hz=rate_hz,
                n_rows=n_rows,
                duration_s=dur_s,
                rate_measured_hz=rate_meas,
                seq_drops=seq_drops,
                tsrc_gaps=tsrc_gaps,
                drop_pct=drop_pct,
                dt_median_ms=dt_median_ms,
                dt_p99_ms=dt_p99_ms,
                dt_max_ms=dt_max_ms,
                tsrc_backsteps=tsrc_backsteps,
                host_latency_std_ms=host_latency_std_ms,
                vars=var_cols,
            )
        )
        slot_dfs.append(df)

    # All slots empty -> still raise
    non_empty_dfs = [df for df in slot_dfs if df is not None]
    if not non_empty_dfs:
        raise LoadError(f"{stem}: no data rows")

    t0_src_ms = float(min(float(np.min(df["t_src_ms"], axis=0)) for df in non_empty_dfs))
    max_t_src_ms = float(max(float(np.max(df["t_src_ms"], axis=0)) for df in non_empty_dfs))
    total_duration_s = float((max_t_src_ms - t0_src_ms) / 1000.0)

    signals: dict[str, Signal] = {}
    for i, (df, rate_hz) in enumerate(zip(slot_dfs, slot_rates)):
        if df is None:
            continue  # skip empty slots for signal extraction

        t_src = df["t_src_ms"].to_numpy(dtype=np.float64)
        order = np.argsort(t_src, kind="stable")
        t_sig = (t_src[order] - t0_src_ms) / 1000.0
        t_host = df["t_host_s"].to_numpy(dtype=np.float64)[order]

        # Add internal host time signal
        signals[f"__t_host_s.slot{i}"] = Signal(
            name=f"__t_host_s.slot{i}",
            t=t_sig,
            v=t_host,
            rate_hz=rate_hz,
            slot=i,
        )

        var_cols = [c for c in df.columns if c not in ("t_src_ms", "t_host_s", "seq")]
        for var in var_cols:
            v_var = df[var].to_numpy(dtype=np.float64)[order]
            sig = Signal(name=var, t=t_sig, v=v_var, rate_hz=rate_hz, slot=i)
            if var not in signals:
                signals[var] = sig
            else:
                existing = signals[var]
                if sig.rate_hz > existing.rate_hz:
                    signals[var] = sig

    source_paths = [str(meta_path)] + [str(p) for p in csv_paths]

    # Enrich meta with masking and empty slot info
    meta["masked"] = {k: v for k, v in masked.items() if v > 0}
    meta["empty_slots"] = empty_slots

    return FlightLog(
        name=stem,
        source_format="vofa",
        source_paths=source_paths,
        meta=meta,
        t0_src_ms=t0_src_ms,
        duration_s=total_duration_s,
        signals=signals,
        slots=slots_info,
    )
