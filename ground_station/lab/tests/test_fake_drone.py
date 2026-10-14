"""Fake drone over real UDP: JustFloat until subscribed, schema + data frames, result frames, control words, reboot."""
from __future__ import annotations

import socket
import struct
import threading
import time

import pytest

from ground_station.lab.fake_drone import JUSTFLOAT_TAIL, FakeUdpDrone, ctl
from ground_station.livewatch.stream import StreamRange, build_stream_request, decode_schema
from ground_station.livewatch.symbols import Symbol
from ground_station.livewatch.transport import pop_frame
from ground_station.platform.transactions import Command, Outcome, build_command, parse_result_parts
from ground_station.platform.wfb_commands import CMD_ARM, CMD_PRIM, ArmIdx, PrimIdx

SYMS = {"DroneStatus.ARM_Status": "B", "real_voltage": "f", "g_wfb_status.prim_state": "f"}


class FakeResolver:
    def __init__(self):
        self.table = {n: Symbol(n, 0x20001000 + 8 * i, struct.calcsize(f), f) for i, (n, f) in enumerate(SYMS.items())}

    def resolve(self, name):
        return self.table[name]          # KeyError for the rest, like a name the .axf lacks


@pytest.fixture
def rig():
    drone = FakeUdpDrone(resolver=FakeResolver(), port=0, ctl_port=0, silent_s=0.3, vbat=15.5, host="127.0.0.1")
    runner = threading.Thread(target=drone.run, kwargs={"until": time.monotonic() + 20}, daemon=True)
    runner.start()
    gs = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    gs.bind(("127.0.0.1", 0))
    gs.settimeout(0.2)
    yield drone, gs
    drone._stop = True
    runner.join(2)
    drone.close()
    gs.close()


def _send(drone, gs, data: bytes) -> None:
    gs.sendto(data, ("127.0.0.1", drone.port))


def _recv(gs, pred, n=1, timeout_s=2.0) -> list[bytes]:
    """The next n datagrams matching pred (the fake drone sends one frame per datagram)."""
    out, end = [], time.monotonic() + timeout_s
    while len(out) < n and time.monotonic() < end:
        try:
            d = gs.recv(4096)
        except OSError:                  # timeout, or a Windows ICMP reset
            continue
        if pred(d):
            out.append(d)
    assert len(out) == n, f"got {len(out)} of {n} datagrams"
    return out


def _is(*types):
    return lambda d: d[:2] == b"\xAA\xBB" and d[2] in types


def _justfloat(d: bytes) -> bool:
    return len(d) == 16 and d.endswith(JUSTFLOAT_TAIL)


def _subscribe(drone, gs, names, divider=1, slot=0):
    t = drone.resolver.table
    ranges = [StreamRange(t[n].address, t[n].size, name=n, fmt=t[n].fmt) for n in names]
    _send(drone, gs, build_stream_request(ranges, divider=divider, slot=slot))
    ftype, n, payload = pop_frame(bytearray(_recv(gs, _is(0x08))[0]))
    decode_schema(n, payload, ranges)
    return n, payload


def _data(gs, n=1, slot=0):
    return [pop_frame(bytearray(d)) for d in _recv(gs, _is(0x09 + slot), n=n)]


def test_justfloat_until_subscribed(rig):
    drone, gs = rig
    _send(drone, gs, b"\x00")            # the bridge's nudge
    _recv(gs, _justfloat)


def test_subscribe_echoes_schema_and_streams_values(rig):
    drone, gs = rig
    n, payload = _subscribe(drone, gs, ["DroneStatus.ARM_Status", "real_voltage"], divider=2)
    assert n == 2 and payload[:5] == struct.pack(">BBBH", 2, 1, 0, 5)
    frames = _data(gs, n=3)
    seqs = [byte5 for _, byte5, _ in frames]
    assert seqs == [(seqs[0] + k) & 0xFF for k in range(3)]
    for _, _, p in frames:
        assert len(p) == 4 + 5 and p[4] == 0
        assert struct.unpack_from("<f", p, 5)[0] == pytest.approx(15.5)


def test_arm_word_flips_the_streamed_arm_state(rig):
    drone, gs = rig
    _subscribe(drone, gs, ["DroneStatus.ARM_Status"])
    assert _data(gs)[0][2][4] == 0
    assert ctl("arm", port=drone.ctl_port, host="127.0.0.1") == "ok arm"
    assert drone.drone.armed and drone.drone.motors_idle
    assert _data(gs, n=5)[-1][2][4] == 1
    assert ctl("disarm", port=drone.ctl_port, host="127.0.0.1") == "ok disarm"


def test_heartbeat_gets_its_result_frame(rig):
    drone, gs = rig
    _send(drone, gs, build_command(Command(7, CMD_PRIM, PrimIdx.HEARTBEAT, 0.0)))
    d = _recv(gs, _is(0x30, 0x31, 0x32))[0]
    res = parse_result_parts(d[2], d[5], d[6:-1])            # wifi_bridge's own parse of 0x30-0x32
    assert (res.transaction_id, res.command_id, res.index) == (7, CMD_PRIM, PrimIdx.HEARTBEAT)
    assert res.outcome in (Outcome.ACK, Outcome.APPLIED)


def test_refused_command_is_rejected_with_a_reason(rig):
    drone, gs = rig
    _send(drone, gs, build_command(Command(9, CMD_ARM, ArmIdx.IDLE, 1.0)))   # motor init while disarmed
    d = _recv(gs, _is(0x31))[0]
    res = parse_result_parts(d[2], d[5], d[6:-1])
    assert res.transaction_id == 9 and res.outcome == Outcome.REJECTED and int(res.reason) != 0


def test_reboot_goes_silent_then_forgets_the_subscription(rig):
    drone, gs = rig
    _subscribe(drone, gs, ["DroneStatus.ARM_Status"])
    _data(gs)
    assert ctl("reboot", port=drone.ctl_port, host="127.0.0.1").startswith("ok reboot")
    t_reboot = time.monotonic()
    _recv(gs, _justfloat, timeout_s=3.0)
    assert time.monotonic() - t_reboot >= 0.25
    with pytest.raises(AssertionError):
        _recv(gs, _is(0x09), timeout_s=0.3)


def test_bad_control_words_are_errors_and_the_drone_keeps_running(rig):
    drone, _ = rig
    assert ctl("fly away", port=drone.ctl_port, host="127.0.0.1").startswith("error")
    assert ctl("vbat low", port=drone.ctl_port, host="127.0.0.1").startswith("error")
    assert ctl("vbat 15.1", port=drone.ctl_port, host="127.0.0.1") == "ok vbat 15.10"
