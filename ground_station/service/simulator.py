"""Deterministic telemetry source for service and API tests."""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from ground_station.comm.frame_simulator import _pack_telemetry_frame


@dataclass
class SimulatorSource:
    """Generate a repeatable Frame A sequence without touching hardware."""
    sample_rate_hz: float = 20.0
    sequence: int = 0
    time_s: float = 0.0

    def next_frame(self) -> bytes:
        t = self.time_s
        floats = [math.sin(2.0 * math.pi * f * t)
                  for f in (0.31, 0.47, 0.59, 0.71, 0.83, 0.97, 1.09, 1.21)]
        # Frame A v13 payload: eight floats followed by status bytes.
        payload = struct.pack("<8fBBBBBBBBB", *floats, 0, 2, 0, 0, 0, 1, 0, 1, 14)
        frame = _pack_telemetry_frame(0x01, self.sequence & 0xFF, payload)
        self.sequence = (self.sequence + 1) & 0xFF
        self.time_s += 1.0 / self.sample_rate_hz
        return frame
