"""Soak harness for the telemetry ingest path.

Feeds synthetic telemetry frames through the same path ``python -m
ground_station.service`` wires (wifi_bridge _on_telemetry callback ->
service.ingest_decoded -> adapter -> store -> snapshot -> SSE).

Usage (standalone):
    python soak_ingest.py --frames 100000

Usage (pytest):
    pytest ground_station/service/tests/soak_ingest.py -v
"""
from __future__ import annotations

import os
import sys
import time
import tracemalloc
from pathlib import Path

# Ensure the repo root is on sys.path for imports
if "__file__" in dir():
    _repo = Path(__file__).resolve().parent.parent.parent.parent
else:
    _repo = Path.cwd()
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

from ground_station.livewatch.stream import StreamRange, StreamSchema
from ground_station.livewatch.transport import crc16_ccitt
from ground_station.service.agent import AgentManager
from ground_station.service.core import GroundStationService
from ground_station.service.storage import SessionStore

os.environ.setdefault("GS_SHELL_WATCH", "0")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _schema():
    """Two-slot schema matching the real subscribe-stream frame layout."""
    return StreamSchema(
        1, 1, 8,
        (
            StreamRange(0x20000000, 4, 1, "altitude", "f"),
            StreamRange(0x20000004, 4, 1, "arm", "f"),
        ),
        0,
    )


def _make_service():
    """Create a minimal service with an agent manager (no HTTP server)."""
    svc = GroundStationService(
        store=SessionStore(),
        schemas=[_schema()],
        source="sim",
        recorder=None,  # disable CSV recorder to isolate RAM usage
    )
    svc.start()
    agent = AgentManager(svc, shell_root=None, journal_root=None)
    agent.start()
    return svc, agent


# Feed frames through ingest_decoded (the bridge callback path).
def _feed_frame_a(service, i):
    """Feed a Frame A equivalent into slot 0 (tag 'a')."""
    service.ingest_decoded("a", {
        "mrac.pitch.e": float(i % 100) * 0.01,
        "mrac.pitch.u_ad": float(i % 80) * 0.01,
        "mrac.roll.e": float(i % 60) * 0.01,
        "mrac.roll.u_ad": float(i % 70) * 0.01,
        "mrac.yaw.e": float(i % 90) * 0.01,
        "mrac.yaw.u_ad": float(i % 50) * 0.01,
        "mrac.z.e": float(i % 40) * 0.01,
        "mrac.z.u_ad": float(i % 30) * 0.01,
        "status.arm": 1.0 if i % 2 else 0.0,
        "status.flymode": 2.0,
        "status.sbus_lost": 0.0,
        "status.twc_execute": 0.0,
        "status.twc_arrived": 1.0 if i % 3 else 0.0,
        "status.rc_authority": 1.0,
        "status.of_hold": 0.0,
        "status.estimator_ready": 1.0,
    }, time_ns=i * 1_000_000)


def _feed_subscribe_frame(service, slot, i):
    """Feed a subscribe-stream frame into the given slot."""
    service.ingest_decoded(f"s{slot}", {
        f"slot{slot}.altitude": 14.0 + (i % 1000) * 0.001,
        f"slot{slot}.arm": 1.0 if i % 2 else 0.0,
    }, time_ns=i * 1_000_000)


# ---------------------------------------------------------------------------
# Soak harness
# ---------------------------------------------------------------------------


def run_soak(frames_per_slot: int, short: bool = False) -> dict:
    """Feed frames through the ingest path and measure retained memory.

    Batches at 1 k / 5 k / 10 k when short, else 50 k / 100 k / 200 k.
    """
    svc, agent = _make_service()
    tracemalloc.start()
    gc_collect()

    batch_sizes = [50_000, 100_000, 200_000]
    if short:
        batch_sizes = [1_000, 5_000, 10_000]

    snapshots = []
    try:
        for batch in batch_sizes:
            if batch > frames_per_slot:
                break
            t0 = time.monotonic()
            for i in range(batch):
                _feed_frame_a(svc, i)
                _feed_subscribe_frame(svc, 0, i)
                _feed_subscribe_frame(svc, 1, i)
            elapsed = time.monotonic() - t0
            current, peak = tracemalloc.get_traced_memory()
            snap = svc.snapshot()
            snapshots.append({
                "batch": batch,
                "elapsed_s": elapsed,
                "current_mb": round(current / 1024 / 1024, 2),
                "peak_mb": round(peak / 1024 / 1024, 2),
                "samples": snap.samples,
            })
            print(f"  After {batch} frames: "
                  f"{current / 1024 / 1024:.1f} MB "
                  f"(peak={peak / 1024 / 1024:.1f} MB, "
                  f"{snap.samples} samples, {elapsed:.2f}s)")
    finally:
        gc_collect()
        current, peak = tracemalloc.get_traced_memory()
        agent.stop()
        svc.stop()
        tracemalloc.stop()

    # Top 15 memory consumers
    tracemalloc.start()
    gc_collect()
    snapshot = tracemalloc.take_snapshot()
    top15 = snapshot.statistics("lineno")[:15]
    tracemalloc.stop()
    top_info = [(str(s), round(s.size / 1024 / 1024, 2)) for s in top15]

    return {"snapshots": snapshots, "top15": top_info}


def gc_collect():
    """Import gc for the GC function."""
    import gc as _gc
    _gc.collect()
    _gc.collect()


# ---------------------------------------------------------------------------
# Pytest
# ---------------------------------------------------------------------------


def test_memory_growth_under_load():
    """Feeding 10k then 5k frames must grow < 5 MB between batches."""
    result = run_soak(frames_per_slot=10_000, short=True)
    snaps = result["snapshots"]

    print("\n=== Soak Results ===")
    for s in snaps:
        print(f"  {s['batch']} frames: {s['current_mb']:.1f} MB "
              f"(peak={s['peak_mb']:.1f} MB, samples={s['samples']})")

    # Memory growth between last two batches must be < 5 MB
    if len(snaps) >= 2:
        growth = snaps[-1]["current_mb"] - snaps[-2]["current_mb"]
        print(f"\n  Growth between last two batches: {growth:.1f} MB")
        assert growth < 5.0, (
            f"Memory grew {growth:.1f} MB between batches (limit: 5 MB)"
        )

    # Total memory after largest batch should be < 50 MB
    assert snaps[-1]["current_mb"] < 50, (
        f"Total memory {snaps[-1]['current_mb']:.1f} MB too high"
    )


def test_top_memory_consumers():
    """Print top memory consumers for debugging."""
    result = run_soak(frames_per_slot=10_000, short=True)
    print("\n=== Top 15 Memory Consumers ===")
    for location, size_mb in result["top15"]:
        print(f"  {size_mb:>8} MB  {location}")
