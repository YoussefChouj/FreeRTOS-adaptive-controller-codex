"""RPM derivation from raw period/edge telemetry.

Firmware source:
  BSP/rpm.c lines 34-35 declare rpm_dbg_period_cyc[] and rpm_dbg_edges[].
  BSP/rpm.h line 31:  RPM_PULSES_PER_REV = 2U   (2 pulses per mechanical rev).
  BSP/rpm.h line 60:  RPM_TIMEOUT_CYCLES = SystemCoreClock / 2  (0.5 s).
  BSP/rpm.h line 82:  "RPM = 60*SystemCoreClock/this" for rpm_dbg_period_cyc.

Conversion formula (verified against BSP/rpm.c:184-185 and rpm.h:82):

    RPM = 60 * SystemCoreClock / period_cyc
        = 60 * 168_000_000 / period_cyc

  period_cyc is the DWT-cycle period of one *full revolution*
  (the ISR accumulates RPM_PULSES_PER_REV = 2 edges before computing a period).

  Clock source: STM32F407 DWT cycle counter at SystemCoreClock = 168 MHz.
"""
from __future__ import annotations

import math
import statistics
from typing import Any


# SystemCoreClock for STM32F407 DWT counter (168 MHz).
# Source: BSP/rpm.h:60, BSP/rpm.c:185 comment, BSP/rpm.h:82.
_SYSTEM_CORE_CLOCK_HZ = 168_000_000

# Pulses per mechanical revolution.
# Source: BSP/rpm.h:31  RPM_PULSES_PER_REV = 2U.
_PULSES_PER_REV = 2


def compute_rpm(
    period_cyc_list: list[float],
    edge_list: list[float],
    hold_window: int = 3,
) -> list[float]:
    """Convert raw period/edge telemetry to RPM values.

    Parameters
    ----------
    period_cyc_list : list[float]
        Per-sample ``rpm_dbg_period_cyc`` values (DWT cycles per revolution).
    edge_list : list[float]
        Per-sample ``rpm_dbg_edges`` values (monotonic counter).
    hold_window : int
        Number of consecutive samples with no edge growth before declaring
        the motor *stale* (RPM set to NaN).  The telemetry rate is ~50 Hz
        so 3 samples = 60 ms.

    Returns
    -------
    list[float]
        Same-length list of RPM values.  NaN where period is 0 or the edge
        counter has not increased for ``hold_window`` consecutive samples.
    """
    result: list[float] = []
    consecutive_stale = 0
    prev_edges: float | None = None

    for period_cyc, edges in zip(period_cyc_list, edge_list):
        # Zero period => division would overflow => NaN.
        if period_cyc <= 0:
            result.append(float("nan"))
            consecutive_stale = 0
            prev_edges = edges if edges > 0 else prev_edges
            continue

        # Stale detection: edge counter not increasing.
        is_stale = False
        if prev_edges is not None and edges <= prev_edges:
            consecutive_stale += 1
            if consecutive_stale >= hold_window:
                is_stale = True
        else:
            consecutive_stale = 0

        if is_stale:
            result.append(float("nan"))
        else:
            # RPM = 60 * clock / period_cyc
            # Source: BSP/rpm.h:82  "RPM = 60*SystemCoreClock/this"
            # Note: period_cyc is already a full-revolution period (ISR
            #        accumulates RPM_PULSES_PER_REV edges before measuring),
            #        so no additional division by pulses is needed.
            rpm = (60.0 * _SYSTEM_CORE_CLOCK_HZ) / period_cyc
            result.append(rpm)

        prev_edges = edges if edges > 0 else prev_edges

    return result


def rpm_metrics(
    rpm_values: list[float],
) -> dict[str, Any]:
    """Compute summary metrics for one motor's RPM series.

    Returns dict with keys: mean, std, max, stale_fraction.
    """
    import statistics

    valid = [v for v in rpm_values if not math.isnan(v)]
    stale_count = len(rpm_values) - len(valid)

    m: dict[str, Any] = {}
    if valid:
        m["mean"] = round(statistics.mean(valid), 4)
        m["std"] = round(statistics.stdev(valid), 4) if len(valid) >= 2 else 0.0
        m["max"] = round(max(valid), 4)
    else:
        m["mean"] = 0.0
        m["std"] = 0.0
        m["max"] = 0.0

    total = len(rpm_values) if rpm_values else 1
    m["stale_fraction"] = round(stale_count / total, 6)
    return m


def asymmetry_index(
    motor_means: dict[int, float],
) -> float | None:
    """(max(mean_i) - min(mean_i)) / mean(mean_i) across motors.

    Returns None if fewer than 2 motors have valid means.
    """
    means = [v for v in motor_means.values() if v > 0]
    if len(means) < 2:
        return None
    min_m = min(means)
    max_m = max(means)
    overall_mean = statistics.mean(means)
    if overall_mean == 0:
        return 0.0
    return round((max_m - min_m) / overall_mean, 6)
