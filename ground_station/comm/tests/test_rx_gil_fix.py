"""The rx thread keeps up with the armed sysid rate next to busy Python threads.

Lab 2026-10-24 (rate-sysid-roll-pitch, armed, ~450 frames/s): command replies came back seconds late. py-spy showed
the rx thread 73% waiting to get the GIL back after recvfrom and 16% in the VOFA+ sendto (a second handoff per
datagram). The bridge now lowers the switch interval when it starts and sends VOFA+ from its own thread.
"""
from __future__ import annotations

import socket
import struct
import sys
import threading
import time

import pytest

from ground_station.comm.wifi_bridge import RX_SWITCH_INTERVAL_S, WifiBridge, apply_rx_switch_interval


def _spare_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_switch_interval_is_lowered_and_never_raised():
    before = sys.getswitchinterval()
    try:
        sys.setswitchinterval(0.005)
        assert apply_rx_switch_interval() == pytest.approx(RX_SWITCH_INTERVAL_S)
        sys.setswitchinterval(0.0001)
        assert apply_rx_switch_interval() == pytest.approx(0.0001)
    finally:
        sys.setswitchinterval(before)


def test_bridge_start_lowers_the_switch_interval():
    before = sys.getswitchinterval()
    sys.setswitchinterval(0.005)
    b = WifiBridge(vofa_enabled=False, wifi_host="127.0.0.1", wifi_port=_spare_udp_port(),
                   cmd_udp_port=_spare_udp_port(), local_port=_spare_udp_port())
    b.start(auto_subscribe_boot_default=False)
    try:
        assert sys.getswitchinterval() <= RX_SWITCH_INTERVAL_S * 1.01
    finally:
        b.stop()
        sys.setswitchinterval(before)


def test_vofa_frames_leave_from_the_vofa_thread_not_the_caller():
    sink = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sink.bind(("127.0.0.1", 0))
    sink.settimeout(2.0)
    b = WifiBridge(vofa_enabled=True, wifi_host="127.0.0.1", wifi_port=_spare_udp_port(),
                   cmd_udp_port=_spare_udp_port(), local_port=_spare_udp_port())
    b._vofa_host = "127.0.0.1"
    b._vofa_port_a = sink.getsockname()[1]
    b.start(auto_subscribe_boot_default=False)
    senders: list[str] = []
    real = b._udp_send

    class _Spy:
        def sendto(self, data, addr):
            senders.append(threading.current_thread().name)
            return real.sendto(data, addr)

    b._udp_send = _Spy()
    try:
        for i in range(50):
            b._forward_vofa(1, [float(i), 2.0])
        got = []
        deadline = time.monotonic() + 2.0
        while len(got) < 50 and time.monotonic() < deadline:
            data, _ = sink.recvfrom(2048)
            got.append(struct.unpack("<2f", data[:8])[0])
        assert got == [float(i) for i in range(50)], "every VOFA+ frame arrives, in order"
        assert senders and set(senders) == {"wifi_bridge_vofa"}, f"sendto ran on {set(senders)}"
    finally:
        b._udp_send = real
        b.stop()
        sink.close()
