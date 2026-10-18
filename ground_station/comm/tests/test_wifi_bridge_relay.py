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


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_relay_waits_while_the_port_is_held_then_binds_and_forwards():
    """Lab 10-18: a leftover stream_log held 47800 at service start and the fixes were off for the whole service."""
    port = _free_udp_port()
    holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    holder.bind(("127.0.0.1", port))
    b = WifiBridge(vofa_enabled=False, wifi_host="192.168.4.1", wifi_port=14550, relay_port=port)
    assert b.relay_state == "waiting"
    assert b._bind_relay() is False and b._relay is None and b.relay_state == "waiting"
    holder.close()
    assert b._bind_relay() is True and b.relay_state == "bound"
    send = _FakeSend()
    b._wifi_send = send
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.sendto(b"\xaa\x03fix", ("127.0.0.1", port))
    real = b._relay
    b._relay = _FakeRelay(b, [real.recvfrom(256)[0]])
    b._relay_loop()
    real.close()
    tx.close()
    assert send.sent == [(b"\xaa\x03fix", ("192.168.4.1", 14550))] and b.relayed == 1


def test_relay_loop_retries_the_bind_instead_of_giving_up():
    b = WifiBridge(vofa_enabled=False, relay_port=47800)
    tries = []

    def fake_bind():
        tries.append(1)
        if len(tries) == 3:
            b._stop.set()
        return False

    b._bind_relay = fake_bind
    b._stop.wait = lambda t: None
    b._relay_loop()
    assert len(tries) == 3 and b._relay is None
