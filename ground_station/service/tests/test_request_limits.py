"""Request-body limits and the 423-without-drain flake (audit G1, 2026-09-23).

Three defects found auditing the HTTP layer, each covered here:

1. Every POST route read ``rfile.read(int(headers["Content-Length"]))`` with no
   cap, so one request decided how much the service would read.
2. A non-numeric Content-Length raised ``ValueError`` out of the handler.
3. ``POST /api/agent/plans/<id>/cancel`` answered 423 without draining the
   request body -- covered in ``test_agent.py``, which has the agent fixtures.

Every test drives an in-process server on an ephemeral port -- never 8081.
"""
import json
import socket
import time

import pytest

from ground_station.livewatch.stream import StreamRange, StreamSchema
from ground_station.service.api import ApiServer, _MAX_BODY_BYTES
from ground_station.service.core import GroundStationService
from ground_station.service.storage import SessionStore


@pytest.fixture
def server():
    """An in-process ApiServer on an ephemeral port; yields its port."""
    schema = StreamSchema(1, 1, 4,
                          (StreamRange(0x20000000, 4, 1, "altitude", "f"),), 0)
    service = GroundStationService(store=SessionStore(), schemas=[schema],
                                   source="sim")
    api = ApiServer(service, host="127.0.0.1", port=0)
    api.start()
    time.sleep(0.2)  # let the server bind
    try:
        yield api.address[1]
    finally:
        api.stop()


def _raw_post(port, route, headers, body=b"", timeout=5):
    """Send a POST by hand and return (status_line, socket).

    http.client refuses to send a Content-Length that disagrees with the body,
    which is precisely the case under test, so the request is framed by hand.
    The caller owns the returned socket.
    """
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    head = "POST %s HTTP/1.1\r\n" % route
    for k, v in headers.items():
        head += "%s: %s\r\n" % (k, v)
    head += "\r\n"
    sock.sendall(head.encode("ascii") + body)
    status = sock.makefile("rb").readline()
    return status, sock


def _ok_headers(port, length):
    return {"Host": "127.0.0.1:%d" % port,
            "Content-Type": "application/json",
            "Content-Length": str(length)}


# ── 1. oversized body ────────────────────────────────────────────────

def test_oversized_content_length_is_rejected_without_reading_the_body(server):
    """A 100 MB declared body is refused; the server does not wait for it.

    Before the fix the handler called ``rfile.read(104857600)`` and blocked
    until the socket ran dry, so this timed out instead of answering.
    """
    huge = 100 * 1024 * 1024
    status, sock = _raw_post(server, "/commands", _ok_headers(server, huge),
                             body=b"{}")  # 2 bytes sent, 100 MB claimed
    try:
        assert b"413" in status, status
    finally:
        sock.close()


def test_body_at_the_limit_is_still_accepted(server):
    """The cap rejects what is over it, not what is merely large."""
    payload = json.dumps({"command_id": 0,
                          "pad": "x" * 2048}).encode("ascii")
    assert len(payload) < _MAX_BODY_BYTES
    status, sock = _raw_post(server, "/commands",
                             _ok_headers(server, len(payload)), body=payload)
    try:
        assert b"413" not in status, status
    finally:
        sock.close()


# ── 2. malformed Content-Length ──────────────────────────────────────

@pytest.mark.parametrize("bad", ["abc", "-1", "", "1e6", "12 34"])
def test_malformed_content_length_is_a_400_not_a_500(server, bad):
    """A junk Content-Length must not raise out of the handler."""
    headers = {"Host": "127.0.0.1:%d" % server,
               "Content-Type": "application/json",
               "Content-Length": bad}
    status, sock = _raw_post(server, "/commands", headers, body=b"{}")
    try:
        assert b"400" in status or b"411" in status, status
        assert b"500" not in status, status
    finally:
        sock.close()
