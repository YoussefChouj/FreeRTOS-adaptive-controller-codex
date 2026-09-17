"""Schema-aware local ground-station service."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from ground_station.livewatch.stream import MultiStreamDecoder, StreamSchema
from ground_station.platform.telemetry import TelemetrySchema, load_telemetry_schema

from .gateway import CommandGateway
from .storage import SessionStore


@dataclass(frozen=True)
class ServiceState:
    schema_id: str
    session_id: str | None
    connected: bool
    samples: int
    last_update_ns: int | None
    streams: dict[int, dict[str, Any]]


class GroundStationService:
    """Own the bridge boundary and publish immutable state snapshots."""

    def __init__(self, bridge=None, *, store: SessionStore | None = None,
                 schema: TelemetrySchema | None = None,
                 schemas: list[StreamSchema] | None = None,
                 source: str = "wifi") -> None:
        self.bridge = bridge
        self.schema = schema or load_telemetry_schema()
        self.store = store or SessionStore()
        self.source = source
        self.session_id: str | None = None
        self.decoder = MultiStreamDecoder(schemas or [])
        self._state_lock = threading.Lock()
        self._listeners: list[Callable[[ServiceState], None]] = []
        self._samples = 0
        self._last_update_ns: int | None = None
        self._streams: dict[int, dict[str, Any]] = {}
        self._connected = False
        self.gateway = CommandGateway(bridge, self._record_event) if bridge else None

    def add_listener(self, callback: Callable[[ServiceState], None]) -> None:
        self._listeners.append(callback)

    def start(self, *, metadata: dict[str, Any] | None = None,
              auto_subscribe: bool = True) -> str:
        if self.session_id is not None:
            return self.session_id
        self.session_id = self.store.start_session(self.schema.schema_id, self.source, metadata)
        self._connected = self.bridge is not None
        if self.bridge:
            self.bridge.start(auto_subscribe_boot_default=auto_subscribe)
        self._record_event("service_started", {"schema_id": self.schema.schema_id})
        return self.session_id

    def stop(self) -> None:
        if self.session_id is None:
            return
        self._record_event("service_stopped", {})
        if self.bridge:
            self.bridge.stop()
        self.store.end_session(self.session_id)
        self._connected = False

    def ingest(self, raw: bytes, *, direction: str = "rx", time_ns: int | None = None) -> int:
        """Persist raw bytes and decoded samples; return number of samples."""
        if self.session_id is None:
            raise RuntimeError("service is not started")
        now = time_ns or time.time_ns()
        self.store.append_raw_frame(self.session_id, direction, raw, now)
        decoded = self.decoder.feed(raw)
        for slot, sequence, source_ms, values in decoded:
            self.store.append_telemetry(self.session_id, slot, sequence, values,
                                        source_ms, now)
            with self._state_lock:
                self._samples += 1
                self._last_update_ns = now
                self._streams[slot] = {"sequence": sequence,
                                       "source_time_ms": source_ms,
                                       "values": values,
                                       "received": self.decoder.decoders[slot].received,
                                       "dropped": self.decoder.decoders[slot].dropped,
                                       "loss_pct": self.decoder.decoders[slot].loss_pct}
            self._notify()
        return len(decoded)

    def submit_command(self, command_id: int, index: int = 0, value: float = 0.0,
                       flags: int = 0) -> int:
        if self.gateway is None:
            raise RuntimeError("command gateway is unavailable without a bridge")
        return self.gateway.submit(command_id, index, value, flags)

    def poll_command(self, timeout: float = 0.0):
        if self.gateway is None:
            return None
        return self.gateway.poll(timeout)

    def snapshot(self) -> ServiceState:
        with self._state_lock:
            # Convert int slot keys to str so the shell JS can use
            # state.streams['1'] instead of state.streams[1].
            streams = {str(slot): dict(data) for slot, data in self._streams.items()}
            return ServiceState(self.schema.schema_id, self.session_id,
                               self._connected, self._samples,
                               self._last_update_ns, streams)

    def _record_event(self, kind: str, payload: dict[str, Any]) -> None:
        if self.session_id is not None:
            self.store.append_event(self.session_id, kind, payload)

    def _notify(self) -> None:
        snapshot = self.snapshot()
        for callback in tuple(self._listeners):
            callback(snapshot)
