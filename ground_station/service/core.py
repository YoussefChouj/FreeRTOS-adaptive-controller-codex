"""Schema-aware local ground-station service.

This module owns the live ``streams[]`` dict that the dashboard consumes
via ``GET /state``. The actual merge logic lives in
:mod:`ground_station.service.telemetry_adapter` -- this class is now a
thin coordination layer:

  * The adapter owns the seam between raw telemetry (typed decoder,
    sidebar path, external injection) and the service's state dict.
  * The service owns session lifecycle, command gateway, event log,
    and the HTTP snapshot publication.

Backward compatibility
~~~~~~~~~~~~~~~~~~~~~~
The public API surface (``ingest``, ``ingest_decoded``,
``inject_external_stream``, ``snapshot``, ``set_slot_freshness_ttl``)
is unchanged. Existing tests pass without edits because the ``streams``
dict shape (``tag``, ``values``, ``sequence``, ``source_time_ms``,
``received``, ``dropped``, ``loss_pct``, ``last_update_ns``,
``_key_ts``) is identical.

The previously-private helpers ``_TAG_TO_SLOT``, ``_resolve_tag_slot``,
``_extract_stream_metadata`` are kept as thin classmethod shims that
delegate to the adapter. Any external code (tests, callers in other
packages) that imported them still works.
"""
from __future__ import annotations

import functools
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from ground_station.livewatch.stream import MultiStreamDecoder, StreamSchema
from ground_station.platform.telemetry import TelemetrySchema, load_telemetry_schema

from .gateway import CommandGateway
from .schema_registry import SchemaRegistry
from .storage import SessionStore
from .telemetry_adapter import StreamMetadata, TelemetryAdapter

# Maximum number of recent command results retained in the service snapshot.
COMMAND_RESULTS_HISTORY = 10

# Maximum number of command actions kept in the service's action journal.
ACTION_JOURNAL_MAX = 64

# Maximum number of fault entries kept in the service's fault log.
FAULT_LOG_MAX = 64

# Adapter protocol version. Plugins should compare this against the
# ``adapter_version`` field on ServiceState to detect drift. Bump when
# the streams[] shape, hoisted metadata contract, or freshness TTL
# semantics change. The shell exposes the same constant to JS via
# window.__SHELL_API_VERSION__ and the dashboard JSON via state.adapter_version.
#
# Version history:
#   v1: initial typed-subscribe decoder + sidebar merge (r1-s1-9F32E2EA)
#   v2: schema_id + telemetry_schema_id + adapter_version on ServiceState
#       (forward-looking — see PLANNING_PROMPT.md §8 Pattern 4)
ADAPTER_PROTOCOL_VERSION = "v2"


@dataclass(frozen=True)
class ServiceState:
    """Immutable snapshot published on each ingest cycle.

    The ``last_transaction_result`` and ``command_results`` fields mirror what
    the browser shell expects when polling ``/state`` for command feedback
    (see ``docs/dashboard-platform/shell/plugins/command-panel.js``).

    Schema-version fields (S16, PLANNING_PROMPT.md §8 Pattern 4):

      * ``schema_id``           — frozen CRC of the typed-telemetry registry
                                   (e.g. ``"r1-s1-9F32E2EA"``). Already
                                   on the legacy snapshot.
      * ``telemetry_schema_id`` — alias of ``schema_id`` (new in v2,
                                   surfaced under its own key so plugins
                                   can subscribe to drift on the
                                   telemetry-specific CRC without
                                   reading the typed-registry id).
      * ``adapter_version``     — host adapter protocol version
                                   (``"v2"`` today). Plugins compare
                                   against the shell's
                                   ``window.__SHELL_API_VERSION__`` so
                                   they can warn on a shell/plugin
                                   mismatch instead of silently
                                   misrendering.
      * ``slot_freshness_ttl_ns`` — current slot TTL so plugins know
                                   how long the service will keep a
                                   slot alive without updates. The
                                   /state JSON embeds it; plugins
                                   that want to compute their own
                                   staleness reads can compare
                                   ``Date.now()*1e6 - last_update_ns``
                                   against this constant instead of
                                   hardcoding 3 s.
    """
    schema_id: str
    session_id: str | None
    connected: bool
    samples: int
    last_update_ns: int | None
    streams: dict[int, dict[str, Any]]
    last_transaction_result: dict[str, Any] | None = None
    command_results: list[dict[str, Any]] = field(default_factory=list)
    # S16 — schema-versioned contract fields (Pattern 4).
    adapter_version: str = ADAPTER_PROTOCOL_VERSION
    telemetry_schema_id: str | None = None
    slot_freshness_ttl_ns: int = 30 * 1_000_000_000


# ---------------------------------------------------------------------------
# Command lifecycle state (WP3)
# ---------------------------------------------------------------------------
# States map to the firmware's 0x30/0x31/0x32 result outcomes:
#   submitted  -> command sent over the wire (wire_bytes > 0)
#   acknowledged -> firmware sent ACK (Outcome.ACK = 0)
#   applied   -> firmware applied the value (Outcome.APPLIED = 2)
#   rejected  -> firmware rejected it (Outcome.REJECTED = 1)
#   timed_out -> no response within the gateway's timeout window
#   unknown   -> pre-submit placeholder before any wire activity
#
# "applied" is NOT "observed": APPLIED means the firmware wrote the
# variable; OBSERVED means a subsequent telemetry frame confirmed the
# new value. The gap between applied and observed is the readback lag.


COMMAND_TIMEOUT_NS = 1_000_000_000  # no result within 1.0 s -> timed_out


class CommandLifecycle:
    UNKNOWN = "unknown"
    SUBMITTED = "submitted"       # wire transaction open
    ACKNOWLEDGED = "acknowledged"  # firmware ACK'd (0x30)
    APPLIED = "applied"          # firmware wrote the variable (0x32)
    VERIFIED = "verified"        # APPLIED + telemetry readback confirmed
    REJECTED = "rejected"        # firmware rejected (0x31)
    TIMED_OUT = "timed_out"      # no firmware response
    ERROR = "error"              # local encode/transport error


@dataclass(frozen=True)
class CommandAction:
    """Complete lifecycle record for one command submission.

    Captures the full command lifecycle from submission through the
    firmware's outcome (ACK / applied / rejected / timeout). Used by
    /api/actions and the event journal.

    ``_readback`` is populated when a telemetry frame carries the matching
    readback signal and the value matches what was submitted (within
    resolution). Agents should not claim "applied" unless the telemetry
    has observed the new value.
    """
    transaction_id: int
    command_id: int
    index: int
    value: float
    flags: int
    lifecycle: str  # one of CommandLifecycle values
    outcome_name: str | None   # Outcome enum name when resolved
    reason_name: str | None    # RejectReason enum name when resolved
    wire_bytes: int | None    # bytes sent; None = not yet transmitted
    submitted_ns: int
    resolved_ns: int | None   # firmware response timestamp; None = unresolved
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "command_id": self.command_id,
            "index": self.index,
            "value": self.value,
            "flags": self.flags,
            "lifecycle": self.lifecycle,
            "outcome_name": self.outcome_name,
            "reason_name": self.reason_name,
            "wire_bytes": self.wire_bytes,
            "submitted_ns": self.submitted_ns,
            "resolved_ns": self.resolved_ns,
            "detail": self.detail,
        }


@functools.lru_cache(maxsize=1)
def _started_commit() -> str | None:
    """Short hash of git HEAD, read once per process at first service
    start. Returns ``None`` when git is absent, the repo is missing, or
    the command fails — callers must render that as null, never a fake
    hash.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    commit = out.stdout.strip()
    return commit or None


class GroundStationService:
    """Own the bridge boundary and publish immutable state snapshots."""

    def __init__(
        self,
        bridge=None,
        *,
        store: SessionStore | None = None,
        schema: TelemetrySchema | None = None,
        schemas: list[StreamSchema] | None = None,
        source: str = "wifi",
        command_history: int = COMMAND_RESULTS_HISTORY,
        adapter: TelemetryAdapter | None = None,
        schema_registry: SchemaRegistry | None = None,
        experiment_runtime=None,
    ) -> None:
        self.bridge = bridge
        self.schema = schema or load_telemetry_schema()
        self.store = store or SessionStore()
        self.source = source
        # Startup identity, exposed by GET /health so a walk can spot a
        # service running stale code. The commit is read once per process.
        self.started_at: float = time.time()
        self.started_commit: str | None = _started_commit()
        self.session_id: str | None = None
        self.decoder = MultiStreamDecoder(schemas or [])
        # The adapter owns the seam between raw telemetry and ``streams``.
        # Callers may inject a custom adapter for tests (see
        # ``test_telemetry_adapter.py``); production code accepts the
        # default, which loads the builtin dashboard mapping. The
        # schema registry is the only thing the service exposes that
        # the adapter doesn't already wrap.
        self.schema_registry = schema_registry or SchemaRegistry.builtin_dashboard()
        self.adapter = adapter or TelemetryAdapter(
            self.schema_registry,
            freshness_ttl_ns=self._default_ttl_ns(),
        )
        self._state_lock = threading.Lock()
        self._listeners: list[Callable[[ServiceState], None]] = []
        self._samples = 0
        self._last_update_ns: int | None = None
        self._streams: dict[int, dict[str, Any]] = {}
        # Per-slot counter for ``inject_external_stream`` -- increments
        # ``received`` per call (the legacy semantics the test suite
        # depends on: ``streams['rtos'].received == 2`` after two calls).
        self._external_received: dict[Any, int] = {}
        self._connected = False
        # Bounded history of recent command results for the shell.
        self._command_results: deque[dict[str, Any]] = deque(maxlen=command_history)
        self._last_transaction_result: dict[str, Any] | None = None
        # Bounded action journal for /api/actions endpoint (WP6).
        self._action_journal: deque[CommandAction] = deque(maxlen=ACTION_JOURNAL_MAX)
        # Bounded fault log for /api/faults endpoint (WP6).
        self._fault_log: deque[dict[str, Any]] = deque(maxlen=FAULT_LOG_MAX)
        # Track APPLIED commands waiting for telemetry readback confirmation.
        # Maps (command_id, index) -> list of transaction_ids waiting for VERIFIED.
        self._pending_verifications: dict[tuple[int, int], list[int]] = {}
        self.gateway = CommandGateway(bridge, self._record_event) if bridge else None
        # Experiment runtime: ticks on every telemetry ingest so active experiments
        # can record samples and advance their settle/measure state machine.
        # Passed from __main__.py; may be None when the experiment subsystem is
        # not wired (e.g. in unit tests that don't exercise experiments).
        self._experiment_runtime = experiment_runtime
        # Default 30 s. Configurable for tests; the dashboard shell uses the
        # default. See snapshot() for how the TTL evicts stale slots.
        self._slot_freshness_ttl_ns: int = 30 * 1_000_000_000
        # Wall-clock timestamp captured in start(). The freshness eviction
        # only acts on slots whose ``last_update_ns`` is BOTH a valid
        # wall-clock time (greater than this) AND older than the TTL cutoff.
        # Slots whose ``last_update_ns`` predates the service start (e.g.
        # tests passing relative wire timestamps like ``time_ns=10``) are
        # NOT eligible for eviction -- this preserves backward compatibility
        # with the original persist/replay tests and lets the dashboard JS
        # do its own staleness rendering via ``Date.now() - last_update_ns``.
        self._started_at_ns: int = 0

    @staticmethod
    def _default_ttl_ns() -> int:
        return 30 * 1_000_000_000

    def set_slot_freshness_ttl(self, ttl_seconds: float) -> None:
        """Override the slot-freshness TTL. Used by tests and by the
        ``--freshness-ttl`` CLI flag for debugging live-link quality."""
        self._slot_freshness_ttl_ns = int(ttl_seconds * 1_000_000_000)
        # Mirror the change on the adapter so its ``evict_stale`` is
        # consistent with what ``snapshot`` would have done inline.
        self.adapter._freshness_ttl_ns = self._slot_freshness_ttl_ns

    def add_listener(self, callback: Callable[[ServiceState], None]) -> None:
        self._listeners.append(callback)

    def start(self, *, metadata: dict[str, Any] | None = None,
              auto_subscribe: bool = True) -> str:
        if self.session_id is not None:
            return self.session_id
        self.session_id = self.store.start_session(self.schema.schema_id, self.source, metadata)
        # Capture the start time so the freshness eviction can distinguish
        # wall-clock ``last_update_ns`` values from relative wire
        # timestamps. The previous behaviour (compare every slot's
        # ``last_update_ns`` to ``time.time_ns() - TTL``) silently evicted
        # any slot whose ingest used a non-wall-clock time, which broke
        # the persist/replay tests.
        self._started_at_ns = time.time_ns()
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

    def replay_to_bus(self, session_id: str) -> int:
        """Push a stored session's telemetry through the live ingest path.

        Read-only toward the drone: nothing is sent and nothing is stored.
        Returns the number of telemetry records replayed.
        """
        from .replay import SessionReplay
        count = 0
        for record in SessionReplay(self.store, session_id).records():
            if record.type != "telemetry":
                continue
            data = record.data
            self.ingest_decoded(
                "replay", dict(data["values"]), slot=int(data["stream_id"]),
                metadata=StreamMetadata(
                    sequence=int(data["sequence"] or 0),
                    source_time_ms=int(data["source_time_ms"] or 0),
                    received=count + 1, dropped=0, loss_pct=0.0),
                persist=False)
            count += 1
        return count

    def ingest(self, raw: bytes, *, direction: str = "rx", time_ns: int | None = None) -> int:
        """Persist raw bytes and decoded samples; return number of samples.

        The typed-subscribe adapter path: the decoder turns raw bytes
        into ``(slot, sequence, source_ms, values)`` tuples where
        ``values`` is a dict-of-lists keyed by the schema's range names.
        Each tuple is normalised through the adapter so the merge into
        ``_streams`` follows the same code path as the sidebar and
        external ingest paths.
        """
        if self.session_id is None:
            raise RuntimeError("service is not started")
        now = time_ns or time.time_ns()
        self.store.append_raw_frame(self.session_id, direction, raw, now)
        decoded = self.decoder.feed(raw)
        for slot, sequence, source_ms, values in decoded:
            self.store.append_telemetry(self.session_id, slot, sequence, values,
                                        source_ms, now)
            decoder_stats = self.decoder.decoders[slot]
            metadata = StreamMetadata(
                sequence=sequence,
                source_time_ms=source_ms,
                received=decoder_stats.received,
                dropped=decoder_stats.dropped,
                loss_pct=decoder_stats.loss_pct,
            )
            sample = self.adapter.adapt_from_decoder(slot, values, metadata, received_ns=now)
            with self._state_lock:
                self._samples += 1
                self._last_update_ns = now
                self.adapter.apply(sample, self._streams)
            self._notify()
            # Check if telemetry readback confirms any pending commands.
            self._check_readback()
        return len(decoded)

    # Map common bridge frame tags to their dashboard slot. Slot 0 is the
    # dashboard's primary sidebar slot (Frame A / subscribe-stream slot 0),
    # so the named ``status.*`` and ``mrac.*`` keys produced by
    # ``wifi_bridge._slot0_to_sidebar`` merge with the raw subscribe stream
    # into a single ``streams['0']`` entry the plugins read.
    #
    # Kept on the class for back-compat with callers that imported it
    # directly; the adapter is the source of truth (it owns the same
    # mapping in its ``resolve_slot`` method).
    _TAG_TO_SLOT = {
        "a":  0,   # Frame A sidebar / slot-0 subscribe stream (merged into slot 0)
        "id": 1,   # Frame ID counters
        "b":  1,   # Frame B adaptive / slot-1 typed stream
        "c":  3,   # Frame C EKF / slot-3 typed stream
    }

    def ingest_decoded(self, tag: str, telemetry: dict[str, Any],
                       *, slot: int | None = None,
                       time_ns: int | None = None,
                       metadata: StreamMetadata | None = None,
                       persist: bool = True) -> None:
        """Feed already-decoded telemetry from wifi_bridge into the service state.

        ``persist=False`` is the replay path: the sample updates the live
        streams and notifies subscribers, but is not stored again and does
        not confirm pending commands or tick experiments.

        Two paths converge here:

        1. The **typed subscribe path** (tags ``"s0"`` … ``"s3"``): the
           wifi bridge has already resolved the slot from the wire
           frame type (``0x09 + slot``). The values dict contains the
           bridge's named channels (``slot0.<dwarf_name>``). The bridge
           computes per-slot stream metadata (``received``, ``dropped``,
           ``loss_pct``, ``seq``, ``t_ms``) from its own counters and
           passes them as a typed ``StreamMetadata`` keyword. This is
           the S15 deep-module path: NO payload mutation, NO round-trip
           of ``slotN.*`` keys through the values dict.

        2. The **sidebar path** (tag ``"a"`` for slot 0, plus legacy
           ``"b"``/``"id"``/``"c"``): ``_slot0_to_sidebar`` (or its
           ``SchemaRegistry`` successor) has mapped the raw DWARF names
           to dashboard-friendly ``status.*``/``mrac.*`` keys. These
           MERGE with the existing slot's values dict so plugins can
           read either form. The sidebar path passes ``metadata=None``
           so the adapter hoists defaults from the existing entry
           (the typed-subscribe path owns the live counters).

        Both paths now flow through the adapter's
        :meth:`adapt_from_bridge`, which accepts typed ``StreamMetadata``
        directly (preferred) or falls back to extracting from legacy
        ``slotN.*`` keys (transitional, kept for backward compat with
        any caller still using the older payload-mutation style).

        Backward-compat shim: when the bridge packs ``__stream_metadata__``
        inside the values dict (its typed-metadata handoff format), the
        adapter pops it and forwards it as the typed ``metadata`` arg.
        Callers that don't use this shim can pass ``metadata=`` directly.
        """
        if self.session_id is None:
            raise RuntimeError("service is not started")
        now = time_ns or time.time_ns()
        if slot is None:
            slot = self.adapter.resolve_slot(tag)
        # Unknown / unsupported tags (e.g. "data" raw 50-53B datagram) are
        # discarded instead of being routed to a phantom -1 slot. See
        # the adapter's resolve_slot() docstring for the rationale.
        if slot is None:
            self._discard_unknown_tag(tag)
            return

        # Typed-metadata shim: the bridge passes StreamMetadata packed
        # in the values dict under ``__stream_metadata__``. Pop it and
        # forward to the adapter as the typed ``metadata`` argument.
        if metadata is None and isinstance(telemetry, dict):
            shim = telemetry.pop("__stream_metadata__", None)
            if isinstance(shim, StreamMetadata):
                metadata = shim

        sample = self.adapter.adapt_from_bridge(
            slot, telemetry, received_ns=now, metadata=metadata,
        )
        # Persist like ``ingest()`` does, so WiFi sessions are logged and
        # replayable (this path previously only updated live state).
        if persist:
            self.store.append_telemetry(self.session_id, slot,
                                        sample.metadata.sequence or 0,
                                        sample.values,
                                        sample.metadata.source_time_ms or None,
                                        now)
        with self._state_lock:
            self._samples += 1
            self._last_update_ns = now
            self.adapter.apply(sample, self._streams)
        self._notify()
        if not persist:
            return
        # Check if telemetry readback confirms any pending commands.
        self._check_readback()

        # Advance the experiment runtime if one is active.
        # Errors in the runtime (no active experiment, invalid state) are
        # swallowed so they never interrupt the telemetry pipeline.
        # Shadow mode: tick() can only READ telemetry and update internal state;
        # it cannot write control outputs (commands go through the separate
        # gateway path, not through the experiment runtime).
        if self._experiment_runtime is not None:
            try:
                self._experiment_runtime.tick(telemetry)
            except RuntimeError:
                pass  # no active experiment; ignore

    def inject_external_stream(self, slot: int | str, values: dict[str, Any],
                               *, sequence: int | None = None) -> None:
        """Public hook for non-WiFi sources (e.g. the SWD RTOS bridge).

        Merges ``values`` into ``streams[slot]`` the same way the typed
        subscribe path does, so an external SWD poll of
        ``platform_obs_send_ticks`` shows up in the dashboard alongside
        the WiFi data. Stream metadata defaults to the source's
        monotonic time when ``sequence`` is ``None``.

        The ``received`` counter increments per call (legacy semantics
        the RTOS-bridge test depends on: ``streams['rtos'].received``
        equals the number of calls, not just 1).

        This is the integration seam for ``ground_station/platform/rtos_bridge.py``
        and any future ``ground_station/analysis/...`` writer that needs
        to publish into the live snapshot without going through the
        bridge's wire-decoder pipeline.

        Idempotent and lock-safe: callers can invoke from any thread.
        """
        if self.session_id is None:
            return  # service not started; silently ignore external inject
        received_count = self._external_received.get(slot, 0) + 1
        self._external_received[slot] = received_count
        metadata = StreamMetadata(
            sequence=int(sequence or 0),
            source_time_ms=int((sequence or 0) * (1000 // 5)) if sequence else 0,
            received=received_count,
            dropped=0,
            loss_pct=0.0,
        )
        sample = self.adapter.adapt_external(
            slot, values, received_ns=time.time_ns(), metadata=metadata,
        )
        with self._state_lock:
            self._last_update_ns = sample.received_ns
            self.adapter.apply(sample, self._streams)
        self._notify()
        # Check if telemetry readback confirms any pending commands.
        self._check_readback()

    ARM_STALE_NS = 2_000_000_000

    def arm_state(self) -> str:
        """Return "armed", "disarmed" or "unknown" from the latest arm telemetry.

        Reads status.arm / DroneStatus.ARM_Status across all streams. No arm
        sample, or one older than ARM_STALE_NS, is "unknown" (fail closed).
        """
        now = time.time_ns()
        with self._state_lock:
            for slot_data in self._streams.values():
                if isinstance(slot_data, dict):
                    vals = slot_data.get("values", {})
                    if isinstance(vals, dict):
                        for k in ("status.arm", "DroneStatus.ARM_Status", "arm"):
                            if k in vals:
                                ts = (slot_data.get("_key_ts") or {}).get(
                                    k, slot_data.get("last_update_ns", 0))
                                if not ts or now - ts > self.ARM_STALE_NS:
                                    continue
                                try:
                                    return "disarmed" if float(vals[k]) == 0.0 else "armed"
                                except (TypeError, ValueError):
                                    pass
        return "unknown"

    def is_disarmed(self) -> bool:
        """True only if fresh arm telemetry says disarmed (fails closed)."""
        return self.arm_state() == "disarmed"

    def submit_command(self, command_id: int, index: int = 0, value: float = 0.0,
                       flags: int = 0) -> int:
        if self.gateway is None:
            raise RuntimeError("command gateway is unavailable without a bridge")
        # Safety gate: motor bench commands (0x16) require drone to be disarmed
        arm = self.arm_state() if command_id == 0x16 else "disarmed"
        if arm != "disarmed":
            why = "arm state unknown" if arm == "unknown" else "drone is not disarmed"
            self._fault_log.append({
                "transaction_id": 0,
                "command_id": command_id,
                "index": index,
                "outcome": "rejected",
                "reason": "SAFETY_INTERLOCK",
                "detail": "command 0x16 (MOTOR_BENCH) rejected: " + why,
                "time_ns": time.time_ns(),
            })
            self._record_event("command_rejected", {
                "command_id": command_id,
                "reason": "SAFETY_INTERLOCK",
                "detail": why,
            })
            raise ValueError("command 0x16 (MOTOR_BENCH) rejected: " + why)

        # Build the wire frame to capture wire_bytes and record the action.
        from ground_station.platform.transactions import build_command, Command
        cmd = Command(
            transaction_id=0, command_id=command_id, index=index,
            value=value, flags=flags,
        )
        wire_frame = build_command(cmd)
        txid = self.gateway.submit(command_id, index, value, flags)
        now_ns = time.time_ns()
        action = CommandAction(
            transaction_id=txid,
            command_id=command_id,
            index=index,
            value=value,
            flags=flags,
            lifecycle=CommandLifecycle.SUBMITTED,
            outcome_name=None,
            reason_name=None,
            wire_bytes=len(wire_frame),
            submitted_ns=now_ns,
            resolved_ns=None,
            detail="",
        )
        self._action_journal.append(action)
        self._record_event("command_submitted", action.to_dict())
        return txid

    def poll_command(self, timeout: float = 0.0):
        """Poll the gateway for the next command result.

        Side effect: when a result is observed, ``last_transaction_result``,
        ``command_results``, and the action journal are updated so the next
        ``snapshot()`` reflects the resolved lifecycle state.
        Faults (rejected / timed-out) are appended to the fault log.
        """
        if self.gateway is None:
            return None
        result = self.gateway.poll(timeout)
        if result is not None:
            entry = self._result_to_entry(result)
            self._last_transaction_result = entry
            self._command_results.append(entry)
            self._update_action_journal(result)
            # Log faults so /api/faults surfaces them.
            from ground_station.platform.transactions import Outcome
            if result.outcome == Outcome.REJECTED or int(result.reason) != 0:
                self._fault_log.append({
                    "transaction_id": int(result.transaction_id),
                    "command_id": int(result.command_id),
                    "index": int(result.index),
                    "outcome": result.outcome.name.lower(),
                    "reason": result.reason.name,
                    "detail": str(result.detail or ""),
                    "time_ns": time.time_ns(),
                })
        return result

    def record_command_result(self, result) -> None:
        """Insert a transaction result into the snapshot history.

        Intended for unit tests and synthetic bridges that bypass the gateway.
        Production code should rely on ``poll_command``.

        Side-effects mirror ``poll_command``:
          - last_transaction_result
          - command_results
          - action journal (via _update_action_journal)
          - fault log for rejected outcomes
        """
        entry = self._result_to_entry(result)
        self._last_transaction_result = entry
        self._command_results.append(entry)
        self._update_action_journal(result)
        # Log rejected outcomes to the fault log (same policy as poll_command).
        from ground_station.platform.transactions import Outcome
        if result.outcome == Outcome.REJECTED or int(result.reason) != 0:
            self._fault_log.append({
                "transaction_id": int(result.transaction_id),
                "command_id": int(result.command_id),
                "index": int(result.index),
                "outcome": result.outcome.name.lower(),
                "reason": result.reason.name,
                "detail": str(result.detail or ""),
                "time_ns": time.time_ns(),
            })

    @staticmethod
    def _result_to_entry(result) -> dict[str, Any]:
        """Translate a ``transactions.Result`` into the shell-facing shape."""
        outcome_name = result.outcome.name.lower()
        try:
            reason_name = result.reason.name
        except Exception:
            reason_name = ""
        entry: dict[str, Any] = {
            "transaction_id": int(result.transaction_id),
            "command_id": int(result.command_id),
            "index": int(result.index),
            "status": outcome_name,
            "reason": reason_name,
            "detail": str(result.detail or ""),
            "time_ns": time.time_ns(),
        }
        return entry

    def _update_action_journal(self, result) -> None:
        """Walk the action journal and close the lifecycle for the matching txid.

        Finds the most-recently submitted action with this transaction_id that
        is still in SUBMITTED state, and updates it to the resolved lifecycle
        (ACKNOWLEDGED / APPLIED / REJECTED). If no open action is found, the
        result is still recorded as a fault but no action entry is created.
        """
        now_ns = time.time_ns()
        outcome_name = result.outcome.name  # e.g. "ACK", "APPLIED", "REJECTED"
        reason_name = result.reason.name
        # Map outcome -> lifecycle state
        from ground_station.platform.transactions import Outcome
        if result.outcome == Outcome.ACK:
            lifecycle = CommandLifecycle.ACKNOWLEDGED
        elif result.outcome == Outcome.APPLIED:
            lifecycle = CommandLifecycle.APPLIED
            # Track for readback verification: telemetry must confirm the
            # value before we report VERIFIED.
            key = (result.command_id, result.index)
            self._pending_verifications.setdefault(key, []).append(
                result.transaction_id
            )
        else:
            lifecycle = CommandLifecycle.REJECTED

        # Find the matching open action (reverse to get the latest)
        for action in reversed(self._action_journal):
            if action.transaction_id == result.transaction_id and \
                    action.lifecycle == CommandLifecycle.SUBMITTED:
                # Replace in-place by building a new CommandAction with resolved fields.
                resolved = CommandAction(
                    transaction_id=action.transaction_id,
                    command_id=action.command_id,
                    index=action.index,
                    value=action.value,
                    flags=action.flags,
                    lifecycle=lifecycle,
                    outcome_name=outcome_name,
                    reason_name=reason_name,
                    wire_bytes=action.wire_bytes,
                    submitted_ns=action.submitted_ns,
                    resolved_ns=now_ns,
                    detail=str(result.detail or ""),
                )
                # Replace the old entry with the resolved one.
                idx = self._action_journal.index(action)
                self._action_journal[idx] = resolved
                self._record_event("command_" + lifecycle, resolved.to_dict())
                return

    def expire_commands(self, now_ns: int | None = None) -> int:
        """Mark SUBMITTED actions older than COMMAND_TIMEOUT_NS as TIMED_OUT.

        Each expired action is logged once to the fault log. Returns the count.
        """
        if now_ns is None:
            now_ns = time.time_ns()
        expired = 0
        for idx, action in enumerate(list(self._action_journal)):
            if action.lifecycle != CommandLifecycle.SUBMITTED or                     now_ns - action.submitted_ns < COMMAND_TIMEOUT_NS:
                continue
            timed_out = CommandAction(
                transaction_id=action.transaction_id,
                command_id=action.command_id,
                index=action.index,
                value=action.value,
                flags=action.flags,
                lifecycle=CommandLifecycle.TIMED_OUT,
                outcome_name=None,
                reason_name=None,
                wire_bytes=action.wire_bytes,
                submitted_ns=action.submitted_ns,
                resolved_ns=now_ns,
                detail="no firmware response",
            )
            self._action_journal[idx] = timed_out
            if self.gateway is not None:
                self.gateway._pending.discard(action.transaction_id)
            self._fault_log.append({
                "transaction_id": int(action.transaction_id),
                "command_id": int(action.command_id),
                "index": int(action.index),
                "outcome": CommandLifecycle.TIMED_OUT,
                "reason": "TIMEOUT",
                "detail": "no firmware response",
                "time_ns": now_ns,
            })
            self._record_event("command_" + CommandLifecycle.TIMED_OUT,
                               timed_out.to_dict())
            expired += 1
        return expired

    def action_journal(self) -> list[dict[str, Any]]:
        """Return the bounded action journal for /api/actions (WP6)."""
        return [a.to_dict() for a in self._action_journal]

    def _check_readback(self) -> None:
        """Upgrade APPLIED commands to VERIFIED when telemetry confirms values.

        Walks the pending verifications and checks whether any telemetry
        values match the submitted command values. When a match is found,
        the action's lifecycle is upgraded from APPLIED to VERIFIED and the
        command is removed from pending verifications.
        """
        if not self._pending_verifications:
            return
        # Collect all telemetry values into a flat set for matching.
        values: set[float] = set()
        for slot_data in self._streams.values():
            if isinstance(slot_data, dict):
                for v in slot_data.get("values", {}).values():
                    try:
                        values.add(float(v))
                    except (TypeError, ValueError):
                        pass
        # Check each pending verification.
        still_pending: dict[tuple[int, int], list[int]] = {}
        for key, txids in self._pending_verifications.items():
            cmd_id, idx = key
            resolved = False
            remaining = []
            for txid in txids:
                # Find the action in the journal for this txid.
                for action in self._action_journal:
                    if action.transaction_id == txid and \
                            action.command_id == cmd_id and \
                            action.lifecycle == CommandLifecycle.APPLIED:
                        # Check if the submitted value is present in telemetry.
                        if action.value in values:
                            # Upgrade to VERIFIED.
                            resolved_action = CommandAction(
                                transaction_id=action.transaction_id,
                                command_id=action.command_id,
                                index=action.index,
                                value=action.value,
                                flags=action.flags,
                                lifecycle=CommandLifecycle.VERIFIED,
                                outcome_name=action.outcome_name,
                                reason_name=action.reason_name,
                                wire_bytes=action.wire_bytes,
                                submitted_ns=action.submitted_ns,
                                resolved_ns=action.resolved_ns,
                                detail=action.detail + " (verified)",
                            )
                            idx_in_journal = self._action_journal.index(action)
                            self._action_journal[idx_in_journal] = resolved_action
                            self._record_event("command_verified", resolved_action.to_dict())
                            resolved = True
                            break
                if not resolved:
                    remaining.append(txid)
            if remaining:
                still_pending[key] = remaining
        self._pending_verifications.clear()
        self._pending_verifications.update(still_pending)

    def fault_log(self) -> list[dict[str, Any]]:
        """Return the bounded fault log for /api/faults (WP6).

        Faults include firmware rejections, safety interlocks, and any local
        command errors. Each entry carries ``outcome`` and ``reason`` from
        the firmware's result frame plus a wall-clock timestamp.
        """
        return [dict(f) for f in self._fault_log]

    def events_for_session(self, session_id: str | None = None,
                          limit: int = 100) -> list[dict[str, Any]]:
        """Return recent session events for /api/events (WP6).

        Reads from the session store's event table so agents can replay
        the full session history. ``limit`` bounds the returned rows.
        """
        if session_id is None:
            session_id = self.session_id
        if session_id is None:
            return []
        rows = self.store._db.execute(
            "SELECT time_ns, kind, payload_json FROM events "
            "WHERE session_id=? ORDER BY time_ns DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        import json
        return [
            {"time_ns": r[0], "kind": r[1], "payload": json.loads(r[2])}
            for r in rows
        ]

    def snapshot(self) -> ServiceState:
        with self._state_lock:
            # Evict slots whose last_update_ns is older than the freshness
            # TTL. The TTL lives on the adapter so future callers that
            # bypass ``snapshot`` (e.g. an HTTP filter that wants to
            # pre-strip before serialisation) can use ``adapter.evict_stale``
            # directly. ``snapshot`` keeps the carve-out for relative
            # wire timestamps: only slots whose ``last_update_ns`` is a
            # valid wall-clock time (i.e. greater than ``_started_at_ns``)
            # are eligible. Slots whose ingest used a relative wire
            # timestamp (e.g. the persist/replay tests pass ``time_ns=10``)
            # have ``last_update_ns`` well before service start, so they
            # would otherwise be evicted on the very first snapshot.
            self.adapter.evict_stale(
                self._streams, time.time_ns(), self._started_at_ns,
            )
            # Convert int slot keys to str so the shell JS can use
            # state.streams['1'] instead of state.streams[1].
            streams = {str(slot): dict(data) for slot, data in self._streams.items()}
            command_results = list(self._command_results)
            return ServiceState(
                self.schema.schema_id, self.session_id,
                self._connected, self._samples,
                self._last_update_ns, streams,
                self._last_transaction_result,
                command_results,
                # v2 contract fields — populate from the adapter's schema.
                # telemetry_schema_id mirrors schema_id so plugins can subscribe
                # to telemetry-specific drift without knowing about the typed
                # registry. slot_freshness_ttl_ns exposes the adapter's TTL
                # so plugins can compute per-key staleness without hardcoding.
                adapter_version=ADAPTER_PROTOCOL_VERSION,
                telemetry_schema_id=self.schema.schema_id,
                slot_freshness_ttl_ns=self.adapter.freshness_ttl_ns,
            )

    # ---- backwards-compat shims ----------------------------------------

    @classmethod
    def _resolve_tag_slot(cls, tag: str) -> int | None:
        """Deprecated; use ``TelemetryAdapter.resolve_slot`` instead.

        Kept as a classmethod shim so external callers that imported
        ``GroundStationService._resolve_tag_slot`` continue to work.
        New code should go through the service's adapter.
        """
        adapter = TelemetryAdapter(SchemaRegistry.builtin_dashboard())
        return adapter.resolve_slot(tag)

    @staticmethod
    def _extract_stream_metadata(tag: str, slot: int,
                                 telemetry: dict[str, Any]
                                 ) -> tuple[int, int, int, int, float]:
        """Deprecated; use ``TelemetryAdapter.extract_metadata`` instead.

        Kept as a static shim so external callers that imported this
        helper continue to work. The adapter's typed signature
        (``StreamMetadata``) is the new contract.
        """
        adapter = TelemetryAdapter(SchemaRegistry.builtin_dashboard())
        meta = adapter.extract_metadata(tag, slot, telemetry)
        return (meta.sequence, meta.source_time_ms, meta.received,
                meta.dropped, meta.loss_pct)

    # ---- internals -----------------------------------------------------

    def _record_event(self, kind: str, payload: dict[str, Any]) -> None:
        if self.session_id is not None:
            self.store.append_event(self.session_id, kind, payload)

    def _discard_unknown_tag(self, tag: str) -> None:
        """Log once per (tag, session) that an unknown frame was discarded.

        The legacy behaviour was to fabricate a ``streams[-1]`` entry
        full of garbage floats that survived in /state for the full
        30 s freshness TTL and alarmed the dashboard sidebar. Now we
        drop the frame silently (with a one-shot log line so operators
        see the rejection without flooding the log).
        """
        key = (tag, self.session_id)
        if not hasattr(self, "_discard_log_seen"):
            self._discard_log_seen = set()
        if key not in self._discard_log_seen:
            self._discard_log_seen.add(key)
            import sys as _sys
            print(
                f"[service] Discarding telemetry with unknown tag {tag!r}; "
                f"not adding to streams[] (would have created phantom slot).",
                file=_sys.stderr, flush=True,
            )

    def _notify(self) -> None:
        snapshot = self.snapshot()
        for callback in tuple(self._listeners):
            callback(snapshot)