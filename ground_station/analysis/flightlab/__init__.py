"""flightlab: deterministic post-flight analysis (docs/analysis/flightlab-spec.md)."""
from .loaders import LoadError, load
from .model import FlightLog, Signal, SlotInfo


def analyze(*args, **kwargs):
    from .pipeline import analyze as _analyze
    return _analyze(*args, **kwargs)


__all__ = ["load", "analyze", "FlightLog", "Signal", "SlotInfo", "LoadError"]
