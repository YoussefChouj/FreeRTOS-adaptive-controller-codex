"""The bridge relays live_tracker camera fixes (udp 47800) to the drone through its own 14550 socket.

The MicoAir module sends its downlink to the source of the last uplink datagram, so the fixes cannot go out on a
second host socket while 8081 owns the link (stream_log --relay-port does the same for the no-8081 path).
"""
from __future__ import annotations

import socket

from ground_station.comm.wifi_bridge import WifiBridge


class _FakeRelay:
    def __init__(self, bridge, frames):
        self.bridge, self.frames = bridge, list(frames)

    def recvfrom(self, n):
        if not self.frames:
            self.bridge._stop.set()
            raise socket.timeout
        f = self.frames.pop(0)
        if isinstance(f, Exception):
            raise f
        return f, ("127.0.0.1", 50000)


class _FakeSend:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))


def test_relay_forwards_frames_unchanged_to_the_module():
    b = WifiBridge(vofa_enabled=False, wifi_host="192.168.4.1", wifi_port=14550, relay_port=47800)
    send = _FakeSend()
    b._wifi_send = send
    b._relay = _FakeRelay(b, [b"\xaa\x01fix", socket.timeout(), b"\xaa\x02fix"])
    b._relay_loop()
    assert send.sent == [(b"\xaa\x01fix", ("192.168.4.1", 14550)), (b"\xaa\x02fix", ("192.168.4.1", 14550))]
    assert b.relayed == 2


def test_relay_off_by_default():
    b = WifiBridge(vofa_enabled=False)
    assert b._relay_port is None and b._relay is None and b.relayed == 0
