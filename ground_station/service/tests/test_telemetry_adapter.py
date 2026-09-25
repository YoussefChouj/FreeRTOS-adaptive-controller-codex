"""Unit tests for TelemetryAdapter -- the seam between raw telemetry and ``streams[]``.

Pins the contract documented in ``ground_station/service/telemetry_adapter.py``:

  - ``resolve_slot`` returns ``None`` (not ``-1``) for unknown tags
  - Each ``adapt_*`` entry point produces a ``NormalizedSample`` that
    the adapter's ``apply`` method merges consistently
  - The three legacy tag-to-slot mappings (``a``/``b``/``c``/``id``) match
    what the wifi bridge emits
  - Per-key freshness map (``_key_ts``) is updated on every apply
  - Hoisted metadata survives across merges (typed path) and stays
    non-clobbering when the sidebar path emits zero metadata
  - Eviction honours the wall-clock-vs-relative carve-out so the
    persistence tests do not break

These tests are the test surface the planning prompt §5 references
("unit tests on ``TelemetryAdapter.adapt`` per source").
"""
from __future__ import annotations

import time
import unittest

from ground_station.service.schema_registry import SchemaRegistry
from ground_station.service.telemetry_adapter import (
    NormalizedSample,
    StreamMetadata,
    TelemetryAdapter,
)


def _adapter(**kwargs) -> TelemetryAdapter:
    """Build a TelemetryAdapter with a builtin dashboard registry."""
    return TelemetryAdapter(
        SchemaRegistry.builtin_dashboard(),
        **kwargs,
    )


def _streams():
    """Return an empty streams dict.

    Tests share one dict across multiple ``apply`` calls so merges work
    correctly. The previous version of these tests created a fresh dict
    per call, which silently defeated the merge semantics.
    """
    return {}


class TestResolveSlot(unittest.TestCase):
    """resolve_slot maps frame tags to dashboard slots."""

    def test_typed_subscribe_tags(self):
        a = _adapter()
        self.assertEqual(a.resolve_slot("s0"), 0)
        self.assertEqual(a.resolve_slot("s1"), 1)
        self.assertEqual(a.resolve_slot("s2"), 2)
        self.assertEqual(a.resolve_slot("s3"), 3)

    def test_legacy_frame_tags(self):
        a = _adapter()
        self.assertEqual(a.resolve_slot("a"), 0)    # Frame A / sidebar slot 0
        self.assertEqual(a.resolve_slot("b"), 1)    # Frame B / typed slot 1
        self.assertEqual(a.resolve_slot("c"), 3)    # Frame C / EKF slot 3
        self.assertEqual(a.resolve_slot("id"), 1)   # Frame ID counters slot 1

    def test_unknown_returns_none_not_negative(self):
        """Unknown tags must return None so they don't create phantom -1 slots."""
        a = _adapter()
        self.assertIsNone(a.resolve_slot("data"))
        self.assertIsNone(a.resolve_slot(""))
        self.assertIsNone(a.resolve_slot("garbage"))
        self.assertIsNone(a.resolve_slot("z"))

    def test_partial_s_prefix_only(self):
        """``s`` alone is not a typed tag; ``s0x`` is not either."""
        a = _adapter()
        self.assertIsNone(a.resolve_slot("s"))
        self.assertIsNone(a.resolve_slot("s0x"))


class TestAdaptFromDecoder(unittest.TestCase):
    """adapt_from_decoder -- raw 0x09+slot bytes via the typed decoder."""

    def test_returns_normalized_sample(self):
        a = _adapter()
        values = {"mrac_state.pitch.e": [0.05]}
        meta = StreamMetadata(sequence=42, source_time_ms=100, received=42,
                              dropped=1, loss_pct=2.3)
        s = a.adapt_from_decoder(slot=0, values=values, metadata=meta,
                                 received_ns=1000)
        self.assertEqual(s.slot, 0)
        self.assertEqual(s.tag, "typed")
        self.assertEqual(s.values["mrac_state.pitch.e"], [0.05])
        self.assertEqual(s.metadata.sequence, 42)
        self.assertEqual(s.received_ns, 1000)

    def test_default_received_ns_is_wall_clock(self):
        a = _adapter()
        before = time.time_ns()
        s = a.adapt_from_decoder(slot=0, values={}, metadata=StreamMetadata())
        after = time.time_ns()
        self.assertGreaterEqual(s.received_ns, before)
        self.assertLessEqual(s.received_ns, after)


class TestAdaptFromBridge(unittest.TestCase):
    """adapt_from_bridge -- the wifi_bridge typed payload + embedded metadata."""

    def test_extracts_slot_metadata(self):
        a = _adapter()
        payload = {
            "slot0.imu_data.rol": -0.5,
            "slot0.t_ms": 1234,
            "slot0.seq": 17,
            "slot0.received": 17,
            "slot0.dropped": 0,
            "slot0.loss_pct": 0.0,
        }
        s = a.adapt_from_bridge(slot=0, payload=payload, received_ns=2000)
        self.assertEqual(s.slot, 0)
        self.assertEqual(s.tag, "typed")
        # The bridge payload keeps ``slot0.*`` keys (legacy back-compat),
        # plus the metadata was hoisted.
        self.assertEqual(s.metadata.sequence, 17)
        self.assertEqual(s.metadata.source_time_ms, 1234)
        self.assertEqual(s.metadata.received, 17)
        self.assertEqual(s.metadata.dropped, 0)
        self.assertEqual(s.metadata.loss_pct, 0.0)

    def test_missing_metadata_zeros(self):
        """A payload without embedded ``slotN.*`` produces zero metadata.

        This is the contract that lets the typed subscribe path emit
        a payload with no metadata keys and still let the service
        publish a stream entry with all metadata fields present (zeroed).
        The test pins the behaviour the S15 tests depend on
        (``test_stream_metadata_present_on_every_path``).
        """
        a = _adapter()
        payload = {"slot0.imu_data.rol": -0.5}
        s = a.adapt_from_bridge(slot=0, payload=payload, received_ns=1)
        self.assertEqual(s.metadata.sequence, 0)
        self.assertEqual(s.metadata.source_time_ms, 0)
        self.assertEqual(s.metadata.received, 0)


class TestAdaptSidebar(unittest.TestCase):
    """adapt_sidebar -- legacy Frame A/B/C mirror path with DWARF names."""

    def test_resolves_known_dwarf_names(self):
        a = _adapter()
        names = ["imu_data.rol", "DroneStatus.ARM_Status", "real_voltage"]
        values = [-0.94, 1.0, 12.4]
        s = a.adapt_sidebar(slot=0, dwarf_names=names, values=values,
                            received_ns=10)
        self.assertEqual(s.slot, 0)
        self.assertEqual(s.tag, "sidebar")
        self.assertEqual(s.values["status.roll_deg"], -0.94)
        self.assertEqual(s.values["status.arm"], 1.0)         # int key → round to 0
        self.assertEqual(s.values["status.vbat"], 12.4)
        # No silent drops: known names map; if we passed an unknown name it
        # would still be emitted under its raw DWARF path.

    def test_keeps_unknown_dwarf_names_under_raw_path(self):
        """Unmapped names emit under their raw DWARF name; no silent drops."""
        a = _adapter()
        names = ["some_new_field.just_now", "imu_data.rol"]
        values = [42.0, -0.5]
        s = a.adapt_sidebar(slot=0, dwarf_names=names, values=values,
                            received_ns=10)
        self.assertEqual(s.values["some_new_field.just_now"], 42.0)
        self.assertEqual(s.values["status.roll_deg"], -0.5)


class TestAdaptExternal(unittest.TestCase):
    """adapt_external -- SWD RTOS bridge / replay / test injection."""

    def test_string_slot_key(self):
        a = _adapter()
        s = a.adapt_external("rtos", {"rtos.send_ticks": 1234.0},
                            received_ns=1000)
        self.assertEqual(s.slot, "rtos")
        self.assertEqual(s.tag, "external")
        self.assertEqual(s.metadata.received, 1)
        self.assertEqual(s.values["rtos.send_ticks"], 1234.0)

    def test_bumps_sequence_counter_per_call(self):
        """Each call increments the per-slot sequence counter monotonically.

        ``adapt_external`` defaults ``received`` to 1 (the legacy semantics
        the RTOS-bridge test relies on); the monotonically-increasing
        counter is exposed as ``sequence``. ``inject_external_stream``
        wraps ``adapt_external`` and tracks ``received`` itself.
        """
        a = _adapter()
        seqs = []
        for i in range(3):
            s = a.adapt_external("rtos", {"x": float(i)})
            seqs.append(s.metadata.sequence)
        self.assertEqual(seqs, [1, 2, 3])


class TestApply(unittest.TestCase):
    """apply() -- the one merge logic for every path."""

    def test_first_ingest_creates_fresh_entry(self):
        a = _adapter()
        s = NormalizedSample(
            slot=0, tag="typed", values={"altitude": 1.5},
            received_ns=100, metadata=StreamMetadata(sequence=42, received=1),
        )
        streams = _streams()
        a.apply(s, streams)
        self.assertIn(0, streams)
        self.assertEqual(streams[0]["tag"], "typed")
        self.assertEqual(streams[0]["values"]["altitude"], 1.5)
        self.assertEqual(streams[0]["sequence"], 42)
        self.assertEqual(streams[0]["received"], 1)
        self.assertEqual(streams[0]["last_update_ns"], 100)
        self.assertEqual(streams[0]["_key_ts"]["altitude"], 100)

    def test_second_ingest_merges_values_not_overwrites(self):
        a = _adapter()
        streams = _streams()
        # First ingest with two keys
        a.apply(NormalizedSample(
            slot=0, tag="typed", values={"a": 1.0, "b": 2.0},
            received_ns=100, metadata=StreamMetadata(),
        ), streams)
        # Second ingest with a NEW key -- ``a`` must survive
        a.apply(NormalizedSample(
            slot=0, tag="typed", values={"c": 3.0},
            received_ns=200, metadata=StreamMetadata(),
        ), streams)
        self.assertEqual(streams[0]["values"]["a"], 1.0)   # preserved
        self.assertEqual(streams[0]["values"]["b"], 2.0)   # preserved
        self.assertEqual(streams[0]["values"]["c"], 3.0)   # new
        self.assertEqual(streams[0]["_key_ts"]["a"], 100)    # stale timestamp on ``a``
        self.assertEqual(streams[0]["_key_ts"]["c"], 200)   # fresh timestamp on ``c``

    def test_metadata_uses_max_not_sum(self):
        """``meta.X or existing.get(X)`` keeps the most-informative value.

        The typed subscribe path carries its own metadata; the sidebar path
        uses 0 for source_time_ms (no wire clock on that path). When the
        sidebar path arrives second, its zero must NOT clobber the typed
        path's real value.
        """
        a = _adapter()
        streams = _streams()
        # Typed path with real metadata
        a.apply(NormalizedSample(
            slot=0, tag="typed", values={"x": 1.0},
            received_ns=100,
            metadata=StreamMetadata(sequence=42, source_time_ms=1234,
                                    received=42, dropped=1, loss_pct=2.3),
        ), streams)
        # Sidebar path with zero metadata -- must not clobber
        a.apply(NormalizedSample(
            slot=0, tag="sidebar", values={"y": 2.0},
            received_ns=200, metadata=StreamMetadata(),
        ), streams)
        self.assertEqual(streams[0]["sequence"], 42)        # preserved
        self.assertEqual(streams[0]["source_time_ms"], 1234)  # preserved
        self.assertEqual(streams[0]["received"], 42)         # preserved
        self.assertEqual(streams[0]["dropped"], 1)           # preserved
        self.assertEqual(streams[0]["loss_pct"], round(100.0 / 43, 3))  # derived from received/dropped
        self.assertEqual(streams[0]["values"]["y"], 2.0)     # new value merged
        self.assertEqual(streams[0]["last_update_ns"], 200)  # updated
        self.assertEqual(streams[0]["tag"], "sidebar")       # tag updated

    def test_two_paths_share_same_slot_via_different_tags(self):
        """Typed and sidebar converge into one stream entry with merged values."""
        a = _adapter()
        streams = _streams()
        a.apply(NormalizedSample(
            slot=0, tag="typed", values={"slot0.imu_data.rol": -0.5},
            received_ns=10,
            metadata=StreamMetadata(sequence=1, received=1),
        ), streams)
        a.apply(NormalizedSample(
            slot=0, tag="sidebar",
            values={"status.roll_deg": -0.94},
            received_ns=20,
            metadata=StreamMetadata(),
        ), streams)
        # Both keys live in the SAME slot 0 entry.
        self.assertIn(0, streams)
        self.assertEqual(streams[0]["values"]["slot0.imu_data.rol"], -0.5)
        self.assertEqual(streams[0]["values"]["status.roll_deg"], -0.94)


class TestEvictStale(unittest.TestCase):
    """evict_stale -- freshness TTL with wall-clock carve-out."""

    def test_evicts_stale_wall_clock_slot(self):
        a = _adapter(freshness_ttl_ns=1_000_000_000)  # 1 s TTL
        # Service started at t=1ns; we poll at t=10s. The TTL is 1s so
        # anything with last_update_ns < 9s is stale.
        now = 10_000_000_000
        started_at = 1
        streams = {
            "old": {"last_update_ns": 5_000_000_000, "values": {}},
            "fresh": {"last_update_ns": now, "values": {}},
        }
        evicted = a.evict_stale(streams, now, started_at_ns=started_at)
        self.assertEqual(evicted, ["old"])
        self.assertNotIn("old", streams)
        self.assertIn("fresh", streams)

    def test_relative_timestamps_not_evicted(self):
        """Slots whose ``last_update_ns`` predates service start are kept.

        This is the carve-out that preserves the persist/replay tests
        (which pass ``time_ns=10``). Without it the tests break because
        the adapter would evict every test fixture immediately.
        """
        a = _adapter(freshness_ttl_ns=1_000_000_000)
        now = 10_000_000_000
        started_at = 9_000_000_000
        streams = {
            "relative": {"last_update_ns": 10, "values": {}},  # way before start
        }
        evicted = a.evict_stale(streams, now, started_at_ns=started_at)
        self.assertEqual(evicted, [])
        self.assertIn("relative", streams)

    def test_started_at_zero_disables_eviction(self):
        """``started_at_ns=0`` (the legacy carve-out) keeps every slot.

        Mirrors the S15 behaviour where ``evict_stale`` would be a no-op
        when the service hadn't yet captured a start timestamp. Useful
        for tests that call ``evict_stale`` without going through
        ``snapshot``.
        """
        a = _adapter(freshness_ttl_ns=1)
        streams = {
            "anything": {"last_update_ns": 100, "values": {}},
        }
        evicted = a.evict_stale(streams, 200, started_at_ns=0)
        self.assertEqual(evicted, [])
        self.assertIn("anything", streams)


class TestStreamMetadataBackwardsCompat(unittest.TestCase):
    """extract_metadata -- transitional helper for payload-mutating bridge.

    New code should pass ``StreamMetadata`` directly to
    ``adapt_from_bridge``. This helper exists only so the wifi_bridge can
    migrate incrementally without breaking.
    """

    def test_reads_all_five_slot_n_keys(self):
        a = _adapter()
        meta = a.extract_metadata(
            tag="s0",
            slot=0,
            telemetry={
                "slot0.seq":       17,
                "slot0.t_ms":      1234,
                "slot0.received":  42,
                "slot0.dropped":   1,
                "slot0.loss_pct":  2.3,
                "slot0.imu_data.rol": -0.5,  # not a meta key
            },
        )
        self.assertEqual(meta.sequence, 17)
        self.assertEqual(meta.source_time_ms, 1234)
        self.assertEqual(meta.received, 42)
        self.assertEqual(meta.dropped, 1)
        self.assertEqual(meta.loss_pct, 2.3)

    def test_missing_keys_default_to_zero(self):
        a = _adapter()
        meta = a.extract_metadata("s0", 0, {"slot0.imu_data.rol": -0.5})
        self.assertEqual(meta.sequence, 0)
        self.assertEqual(meta.received, 0)
        self.assertEqual(meta.loss_pct, 0.0)

    def test_garbage_values_become_zero(self):
        """Non-numeric payload values don't crash the adapter."""
        a = _adapter()
        meta = a.extract_metadata("s0", 0, {
            "slot0.seq":       "garbage",
            "slot0.loss_pct":  None,
        })
        self.assertEqual(meta.sequence, 0)
        self.assertEqual(meta.loss_pct, 0.0)


class TestLossPctStuckRegression(unittest.TestCase):
    """Regression: loss_pct must not be stuck at a stale value after preset load.

    When a preset is loaded, subscribe_slot() clears the bridge's
    ``_stream_stats`` (``wifi_bridge.py:1018``) while the service's
    ``_streams`` entry persists with an old high ``loss_pct``.
    Fresh frames then carry ``meta.loss_pct=0.0`` which, with the
    old ``or`` logic, could not clobber the stale value.

    The fix computes ``loss_pct`` from the merged ``received`` + ``dropped``
    counters, so it always equals ``100*dropped/(received+dropped)``.
    """

    def test_loss_pct_updates_after_preset_load(self):
        """Simulate preset-load: old slot entry, fresh frame with low loss."""
        a = _adapter()
        streams = _streams()
        # Step 1: Initial ingest — decoder had few frames, many drops.
        # Simulates the very first frames after service start or preset load.
        a.apply(NormalizedSample(
            slot=0, tag="typed", values={"x": 1.0},
            received_ns=100,
            metadata=StreamMetadata(received=32, dropped=400, loss_pct=92.753),
        ), streams)
        # loss_pct should be 100*400/(32+400) ≈ 92.753
        self.assertAlmostEqual(streams[0]["loss_pct"], 92.753, places=2)
        # Step 2: More frames arrive, many clean (no drops).
        a.apply(NormalizedSample(
            slot=0, tag="typed", values={"y": 2.0},
            received_ns=200,
            metadata=StreamMetadata(received=10253, dropped=220, loss_pct=0.0),
        ), streams)
        # loss_pct must be recomputed from merged counters, not stuck at 92.753.
        expected = round(100.0 * 220 / (10253 + 220), 3)
        self.assertAlmostEqual(streams[0]["loss_pct"], expected, places=2)
        self.assertNotEqual(streams[0]["loss_pct"], 92.753)

    def test_loss_pct_zero_when_no_drops(self):
        """Zero loss is reported as 0.0, not preserved from a prior value."""
        a = _adapter()
        streams = _streams()
        # First: some drops.
        a.apply(NormalizedSample(
            slot=0, tag="typed", values={"x": 1.0},
            received_ns=100,
            metadata=StreamMetadata(received=10, dropped=5, loss_pct=33.333),
        ), streams)
        # Second: no new drops, clean frame — loss should update to 0.0.
        a.apply(NormalizedSample(
            slot=0, tag="typed", values={"y": 2.0},
            received_ns=200,
            metadata=StreamMetadata(received=20, dropped=5, loss_pct=0.0),
        ), streams)
        # received increased from 10 to 20, dropped stays at 5.
        self.assertEqual(streams[0]["received"], 20)
        self.assertEqual(streams[0]["dropped"], 5)
        expected = round(100.0 * 5 / 25, 3)
        self.assertAlmostEqual(streams[0]["loss_pct"], expected, places=2)

    def test_sidebar_does_not_clobber_bridge_counters(self):
        """Sidebar (meta.received=0) must not reset loss_pct via the or trick.

        The sidebar path carries zero metadata so that ``or`` preserves
        the bridge's counters.  The fix computes loss_pct from those
        preserved counters, so the result is still correct.
        """
        a = _adapter()
        streams = _streams()
        # Typed path sets counters and loss_pct.
        a.apply(NormalizedSample(
            slot=0, tag="typed", values={"x": 1.0},
            received_ns=100,
            metadata=StreamMetadata(received=42, dropped=1, loss_pct=2.3),
        ), streams)
        # Sidebar arrives with zero metadata — must not clobber.
        a.apply(NormalizedSample(
            slot=0, tag="sidebar", values={"y": 2.0},
            received_ns=200,
            metadata=StreamMetadata(),
        ), streams)
        self.assertEqual(streams[0]["received"], 42)
        self.assertEqual(streams[0]["dropped"], 1)
        expected = round(100.0 * 1 / 43, 3)
        self.assertAlmostEqual(streams[0]["loss_pct"], expected, places=2)