"""Single-instance guard for the 8081 service (WP-23: two 8081 instances once ran at the same time).

On Windows ``HTTPServer`` binds with SO_REUSEADDR, which there lets a second process bind the same port, so two
services both "started" and split the requests. Two layers stop that:

1. ``ensure_single_instance(port)`` runs first in ``__main__``, before the bridge opens the drone link: if anything
   already answers on the port it raises ``AlreadyRunning`` with one clear line.
2. ``ExclusiveThreadingHTTPServer`` binds without address reuse on Windows (SO_EXCLUSIVEADDRUSE), so a race
   past layer 1 still fails at bind.

``INSTANCE_ID`` names this process; every JSON reply carries it in ``X-GS-Instance`` so a client (the MCP
server) can tell that 8081 restarted between two calls.
"""

from __future__ import annotations

import os
import socket
import time
from http.server import ThreadingHTTPServer

INSTANCE_ID = f"{os.getpid()}-{time.time_ns() // 1_000_000}"
INSTANCE_HEADER = "X-GS-Instance"


class AlreadyRunning(RuntimeError):
    pass


class BridgePortBusy(RuntimeError):
    pass


def port_in_use(port: int, host: str = "127.0.0.1", timeout_s: float = 0.5) -> bool:
    """True when something accepts a TCP connection on host:port."""
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def ensure_single_instance(port: int, host: str = "127.0.0.1") -> None:
    if port_in_use(port, host):
        raise AlreadyRunning(
            f"port {port} already has a listener: another ground-station service is running. "
            f"Stop it first (Windows: netstat -ano | findstr :{port}, then taskkill /PID <pid>), then start again.")


def ensure_bridge_port_free(port: int = 14550, wait_s: float = 3.0) -> None:
    """Refuse to start when another process already holds the wildcard bridge port.

    The bridge binds 0.0.0.0:14550 with SO_REUSEADDR, so a second service started next to a running one
    shares the port without an error, and Windows hands the drone's replies to the older socket: the new
    service gets no frames at all and every flight ends on "link lost" with no hint of the cause
    (lab 10-22 deck soak, open item ``bridge-port-shared-silently``).

    A bind probe without SO_REUSEADDR cannot see it as a bind error, because the bind succeeds; the probe
    is what detects it (Windows: WSAEADDRINUSE when a wildcard holder exists).

    ``wait_s`` gives a just-killed predecessor time to release the port: ``fly`` restarts the service
    after a code change, and the old process may still hold the socket for a moment.
    """
    from ground_station.comm.wifi_bridge import udp_port_holder

    deadline = time.monotonic() + max(0.0, wait_s)
    holders: list[str] = []
    while True:
        holders = udp_port_holder(port, exclude_pid=os.getpid())
        if not holders or time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    if not holders:
        return
    raise BridgePortBusy(
        f"UDP {port} is already held by another process ({'; '.join(holders)}). "
        f"One service per laptop: stop the other one first "
        f"(Windows: netstat -ano | findstr :{port}, then taskkill /PID <pid>), then start again.")


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer whose bind fails while another process holds the port (also on Windows)."""

    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        excl = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if os.name == "nt" and excl is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, excl, 1)
        super().server_bind()
