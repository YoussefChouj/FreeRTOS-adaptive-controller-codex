"""Deterministic core-metrics pipeline for Run analysis.

Per axis: RMSE of tracking error, overshoot, settling time, saturation time,
and dominant spectrum peaks.

Plugin registry: ``@metric("name")`` decorates functions that accept a dict of
data columns (numpy-like arrays or lists) and return a float or None.

Thesis plugin stubs:
* ``u_ad_spike_ratio``  – max |u_ad| / median |u_ad|
* ``w_norm_convergence`` – slope of ||W|| over the last 30 % of the run
* ``gate_saturation``   – fraction of samples outside [0,1] or sum != 1

Each plugin returns None when its required columns are missing.
"""
from __future__ import annotations

import csv
import math
import statistics
import textwrap
from pathlib import Path
from typing import Any, Callable

from .run import Run

# ---------------------------------------------------------------------------
# Plugin registry
# ---------------------------------------------------------------------------

_metric_plugins: dict[str, Callable[..., float | None]] = {}


def metric(name: str) -> Callable[[Callable[..., float | None]],
                                  Callable[..., float | None]]:
    """Register a metric plugin under *name*."""
    def decorator(fn: Callable[..., float | None]) -> Callable[..., float | None]:
        _metric_plugins[name] = fn
        return fn
    return decorator


def list_plugins() -> list[str]:
    return sorted(_metric_plugins.keys())


def _get(data: dict[str, Any], key: str) -> list[float] | None:
    vals = data.get(key)
    if vals is None:
        return None
    if isinstance(vals, list):
        return vals
    if isinstance(vals, str):
        # Assume comma-separated numeric string
        try:
            return [float(v.strip()) for v in vals.split(",")]
        except (ValueError, TypeError):
            return None
    return None

# ---------------------------------------------------------------------------
# Core metrics
# ---------------------------------------------------------------------------


def rmse_of(error: list[float]) -> float:
    """Root-mean-square of a sequence of errors."""
    if not error:
        return 0.0
    return math.sqrt(sum(e * e for e in error) / len(error))


def overshoot(setpoint: list[float], response: list[float]) -> float:
    """Peak overshoot as a fraction of the setpoint step.

    Returns the ratio ``(max(response) - step_size) / step_size`` where
    *step_size* is the final setpoint value minus the initial value.
    """
    if not setpoint or not response or len(setpoint) != len(response):
        return 0.0
    step = setpoint[-1] - setpoint[0]
    if step == 0:
        return 0.0
    peak = max(max(response), 0.0)
    return (peak - step) / step


def settling_time(response: list[float], setpoint: list[float],
                  tolerance: float = 0.02) -> float:
    """Seconds until the response stays within *tolerance* of the setpoint.

    Returns the index of the last sample outside the band, assuming 1 Hz
    sampling (caller can rescale).
    """
    if not response or not setpoint or len(response) != len(setpoint):
        return 0.0
    final = setpoint[-1]
    band = tolerance * abs(final) if final != 0 else 1e-9
    last_out = 0
    for i, r in enumerate(response):
        if abs(r - final) > band:
            last_out = i
    return float(last_out)


def saturation_time(response: list[float],
                    lo: float = -1.0, hi: float = 1.0) -> float:
    """Number of samples where response is outside [lo, hi]."""
    if not response:
        return 0.0
    return sum(1 for r in response if r < lo or r > hi)


def dominant_peaks(signal: list[float], n_peaks: int = 3) -> list[int]:
    """Naive dominant-peak finder by local maxima ranking.

    Returns the indices of the *n_peaks* largest local maxima.
    """
    if len(signal) < 3:
        return []
    magnitudes = [abs(v) for v in signal]
    maxima: list[tuple[float, int]] = []
    for i in range(1, len(magnitudes) - 1):
        if magnitudes[i] > magnitudes[i - 1] and magnitudes[i] > magnitudes[i + 1]:
            maxima.append((magnitudes[i], i))
    maxima.sort(reverse=True)
    return [idx for _, idx in maxima[:n_peaks]]

# ---------------------------------------------------------------------------
# Thesis metric plugins
# ---------------------------------------------------------------------------


@metric("u_ad_spike_ratio")
def u_ad_spike_ratio(data: dict[str, Any]) -> float | None:
    """max |u_ad| / median |u_ad|.  Skips when columns missing."""
    u_ad = _get(data, "u_ad")
    if not u_ad or len(u_ad) < 2:
        return None
    abs_u = [abs(v) for v in u_ad]
    med = statistics.median(abs_u)
    if med == 0:
        return None
    return max(abs_u) / med


@metric("w_norm_convergence")
def w_norm_convergence(data: dict[str, Any]) -> float | None:
    """Slope of ||W|| over the last 30 % of the run (linear regression)."""
    w_norm = _get(data, "w_norm")
    if not w_norm or len(w_norm) < 4:
        return None
    last = int(len(w_norm) * 0.3)
    tail = w_norm[-last:]
    x = list(range(len(tail)))
    slope, _ = _linear_regression(x, tail)
    return slope


@metric("gate_saturation")
def gate_saturation(data: dict[str, Any]) -> float | None:
    """Fraction of samples outside [0,1] or with sum != 1."""
    # Accept either separate columns or a single "gates" list of comma-separated values
    g0 = _get(data, "gate_0")
    g1 = _get(data, "gate_1")
    g2 = _get(data, "gate_2")
    g3 = _get(data, "gate_3")
    if g0 and g1 and g2 and g3 and len(g0) == len(g1) == len(g2) == len(g3):
        n = len(g0)
        bad = 0
        for i in range(n):
            vals = [g0[i], g1[i], g2[i], g3[i]]
            if any(v < 0.0 or v > 1.0 for v in vals):
                bad += 1
                continue
            if abs(sum(vals) - 1.0) > 0.05:
                bad += 1
        return bad / n if n else 0.0

    # Fallback: check each gate column independently
    all_gates = [v for v in [g0, g1, g2, g3] if v is not None]
    if not all_gates:
        return None
    total = 0
    bad = 0
    for vals in all_gates:
        for v in vals:
            total += 1
            if v < 0.0 or v > 1.0:
                bad += 1
    return bad / total if total else 0.0

# ---------------------------------------------------------------------------
# Analysis runner
# ---------------------------------------------------------------------------


def _linear_regression(x: list[float], y: list[float]) -> tuple[float, float]:
    """Return (slope, intercept) via least-squares."""
    n = len(x)
    if n < 2:
        return 0.0, 0.0
    sx = sum(x)
    sy = sum(y)
    sxx = sum(xi * xi for xi in x)
    sxy = sum(xi * yi for xi, yi in zip(x, y))
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0.0, sy / n
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


def analyse_run(run: Run, data: dict[str, Any],
                axis: str = "default") -> dict[str, Any]:
    """Compute core metrics for one axis from *data* columns.

    Returns a dict of metric name -> value (``None`` columns are skipped).
    """
    result: dict[str, Any] = {}
    prefix = f"{axis}_" if axis != "default" else ""

    setpoint = _get(data, prefix + "setpoint")
    response = _get(data, prefix + "response")
    error = _get(data, prefix + "error")

    if error:
        result[f"{prefix}rmse"] = rmse_of(error)
    if setpoint and response:
        result[f"{prefix}overshoot"] = overshoot(setpoint, response)
        result[f"{prefix}settling_time"] = settling_time(setpoint, response)

    sat_signal = _get(data, prefix + "saturation") or response
    if sat_signal:
        result[f"{prefix}saturation_time"] = saturation_time(sat_signal)

    result[f"{prefix}dominant_peaks"] = dominant_peaks(response or [])

    # Thesis plugins (always run on the full data, not just the axis subset)
    for name, fn in _metric_plugins.items():
        try:
            val = fn(data)
            if val is not None:
                result[name] = val
        except Exception:
            pass

    return result


def generate_report(run: Run, metrics: dict[str, Any],
                    run_dir: Path) -> str:
    """Write ``report.md`` into *run_dir* and return its path as a string."""
    lines = [
        f"# Analysis Report — {run.id}",
        "",
        f"**Kind:** {run.kind}",
        f"**Phase:** {run.phase}",
        f"**Hypothesis:** {run.hypothesis}",
        "",
        "## Metrics",
        "",
    ]
    for name, value in sorted(metrics.items()):
        if isinstance(value, float):
            lines.append(f"- `{name}`: {value:.6f}")
        elif isinstance(value, list):
            lines.append(f"- `{name}`: {value}")
        else:
            lines.append(f"- `{name}`: {value}")
    lines.append("")
    lines.append("## Events")
    lines.append("")
    for evt in run.events:
        lines.append(f"- t={evt.get('t', '?')} kind={evt.get('kind', '?')} "
                      f"detail={evt.get('detail', '')}")
    lines.append("")

    report_path = run_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return str(report_path)

# ---------------------------------------------------------------------------
# Convenience: load data from CSV
# ---------------------------------------------------------------------------


def load_csv_columns(path: str | Path) -> dict[str, list[float]]:
    """Read a CSV and return {column_name: [float, ...]}, skipping headers."""
    path = Path(path)
    result: dict[str, list[float]] = {}
    if not path.exists():
        return result
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            for key, val in row.items():
                if key not in result:
                    result[key] = []
                try:
                    result[key].append(float(val))
                except (ValueError, TypeError):
                    result[key].append(0.0)
    return result
