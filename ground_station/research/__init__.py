"""Research platform: run records, index, deterministic analysis."""
from __future__ import annotations

from .run import Run, Event, Note
from .store import Store, UAV_RUNS_DIR
from .analysis import (
    analyse_run,
    generate_report,
    load_csv_columns,
    rmse_of,
    overshoot,
    settling_time,
    saturation_time,
    dominant_peaks,
    list_plugins,
    metric,
)

__all__ = [
    "Run", "Event", "Note",
    "Store", "UAV_RUNS_DIR",
    "analyse_run", "generate_report", "load_csv_columns",
    "rmse_of", "overshoot", "settling_time", "saturation_time",
    "dominant_peaks", "list_plugins", "metric",
]
