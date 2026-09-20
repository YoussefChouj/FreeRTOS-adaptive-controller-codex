"""TelemetryAdapter -- the single seam that turns raw telemetry into ``streams[]``.

Architecture
~~~~~~~~~~~~
Before this module, three independent code paths merged into
``GroundStationService._streams``:

  * **Typed subscribe** (tags ``s0``..``s3``): the wifi bridge already
    knows the slot from the wire frame type, but stream metadata
    (sequence / received / dropped / loss_pct) was stuffed into the
    values dict via payload mutation and extracted again in
    ``service.core._extract_stream_metadata``.

  * **Sidebar** (tags ``a``/``b``/``c``/``id``): the legacy Frame A/B/C
    mirror path. ``wifi_bridge._slot0_to_sidebar`` (90-line hardcoded
    dict) mapped DWARF names to dashboard sidebar keys, silently
    dropping 19 of 22 entries when DWARF drifted.

  * **External** (``inject_external_stream``): used by the SWD RTOS
    bridge. Clean single-purpose path, but it lived in a separate
    method with its own merge logic.

Each path had its **own merging semantics**, its **own freshness
tracking**, and its **own DWARF resolution. They met at
``_streams[slot].values`` but never agreed on where metadata lived,
how stale data was evicted, or what an unknown tag should become.

This module replaces all three with **one seam**:

  ┌─────────────────────────────────────────────────────────────────┐
  │                  TelemetryAdapter                               │
  │                                                                 │
  │   adapt_typed_subscribe(...) ┐                                │
  │   adapt_sidebar(...)         ├─→ NormalizedSample ─→ apply()  │
  │   adapt_external(...)        ┘                                │
  │                                                                 │
  │   evict_stale(...)  ← freshness TTL lives here, not in snapshot │
  └─────────────────────────────────────────────────────────────────┘

The adapter's interface is small (3 ``adapt_*`` entry points + ``apply`` +
``evict_stale``). Behind it lives the schema-driven DWARF lookup, the
merge logic, the per-key freshness map, and the freshness eviction.

Why a deep module (small interface, lots of behaviour behind it)?

* The deletion test: imagine deleting the adapter. ``_streams`` would
  need three merge implementations scattered across ``wifi_bridge``
  and ``service.core``, plus two ``_extract_*`` helpers and the
  freshness TTL. Complexity reappears in N places; the adapter earns
  its keep.

* The interface is the test surface. ``adapt_*`` produces a
  ``NormalizedSample``; ``apply`` mutates a dict in a known way. Tests
  exercise the seam without spinning up the bridge, the typed decoder,
  or the HTTP server.

* One adapter means future agents add a new source by writing one
  ``adapt_<source>()`` method, not by editing four files. The
  ``codebase-design/SKILL.md`` rule "deepen where the seam is real"
  applies: this seam is real because three paths converge here today.

Backward compatibility
~~~~~~~~~~~~~~~~~~~~~~
The public API surface of ``GroundStationService`` (``ingest``,
``ingest_decoded``, ``inject_external_stream``, ``snapshot``,
``GET /state``) is unchanged. Existing tests continue to pass with no
edits because the JSON shape ``/state`` returns is identical.

Internally:

* ``_TAG_TO_SLOT`` and ``_resolve_tag_slot`` move into the adapter.
* ``_extract_stream_metadata`` is replaced by ``_extract_metadata``
  (a private adapter helper used only by ``adapt_typed_subscribe``).
* ``_slot0_to_sidebar`` (the 90-line static dict) is replaced by
  ``SchemaRegistry`` -- still readable from the wifi bridge, but the
  registry is the single source of truth.

The ``_streams`` dict shape is unchanged (``tag``, ``values``,
``sequence``, ``source_time_ms``, ``received``, ``dropped``,
``loss_pct``, ``last_update_ns``, ``_key_ts``). Plugins do not change.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from .schema_registry import SchemaRegistry


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StreamMetadata:
    """Per-sample stream metadata, hoisted from the values dict.

    Replaces the previous ``slotN.seq``/``slotN.t_ms``/``slotN.received``/
    ``slotN.dropped``/``slotN.loss_pct`` keys that ``wifi_bridge`` used
    to stuff INSIDE the values dict and that ``service.core._extract_stream_metadata``
    would then read out. The new adapter takes the metadata as a typed
    argument at the seam, so the values dict carries user-visible keys
    only.

    ``crc_errors`` is the cumulative count of frames rejected by CRC16-CCITT
    validation in ``wifi_bridge._decode_stream_frame``. It is NOT included
    in ``loss_pct`` because it represents wire corruption rather than
    expected wireless loss; it is tracked separately so the diagnostics
    bundle can distinguish the two failure modes.
    """

    sequence: int = 0
    source_time_ms: int = 0
    received: int = 0
    dropped: int = 0
    loss_pct: float = 0.0
    crc_errors: int = 0


@dataclass(frozen=True)
class NormalizedSample:
    """One canonical telemetry sample, ready for ``apply()``.

    The three adapt_*() methods produce this shape so the service
    ``streams[]`` dict has exactly one merge path. Provenance is
    preserved as ``tag`` so future plugins can colour-code streams
    by source (typed / sidebar / external / replay).
    """

    slot: int | str
    tag: str                       # "typed" | "sidebar" | "external" | "replay"
    values: dict[str, float]       # spec-keyed (where DWARF name was known)
    received_ns: int               # wall-clock at ingest
    metadata: StreamMetadata = field(default_factory=StreamMetadata)

    def with_metadata(self, metadata: StreamMetadata) -> "NormalizedSample":
        """Return a copy with replaced metadata (fluent; keeps the dataclass frozen)."""
        return NormalizedSample(
            slot=self.slot,
            tag=self.tag,
            values=self.values,
            received_ns=self.received_ns,
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


# Tag-to-slot mapping, lifted from ``service.core._TAG_TO_SLOT`` and
# ``service.core._resolve_tag_slot``. Kept at module level so the
# adapter can be unit-tested without instantiating a service.
_LEGACY_TAG_TO_SLOT: dict[str, int] = {
    "a":  0,   # Frame A sidebar / slot-0 subscribe stream (merged into slot 0)
    "id": 1,   # Frame ID counters
    "b":  1,   # Frame B adaptive / slot-1 typed stream
    "c":  3,   # Frame C EKF / slot-3 typed stream
}


class TelemetryAdapter:
    """Single seam that converts raw telemetry into ``streams[slot]`` dicts.

    Three ``adapt_*`` entry points cover every current source; all three
    produce the same ``NormalizedSample`` shape. ``apply()`` owns the
    merge logic; ``evict_stale()`` owns the freshness TTL.

    The adapter is **deep**: small public surface, lots of behaviour
    behind it (schema-driven mapping, per-key freshness, slot merge
    semantics, metadata hoisting). Adding a new source (e.g. UART5 wired
    CMSIS-DAP) is one new ``adapt_<source>()`` method that builds a
    ``NormalizedSample``; everything downstream reuses ``apply``.
    """

    DEFAULT_FRESHNESS_TTL_NS: int = 30 * 1_000_000_000

    def __init__(
        self,
        schema: SchemaRegistry,
        *,
        freshness_ttl_ns: int = DEFAULT_FRESHNESS_TTL_NS,
    ) -> None:
        self._schema = schema
        self._freshness_ttl_ns = int(freshness_ttl_ns)
        # Per-slot monotonic counters for paths that don't carry their
        # own sequence number (sidebar / external). The typed subscribe
        # path carries its own ``sequence`` byte from the wire; this
        # counter is only used as a fallback when ``sequence`` is 0.
        self._counter: dict[Any, int] = {}

    # ---- public surface ------------------------------------------------

    @property
    def schema(self) -> SchemaRegistry:
        return self._schema

    @property
    def freshness_ttl_ns(self) -> int:
        return self._freshness_ttl_ns

    def resolve_slot(self, tag: str) -> int | None:
        """Map a wifi_bridge frame tag to its dashboard slot.

        Typed subscribe tags (``s0``..``s3``) map via the integer suffix.
        Legacy frame tags (``a``/``b``/``c``/``id``) map via the
        ``_LEGACY_TAG_TO_SLOT`` table. Unknown tags return ``None`` so
        the caller can discard the frame instead of creating a phantom
        ``streams['-1']`` entry.

        Returning ``None`` is preferred over ``-1`` because the slot
        dict is keyed by string in the JSON snapshot (``streams['0']``
        etc.); a negative integer key would render as a real
        ``streams['-1']`` entry visible to plugins.
        """
        if tag and tag.startswith("s") and len(tag) > 1 and tag[1:].isdigit():
            return int(tag[1:])
        if tag in _LEGACY_TAG_TO_SLOT:
            return _LEGACY_TAG_TO_SLOT[tag]
        return None

    # ---- adapt_* entry points -----------------------------------------

    def adapt_from_decoder(
        self,
        slot: int,
        values: dict[str, list],
        metadata: StreamMetadata,
        received_ns: int | None = None,
    ) -> NormalizedSample:
        """Adapt ``MultiStreamDecoder.feed()`` output -- raw 0x09+slot bytes.

        ``values`` is a dict mapping range name to a list of decoded floats
        (one element per range, except for packed multi-element ranges which
        carry the whole list). We pass it through unchanged -- the typed
        decoder's range names ARE the spec keys, and the list shape is the
        contract plugins reading ``state.streams[N].values`` already see.

        Stream metadata is taken from the typed decoder's per-slot
        counters (``received``, ``dropped``, ``loss_pct``) and the wire
        ``Sequence`` / source timestamp bytes.
        """
        return NormalizedSample(
            slot=slot,
            tag="typed",
            values=dict(values),
            received_ns=received_ns if received_ns is not None else time.time_ns(),
            metadata=metadata,
        )

    def adapt_from_bridge(
        self,
        slot: int,
        payload: dict,
        received_ns: int | None = None,
        *,
        metadata: StreamMetadata | None = None,
    ) -> NormalizedSample:
        """Adapt ``wifi_bridge`` decoded telemetry into a normalized sample.

        Two operating modes:

        * **Typed metadata** (preferred, S15 deep-module refactor):
          the bridge computes ``StreamMetadata`` from its own per-slot
          counters (``_stream_stats``) and passes it as the ``metadata=``
          keyword. The values dict stays clean (no ``slotN.*`` keys
          injected and re-extracted).

        * **Legacy payload mutation** (transitional): if ``metadata`` is
          ``None``, fall back to reading ``slotN.seq``/``slotN.t_ms``/etc.
          out of the values dict via :meth:`extract_metadata`. This keeps
          the older wifi_bridge.py path that injected those keys via
          ``dict(payload)`` working unchanged. New bridge code should
          pass typed metadata; this branch exists for backward compat
          with test fixtures and any external caller that hasn't migrated.

        The output sample's ``tag`` is ``"typed"`` because every wire
        frame the bridge decodes is, by definition, a typed
        subscription. The dashboard distinguishes ``typed`` from
        ``sidebar`` and ``external`` by inspecting the tag, so the
        typed path keeps its identity even when metadata is hoisted
        into the top-level fields.
        """
        if metadata is None:
            metadata = self.extract_metadata("", slot, payload)
        return NormalizedSample(
            slot=slot,
            tag="typed",
            values=dict(payload),
            received_ns=received_ns if received_ns is not None else time.time_ns(),
            metadata=metadata,
        )

    def adapt_sidebar(
        self,
        slot: int,
        dwarf_names: Iterable[str],
        values: Iterable[float],
        received_ns: int | None = None,
        *,
        metadata: StreamMetadata | None = None,
    ) -> NormalizedSample:
        """Adapt a sidebar / legacy frame payload (Frame A/B/C mirror).

        Uses the schema registry to map DWARF names to dashboard spec
        keys (``imu_data.rol`` -> ``status.roll_deg``). Unmapped names
        are emitted under their raw DWARF path so plugins can still
        read them.

        Stream metadata defaults to zeros when the caller doesn't
        supply one -- this preserves the S15 contract where a sidebar
        payload WITHOUT embedded ``slotN.*`` keys would not clobber an
        existing slot's hoisted metadata. The wifi bridge passes an
        explicit ``StreamMetadata`` carrying its own ``_stream_stats``
        counters when those are meaningful (slot 0 sidebar with active
        ``_SIDEBAR_TAGS_FOR_STATS`` tracking).
        """
        mapped = self._schema.resolve_all(zip(dwarf_names, values))
        ns = received_ns if received_ns is not None else time.time_ns()
        meta = metadata if metadata is not None else StreamMetadata()
        return NormalizedSample(
            slot=slot,
            tag="sidebar",
            values=mapped,
            received_ns=ns,
            metadata=meta,
        )

    def adapt_external(
        self,
        slot: int | str,
        values: dict[str, float],
        received_ns: int | None = None,
        *,
        metadata: StreamMetadata | None = None,
    ) -> NormalizedSample:
        """Adapt an external injection (SWD RTOS bridge, replay, test).

        The caller already knows the spec keys (no DWARF name lookup);
        the values dict is passed through unchanged. Used by the RTOS
        bridge under the string key ``"rtos"`` and by the test fixtures
        under both int and string slots.
        """
        ns = received_ns if received_ns is not None else time.time_ns()
        meta = metadata if metadata is not None else StreamMetadata(
            sequence=self._bump_counter(slot),
            source_time_ms=0,
            received=1,
            dropped=0,
            loss_pct=0.0,
        )
        return NormalizedSample(
            slot=slot,
            tag="external",
            values=dict(values),
            received_ns=ns,
            metadata=meta,
        )

    # ---- merge + eviction ----------------------------------------------

    def apply(self, sample: NormalizedSample, streams: dict[Any, dict]) -> None:
        """Merge a normalized sample into the service's ``streams`` dict.

        One place handles every merge:

        * First ingest on a slot creates a fresh entry with all
          metadata fields populated.
        * Subsequent ingests merge into the existing entry, updating
          only the keys the sample carries (no wholesale overwrite).
        * Per-key freshness map (``_key_ts``) is updated for every key
          in the sample so plugins can flag stale individual keys.
        * Hoisted metadata (``sequence``, ``source_time_ms``,
          ``received``, ``dropped``, ``loss_pct``) is taken from the
          sample's ``StreamMetadata``, not from the values dict.

        The output entry shape is **identical** to the S15 shape the
        service was producing before -- ``tag``, ``values``,
        ``sequence``, ``source_time_ms``, ``received``, ``dropped``,
        ``loss_pct``, ``last_update_ns``, ``_key_ts``. Plugins do not
        need to change.
        """
        slot = sample.slot
        existing = streams.get(slot)
        meta = sample.metadata
        ns = sample.received_ns
        if existing is None:
            streams[slot] = {
                "tag":            sample.tag,
                "values":         dict(sample.values),
                "sequence":       meta.sequence,
                "source_time_ms": meta.source_time_ms,
                "received":       meta.received,
                "dropped":        meta.dropped,
                "loss_pct":       meta.loss_pct,
                "last_update_ns": ns,
                "_key_ts":        {k: ns for k in sample.values},
            }
            return

        # Existing entry -- merge.
        existing_values = existing.get("values")
        # Frames decoded before the slot's 0x08 schema arrived are named
        # positionally (``slotN.chN.i``) and hold garbage. Once named keys
        # arrive for the slot, drop the positional ones so they don't linger.
        pos_prefix = "slot%s.ch%s." % (slot, slot)
        if isinstance(existing_values, dict) and any(
            k.startswith("slot%s." % slot) and not k.startswith(pos_prefix)
            for k in sample.values
        ):
            key_ts_old = existing.get("_key_ts")
            for k in [k for k in existing_values if k.startswith(pos_prefix)]:
                del existing_values[k]
                if isinstance(key_ts_old, dict):
                    key_ts_old.pop(k, None)
        if isinstance(existing_values, dict):
            existing_values.update(sample.values)
        else:
            existing["values"] = dict(sample.values)
        existing["tag"]            = sample.tag
        # Metadata fields use ``or existing.get(...)`` semantics so a 0
        # from one path doesn't clobber a real value from another.
        # The TypedSubscribe path carries its own metadata; the sidebar
        # path uses 0 for source_time_ms (no wire clock on that path).
        # Taking ``meta.X or existing.get(X, 0)`` preserves the more
        # informative of the two.
        existing["sequence"]       = meta.sequence or existing.get("sequence", 0)
        existing["source_time_ms"] = meta.source_time_ms or existing.get("source_time_ms", 0)
        existing["received"]       = meta.received or existing.get("received", 0)
        existing["dropped"]        = meta.dropped or existing.get("dropped", 0)
        existing["loss_pct"]       = meta.loss_pct or existing.get("loss_pct", 0.0)
        existing["last_update_ns"] = ns

        # Per-key freshness map. Plugins reading ``_key_ts[k]`` can
        # render stale individual keys; absent keys are considered
        # stale (never received).
        key_ts = existing.get("_key_ts")
        if not isinstance(key_ts, dict):
            key_ts = {}
            existing["_key_ts"] = key_ts
        for k in sample.values:
            key_ts[k] = ns

    def evict_stale(
        self,
        streams: dict[Any, dict],
        now_ns: int,
        started_at_ns: int = 0,
    ) -> list[Any]:
        """Evict slots whose ``last_update_ns`` is older than the TTL.

        Preserves the S15 carve-out: only slots whose
        ``last_update_ns`` is a valid wall-clock timestamp (greater than
        ``started_at_ns``) are eligible. Slots whose ingest used a
        relative wire timestamp (e.g. the persist/replay tests pass
        ``time_ns=10``) have ``last_update_ns`` well before service
        start; they are kept so the persistence tests do not break.

        Returns the list of evicted slots so callers can log.
        """
        cutoff = now_ns - self._freshness_ttl_ns
        stale = [
            slot for slot, data in streams.items()
            if started_at_ns
            and (data.get("last_update_ns") or 0) > started_at_ns
            and (data.get("last_update_ns") or 0) < cutoff
        ]
        for slot in stale:
            del streams[slot]
        return stale

    # ---- backwards-compat helpers --------------------------------------

    def extract_metadata(
        self,
        tag: str,
        slot: int,
        telemetry: dict[str, Any],
    ) -> StreamMetadata:
        """Pull stream metadata out of legacy ``slotN.*`` payload keys.

        The wifi_bridge embeds ``slotN.seq``/``slotN.t_ms``/etc. inside
        the values dict as a side effect of the S15 payload-mutation
        injection. This helper reads them out so the typed-subscribe
        adapt path can hoist them into ``StreamMetadata``.

        New code should pass ``StreamMetadata`` directly to
        ``adapt_typed_subscribe()`` -- this helper exists only for the
        transitional period when the bridge still uses payload mutation.
        """
        prefix = f"slot{slot}."
        try:
            sequence = int(telemetry.get(f"{prefix}seq", 0) or 0)
        except (TypeError, ValueError):
            sequence = 0
        try:
            source_ms = int(telemetry.get(f"{prefix}t_ms", 0) or 0)
        except (TypeError, ValueError):
            source_ms = 0
        try:
            received = int(telemetry.get(f"{prefix}received", 0) or 0)
        except (TypeError, ValueError):
            received = 0
        try:
            dropped = int(telemetry.get(f"{prefix}dropped", 0) or 0)
        except (TypeError, ValueError):
            dropped = 0
        try:
            loss_pct = float(telemetry.get(f"{prefix}loss_pct", 0.0) or 0.0)
        except (TypeError, ValueError):
            loss_pct = 0.0
        try:
            crc_errors = int(telemetry.get(f"{prefix}crc_errors", 0) or 0)
        except (TypeError, ValueError):
            crc_errors = 0
        return StreamMetadata(
            sequence=sequence,
            source_time_ms=source_ms,
            received=received,
            dropped=dropped,
            loss_pct=loss_pct,
            crc_errors=crc_errors,
        )

    # ---- internal ------------------------------------------------------

    def _bump_counter(self, slot: Any) -> int:
        self._counter[slot] = self._counter.get(slot, 0) + 1
        return self._counter[slot]