from ground_station.comm.wifi_bridge import WifiBridge
from ground_station.platform.transactions import (
    Outcome, RejectReason, Result, build_result,
)


def test_wifi_bridge_decodes_transaction_result_frame():
    bridge = WifiBridge(vofa_enabled=False)
    expected = Result(0x4243, Outcome.REJECTED, 0xFE, 0,
                      RejectReason.UNKNOWN_COMMAND, "command rejected")
    rx = bytearray(build_result(expected))
    decoded = bridge._parse_one(rx)
    assert decoded == ("transaction", expected)
    assert rx == bytearray()


def test_wifi_bridge_rx_loop_queues_transaction_result():
    bridge = WifiBridge(vofa_enabled=False)
    expected = Result(7, Outcome.APPLIED, 1, 0, detail="applied")
    decoded = bridge._parse_one(bytearray(build_result(expected)))
    assert decoded[0] == "transaction"
    bridge._transaction_results.put(decoded[1])
    assert bridge.poll_transaction_result() == expected


def test_reply_not_delayed_by_slow_telemetry_ingest():
    """10-18 P12: a slow ingest/record callback must not delay a command reply behind the stream."""
    import socket
    import struct
    import threading
    import time

    bridge = WifiBridge(vofa_enabled=False)
    bridge._check_resubscribe = lambda: None
    bridge._publish_telem = lambda *a: None
    attitude = struct.pack("<3f", 1.0, 2.0, 3.0) + b"\x00\x00\x80\x7f"
    expected = Result(9, Outcome.APPLIED, 1, 0, detail="applied")
    datagrams = [attitude] * 20 + [bytes(build_result(expected))]

    class FakeSock:
        def recvfrom(self, _n):
            if datagrams:
                return datagrams.pop(0), ("127.0.0.1", 14550)
            time.sleep(0.01)
            raise socket.timeout

    seen = []

    def slow_ingest(tag, payload, t_ns):
        time.sleep(0.05)  # 20 frames = 1 s of ingest backlog
        seen.append(t_ns)

    slow_ingest.takes_time_ns = True
    bridge._wifi = FakeSock()
    bridge._on_telemetry = slow_ingest
    t0 = time.monotonic()
    threads = [threading.Thread(target=bridge._rx_loop, daemon=True),
               threading.Thread(target=bridge._ingest_loop, daemon=True)]
    for t in threads:
        t.start()
    try:
        assert bridge._transaction_results.get(timeout=0.5) == expected
        assert time.monotonic() - t0 < 0.2  # inline ingest would take about 1 s
        deadline = time.monotonic() + 3.0
        while len(seen) < 20 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(seen) == 20  # nothing lost, only deferred
        assert max(seen) - min(seen) < 0.2e9  # stamped at arrival, not at ingest (ingest spans 1 s)
    finally:
        bridge._stop.set()
        for t in threads:
            t.join(timeout=1.0)


def test_link_health_tells_a_host_stall_from_a_link_loss():
    """10-18: two takeoff link losses could not be told apart. Real rx loop: while the ingest thread is stuck,
    datagrams and replies still arrive (ages stay small, the ingest queue grows); when the socket goes quiet, both
    ages grow."""
    import socket
    import struct
    import threading
    import time

    bridge = WifiBridge(vofa_enabled=False)
    bridge._check_resubscribe = lambda: None
    bridge._publish_telem = lambda *a: None
    assert bridge.link_health()["rx_age_s"] is None and bridge.link_health()["reply_age_s"] is None
    attitude = struct.pack("<3f", 1.0, 2.0, 3.0) + b"\x00\x00\x80\x7f"
    datagrams = [attitude] * 20 + [bytes(build_result(Result(9, Outcome.APPLIED, 1, 0, detail="applied")))]

    class FakeSock:
        def recvfrom(self, _n):
            if datagrams:
                return datagrams.pop(0), ("127.0.0.1", 14550)
            time.sleep(0.01)
            raise socket.timeout

    release = threading.Event()

    def stuck_ingest(tag, payload, t_ns):
        release.wait(5.0)                           # the host stall: ingest blocks, rx keeps reading

    stuck_ingest.takes_time_ns = True
    bridge._wifi = FakeSock()
    bridge._on_telemetry = stuck_ingest
    threads = [threading.Thread(target=bridge._rx_loop, daemon=True),
               threading.Thread(target=bridge._ingest_loop, daemon=True)]
    for t in threads:
        t.start()
    try:
        bridge._transaction_results.get(timeout=1.0)
        stall = bridge.link_health()
        assert stall["rx_age_s"] < 0.2 and stall["reply_age_s"] < 0.2
        assert stall["ingest_queue"] >= 15         # 20 frames in, the stuck thread holds one
        time.sleep(0.4)                             # the socket stays quiet: the link-loss signature
        quiet = bridge.link_health()
        grew = (quiet["rx_age_s"] - stall["rx_age_s"], quiet["reply_age_s"] - stall["reply_age_s"])
        assert min(grew) > 0.3, grew                # both ages grow with the quiet time (15.6 ms clock on Windows)
    finally:
        release.set()
        bridge._stop.set()
        for t in threads:
            t.join(timeout=1.0)
