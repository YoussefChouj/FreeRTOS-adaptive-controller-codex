"""The link's hidden queue is visible, and the link port can move.

Lab 2026-10-24 (rate-sysid-roll-pitch, armed): command replies came back seconds late while the ingest queue was
empty. The backlog sat where nothing measured it: the kernel receive buffer of the link socket. link_health() now
reports its size (rx_pending_bytes), and a soak can run the link on a spare port next to the lab's 8081.
"""
from __future__ import annotations

import socket
import sys

from ground_station.comm.wifi_bridge import WifiBridge, socket_pending_bytes


def _spare_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_pending_bytes_counts_unread_datagrams():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        rx.bind(("127.0.0.1", 0))
        assert socket_pending_bytes(rx) == 0
        for _ in range(3):
            tx.sendto(b"x" * 100, rx.getsockname())
        rx.settimeout(2.0)
        import time
        deadline = time.monotonic() + 2.0
        while (socket_pending_bytes(rx) or 0) < (300 if sys.platform == "win32" else 100):
            assert time.monotonic() < deadline, f"pending stayed at {socket_pending_bytes(rx)}"
            time.sleep(0.01)
        rx.recvfrom(2048)
        # Windows reports the whole queue; Linux only the next datagram.
        assert socket_pending_bytes(rx) == (200 if sys.platform == "win32" else 100)
    finally:
        rx.close()
        tx.close()


def test_pending_bytes_is_none_for_a_closed_socket():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.close()
    assert socket_pending_bytes(s) is None


def test_bridge_binds_the_link_on_the_requested_local_port():
    local, cmd, module = _spare_udp_port(), _spare_udp_port(), _spare_udp_port()
    b = WifiBridge(vofa_enabled=False, wifi_host="127.0.0.1", wifi_port=module, cmd_udp_port=cmd, local_port=local)
    b.start(auto_subscribe_boot_default=False)
    try:
        assert b._wifi.getsockname()[1] == local
        assert b._cmd_udp.getsockname()[1] == cmd
        assert b.link_health()["rx_pending_bytes"] == 0
    finally:
        b.stop()
