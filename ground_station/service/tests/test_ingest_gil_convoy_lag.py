"""Frame age must stay under the 200 ms ingest-lag limit while another Python thread is busy.

The ingest thread used to send the per-frame dashboard mirror (a UDP sendto) itself. A sendto drops the GIL, and
getting it back from a busy thread costs up to one switch interval (5 ms), so with any CPU-bound thread in the
service (a campaign step, an HTTP handler) every frame paid that wait and the queue grew: the soak saw 778 ms
mid-flight with no lock held. This drives the real bridge ingest loop, the real mirror socket and the real service
at 100 Hz next to one busy thread, then checks every frame's age and that every mirror message still arrives.
"""
from __future__ import annotations

import json
import socket
import threading
import time

from ground_station.comm.wifi_bridge import WifiBridge
from ground_station.service.core import GroundStationService
from ground_station.service.storage import SessionStore

LIMIT_MS = 200          # wifi_bridge INGEST_LAG_WARN_NS, the soak's pass line
RATE_HZ = 100
FRAMES = 300


def test_frame_age_stays_under_limit_next_to_a_busy_python_thread():
    store = SessionStore(max_rows=10_000)
    svc = GroundStationService(store=store, source="sim")
    svc.start()
    bridge = WifiBridge(vofa_enabled=False)
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
    listener.bind(("127.0.0.1", 0))
    listener.setblocking(False)
    bridge._telem_udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    bridge._telem_udp.setblocking(False)                 # as WifiBridge.start() opens it
    bridge._telem_udp_port = listener.getsockname()[1]

    def ingest(tag, payload, t_ns):                     # as ground_station/service/__main__.py wires it
        svc.ingest_decoded(tag, payload, time_ns=t_ns)

    ingest.takes_time_ns = True
    bridge._on_telemetry = ingest
    threads = [threading.Thread(target=bridge._ingest_loop, daemon=True)]
    if hasattr(bridge, "_mirror_loop"):
        threads.append(threading.Thread(target=bridge._mirror_loop, daemon=True))
    for t in threads:
        t.start()

    stop = threading.Event()

    def busy():                                         # pure Python, never sleeps: holds the GIL until forced off
        x = 0
        while not stop.is_set():
            for i in range(1000):
                x += i * i

    spinner = threading.Thread(target=busy, daemon=True)
    t_start = time.time_ns()
    got = []
    try:
        spinner.start()
        t0 = time.perf_counter()
        for seq in range(FRAMES):
            payload = {f"v{i}": float(i + seq) for i in range(32)}
            payload["seq"] = seq
            bridge._ingest_q.put(("s0", payload, time.time_ns()))
            while time.perf_counter() < t0 + (seq + 1) / RATE_HZ:
                time.sleep(0.0005)
        deadline = time.monotonic() + 10.0
        while (not bridge._ingest_q.empty() or bridge.__dict__.get("_mirror_q")) and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.1)
    finally:
        stop.set()
        bridge._stop.set()
        for t in threads:
            t.join(timeout=2.0)
    while True:
        try:
            got.append(json.loads(listener.recv(65536))["s0"]["seq"])
        except BlockingIOError:
            break
    listener.close()
    bridge._telem_udp.close()

    snap = bridge.ingest_lag_hist.snapshot(since_s=t_start // 1_000_000_000)
    assert snap["total"] == FRAMES
    assert snap["max_ms"] < LIMIT_MS, snap
    assert snap["over_limit"] == 0, snap
    assert got == list(range(FRAMES))                   # every per-frame mirror message, in order
