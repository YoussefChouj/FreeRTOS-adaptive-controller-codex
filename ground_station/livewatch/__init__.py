"""livewatch — full pyOCD capability layer for the STM32F407 research drone.

All pyOCD read and write paths are exposed. The drone is a research platform;
the operator has explicitly unlocked all tools (2026-08-18).

Layers:
  probe.py     — raw pyOCD session: memory r/w, flash, halt/step/resume/reset,
                 breakpoints, watchpoints, RTT, SWO, gdbserver
  symbols.py   — DWARF-backed name → (address, ctype) resolver (offline, no hardware)
  reader.py    — safe attach-mode read-only wrapper around probe.py
  transport.py — UART5 / USART3 read-only transport seam
  registry.py  — curated variable groups
  cli.py       — `python -m ground_station.livewatch ...`

WARNING: probe.py is NOT read-only. It can halt, reset, write flash, write RAM,
set breakpoints, and drive the core. Use with caution.
"""
from .symbols import SymbolResolver, Symbol
from .reader import LiveReader
from .transport import (
    LiveTransport, LiveTransportError, SwdCmsisDap, Uart5LongRange,
)
from .probe import (
    ProbeSession, ProbeError, CORE_REGISTERS,
    BreakpointKind, BreakpointEvent,
    WatchpointType, WatchpointEvent,
    RTTCapture, SWOCapture,
    RTTDirection,
)

__all__ = [
    # Public API
    "SymbolResolver", "Symbol",
    "LiveReader",
    "LiveTransport", "LiveTransportError",
    "SwdCmsisDap", "Uart5LongRange",
    "ProbeSession", "ProbeError",
    "CORE_REGISTERS",
    # Enums
    "BreakpointKind", "BreakpointEvent",
    "WatchpointType", "WatchpointEvent",
    "RTTCapture", "SWOCapture",
    "RTTDirection",
]
