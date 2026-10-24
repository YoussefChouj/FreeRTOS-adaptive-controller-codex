"""The service refuses to start when another process already holds the bridge port.

Lab 10-22 deck soak (open item ``bridge-port-shared-silently``): the bridge binds 0.0.0.0:14550 with
SO_REUSEADDR. A second service started next to a running one binds without an error, and Windows hands the
drone's replies to the older socket. The new service sees no frames at all and every flight ends on
"link lost" with no hint of the cause.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys

import pytest

from ground_station.comm.wifi_bridge import udp_port_holder
from ground_station.service.instance_guard import BridgePortBusy, ensure_bridge_port_free


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


_HOLDER = """
import socket, sys, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", int(sys.argv[1])))
print("bound", flush=True)
time.sleep(30)
"""


def _start_holder(port: int) -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, "-c", _HOLDER, str(port)],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    assert proc.stdout.readline().strip() == "bound"
    return proc


def test_a_wildcard_holder_is_reported():
    port = _free_udp_port()
    proc = _start_holder(port)
    try:
        holders = udp_port_holder(port, exclude_pid=os.getpid())
    finally:
        proc.kill()
        proc.wait(timeout=10)
    assert any(f"pid {proc.pid}" in h for h in holders), f"expected the holder pid, got {holders!r}"


def test_a_loopback_only_holder_is_not_a_conflict():
    """The fake drone binds 127.0.0.2:14550 next to the bridge's 0.0.0.0:14550 in a soak: that pair works."""
    port = _free_udp_port()
    holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    holder.bind(("127.0.0.1", port))
    try:
        holders = udp_port_holder(port, exclude_pid=os.getpid())
    finally:
        holder.close()
    assert not any(f"pid {os.getpid()}" in h for h in holders), f"loopback holder must not block, got {holders!r}"


def test_our_own_pid_is_never_reported():
    port = _free_udp_port()
    holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("0.0.0.0", port))
    try:
        holders = udp_port_holder(port, exclude_pid=os.getpid())
    finally:
        holder.close()
    assert not any(f"pid {os.getpid()}" in h for h in holders), f"own pid must be filtered, got {holders!r}"


def test_ensure_bridge_port_free_raises_on_a_foreign_holder():
    port = _free_udp_port()
    proc = _start_holder(port)
    try:
        with pytest.raises(BridgePortBusy) as exc:
            ensure_bridge_port_free(port)
    finally:
        proc.kill()
        proc.wait(timeout=10)
    assert str(port) in str(exc.value) and "One service per laptop" in str(exc.value)


def test_ensure_bridge_port_free_is_silent_when_the_port_is_free():
    ensure_bridge_port_free(_free_udp_port())
