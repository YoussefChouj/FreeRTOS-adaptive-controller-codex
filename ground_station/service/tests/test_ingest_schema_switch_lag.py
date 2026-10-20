"""Frame age across a schema switch must stay under the 200 ms ingest-lag limit.

At a flight start the drone switches schema and the first frames of the new
schema read back the gain the campaign just wrote, so the ingest thread
verifies the command and records a ``command_verified`` event. That event was
an INSERT + commit under the store lock, which the writer thread holds for a
whole batch (prune DELETE, commit, WAL checkpoint). This drives the real
bridge ingest loop into the real service and holds the store lock as a slow
writer batch would, then checks the age of every frame.
"""
from __future__ import annotations

import threading
import time

from ground_station.comm.wifi_bridge import WifiBridge
from ground_station.service.core import CommandAction, CommandLifecycle, GroundStationService
from ground_station.service.storage import SessionStore

MS = 1_000_000
LIMIT_MS = 200          # wifi_bridge INGEST_LAG_WARN_NS, the soak's pass line
WRITER_HOLD_S = 0.6     # one slow writer batch, longer than the 0.5 s full3 stall
RATE_HZ = 100


def _applied(txid: int, value: float) -> CommandAction:
    return CommandAction(
        transaction_id=txid, command_id=15, index=10, value=value, flags=0,
        lifecycle=CommandLifecycle.APPLIED, outcome_name="APPLIED", reason_name=None,
        wire_bytes=12, submitted_ns=0, resolved_ns=1,
    )


def test_frame_age_stays_under_limit_when_a_schema_switch_verifies_a_command():
    store = SessionStore(max_rows=10_000)
    svc = GroundStationService(store=store, source="sim")
    sid = svc.start()
    svc._action_journal.append(_applied(7, 2.5))        # the gain write the campaign just made
    svc._pending_verifications[(15, 10)] = [7]

    bridge = WifiBridge(vofa_enabled=False)
    bridge._publish_telem = lambda *a: None

    def ingest(tag, payload, t_ns):                     # as ground_station/service/__main__.py wires it
        svc.ingest_decoded(tag, payload, time_ns=t_ns)

    ingest.takes_time_ns = True
    bridge._on_telemetry = ingest
    worker = threading.Thread(target=bridge._ingest_loop, daemon=True)
    worker.start()

    held, release = threading.Event(), threading.Event()

    def slow_writer_batch():
        with store._lock:
            held.set()
            release.wait(WRITER_HOLD_S)

    t_start = time.time_ns()
    try:
        for seq in range(20):                           # old schema on slot 0: no readback match
            bridge._ingest_q.put(("s0", {"alt": 1.0, "seq": seq}, time.time_ns()))
            time.sleep(1 / RATE_HZ)
        writer = threading.Thread(target=slow_writer_batch)
        writer.start()
        assert held.wait(2.0)
        for seq in range(int(WRITER_HOLD_S * RATE_HZ) + 20):   # new schema: reads back the written gain
            bridge._ingest_q.put(("s1", {"inj": 2.5, "seq": seq}, time.time_ns()))
            time.sleep(1 / RATE_HZ)
        release.set()
        writer.join()
        deadline = time.monotonic() + 2.0
        while not bridge._ingest_q.empty() and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
    finally:
        release.set()
        bridge._stop.set()
        worker.join(timeout=2.0)

    assert svc._action_journal[0].lifecycle == CommandLifecycle.VERIFIED
    snap = bridge.ingest_lag_hist.snapshot(since_s=t_start // 1_000_000_000)
    assert snap["total"] >= 100
    assert snap["max_ms"] < LIMIT_MS, snap
    assert snap["over_limit"] == 0
    kinds = [r["kind"] for r in store.iter_records(sid) if r["type"] == "event"]
    assert "command_verified" in kinds                  # the event is still stored, after the writer
