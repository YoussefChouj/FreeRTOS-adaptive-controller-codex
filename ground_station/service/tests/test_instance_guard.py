"""WP-23 #6: a second 8081 refuses to start, before it touches the drone link, and cannot bind the same port."""

from __future__ import annotations

import socket
import sys
from http.server import BaseHTTPRequestHandler

import pytest

from ground_station.service.instance_guard import (
    AlreadyRunning, ExclusiveThreadingHTTPServer, ensure_single_instance, port_in_use,
)


@pytest.fixture
def listener():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(16)  # never accepts: the backlog must hold every probe connect
    try:
        yield s.getsockname()[1]
    finally:
        s.close()


def test_busy_port_is_refused_with_one_clear_line(listener):
    assert port_in_use(listener)
    with pytest.raises(AlreadyRunning, match=f"port {listener} already has a listener"):
        ensure_single_instance(listener)


def test_free_port_passes():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    ensure_single_instance(port)  # nothing listens: no raise


def test_second_server_cannot_bind_the_same_port():
    first = ExclusiveThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    try:
        with pytest.raises(OSError):
            ExclusiveThreadingHTTPServer(("127.0.0.1", first.server_address[1]), BaseHTTPRequestHandler)
    finally:
        first.server_close()


def test_main_refuses_before_building_the_bridge(listener, monkeypatch, capsys):
    import ground_station.comm.wifi_bridge as wifi_bridge
    from ground_station.service import __main__ as service_main

    def no_bridge(*a, **k):
        raise AssertionError("the bridge was built before the single-instance guard")

    monkeypatch.setattr(wifi_bridge, "WifiBridge", no_bridge)
    monkeypatch.setattr(sys, "argv", ["ground_station.service", "--port", str(listener)])
    with pytest.raises(SystemExit) as exc:
        service_main.main()
    assert exc.value.code == 2
    assert "REFUSED: port" in capsys.readouterr().err
