"""
conftest.py — ground_station/comm/tests/

Provides pytest fixtures for hardware integration tests against the live drone.

The drone (MicoAir module at 192.168.4.1:14550) must be reachable and powered.
Wi-Fi bridge (192.168.4.2:14550) must NOT be running during these tests, because
the tests bind their own receive ports to capture MAVLink / subscribe telemetry
directly from the drone.

Run with:
    pytest ground_station/comm/tests/test_mavlink_limit.py -v
"""
from __future__ import annotations

import socket
import time
from pathlib import Path
from typing import Optional

import pytest


# ── Network constants ──────────────────────────────────────────────────────────
DRONE_HOST = "192.168.4.1"   # MicoAir AP gateway
DRONE_PORT = 14550            # MicoAir UDP TX (drone → GS)
# Receive ports chosen to NOT conflict with wifi_bridge (192.168.4.2:14550):
#  - 0.0.0.0:14551 is always free on any interface
#  - 0.0.0.0:14552 is the dedicated sim/integration receive port
RECV_PORT  = 14551
RECV_PORT_SIM = 14552


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def fc_host() -> str:
    """MicoAir module IP address."""
    return DRONE_HOST


@pytest.fixture
def drone_port() -> int:
    """MicoAir UDP port (drone TX / GS RX for MAVLink redirect)."""
    return DRONE_PORT


@pytest.fixture
def recv_port() -> int:
    """Test receive port for downlink telemetry (avoids wifi_bridge:14550)."""
    return RECV_PORT


@pytest.fixture
def sim_host() -> str:
    """Loopback address for simulation tests."""
    return "127.0.0.1"


@pytest.fixture
def sim_recv_port() -> int:
    """Dedicated receive port for simulation tests."""
    return RECV_PORT_SIM


@pytest.fixture
def sim_hz() -> float:
    """Simulation frame rate (Hz per MAVLink stream, 3 streams total = 3×Hz total)."""
    return 10.0


@pytest.fixture
def target_hz() -> float:
    """Target uplink command rate (Hz)."""
    return 100.0


@pytest.fixture
def uplink_host() -> str:
    """Target host for uplink commands."""
    return DRONE_HOST


@pytest.fixture
def uplink_port() -> int:
    """Target port for uplink commands (MicoAir command port)."""
    return DRONE_PORT


@pytest.fixture
def frame_capture_path(tmp_path: Path) -> Path:
    """Capture ~5 s of live MAVLink / subscribe frames from the drone.

    The MicoAir redirects telemetry to the sender's source port after a nudge
    byte is sent. This fixture binds a dedicated receive socket, sends the nudge,
    collects frames for 5 s, then writes them to a .bin file next to the test.

    Returns the path to the captured file. If the drone is unreachable the
    fixture yields a path that will cause the consuming test to fail with a
    clear error rather than silently skip.
    """
    capture_file = tmp_path / "live_capture.bin"

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(1.0)
    try:
        sock.bind(("0.0.0.0", RECV_PORT))
    except OSError:
        # Port in use — try alternate port
        sock.close()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1.0)
        sock.bind(("0.0.0.0", RECV_PORT_SIM))

    # Send nudge: MicoAir redirects telemetry to our source port after this
    try:
        sock.sendto(b"\x00", (DRONE_HOST, DRONE_PORT))
    except OSError:
        sock.close()
        pytest.fail(f"Cannot reach drone at {DRONE_HOST}:{DRONE_PORT} — is Wi-Fi connected?")

    frames: list[bytes] = []
    deadline = time.monotonic() + 10.0  # 10 s to capture enough extended frames (~86 at 8.6 Hz)
    while time.monotonic() < deadline:
        try:
            data, _ = sock.recvfrom(4096)
            if len(data) >= 2:
                frames.append(bytes(data))
        except socket.timeout:
            continue

    sock.close()

    if not frames:
        pytest.fail(
            f"No frames received from {DRONE_HOST}:{DRONE_PORT} on "
            f"port {RECV_PORT}/{RECV_PORT_SIM} in 5 s — "
            "check that the drone is streaming and Wi-Fi is connected."
        )

    with open(capture_file, "wb") as f:
        for frame in frames:
            f.write(frame)

    return capture_file


@pytest.fixture
def frame_type_hex() -> str:
    """Frame type hex for test_frame_type.

    Default: \"06\" = extended attitude frame (32 B, ~8.6 Hz on direct path).
    The drone sends JustFloat (16 B) and extended (32 B) on the direct path;
    MAVLink is aggregated by wifi_bridge and not present here.
    """
    return "06"


# Alias so the fixture is discoverable as "file" (expected by test_frame_type).
@pytest.fixture
def file(frame_capture_path: Path) -> Path:
    """Path to a live-captured .bin frame file for test_frame_type."""
    return frame_capture_path
