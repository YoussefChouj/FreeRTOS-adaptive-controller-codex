"""Tests for stream_log usart3 (WiFi) transport path.

These tests use a fake UDP socket and mock Usart3WifiSubscribeTransport so
no real hardware is needed.

Hermeticity: socket.socket is patched at module level before any transport
imports so no real UDP port 14550 is ever bound, even during module imports.
"""
import argparse
import csv
import itertools
import socket as _real_socket
import struct
from pathlib import Path
from unittest import mock

import pytest

# Patch socket.socket BEFORE any transport/stream_log imports so UdpDataPort
# and Usart3WifiSubscribeTransport constructors never bind a real UDP port.
_real_socket.socket = mock.MagicMock(spec=_real_socket.socket)

from ground_station.livewatch.stream_log import (
    _await_schema, _run_usart3, _run_groups_usart3, _TRANSPORTS,
    TRANSPORT_USART3, TRANSPORT_UART5,
)
from ground_station.livewatch.stream import (
    StreamRange, build_stream_request, decode_schema,
)
from ground_station.livewatch.transport import LiveTransportError, pop_frame, crc16_ccitt

# Default test range (in SRAM, 4-byte float)
_TEST_RANGE = StreamRange(0x20000000, 4, 1, "test")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_schema_payload(ranges, slot=0, divider=1):
    """Build the raw payload bytes for a 0x08 schema frame."""
    total = sum(r.nbytes for r in ranges)
    payload = struct.pack(">BBBH", divider, TRANSPORT_USART3, slot, total)
    for r in ranges:
        payload += struct.pack("<IHH", r.address, r.size, r.count)
    return payload


def _schema_frame(ranges, slot=0, divider=1):
    """Build a 0x08 schema frame, with byte5 = range count."""
    payload = _make_schema_payload(ranges, slot=slot, divider=divider)
    body = bytes((0x08, len(payload) >> 8, len(payload) & 0xFF, len(ranges))) + payload
    crc = 0
    for b in body:
        crc ^= b
    return b"\xAA\xBB" + body + bytes((crc,))


def _data_frame(values, slot=0, seq=0, t_ms=0):
    """Build a 0x09+slot data frame."""
    payload = struct.pack("<I", t_ms) + values
    body = bytes((0x09 + slot, len(payload) >> 8, len(payload) & 0xFF, seq)) + payload
    return b"\xAA\xBB" + body + struct.pack(">H", crc16_ccitt(body))


def _error_frame(msg):
    """Build a 0x7F error frame."""
    payload = msg.encode("utf-8") + b"\x00"
    body = bytes((0x7F, len(payload) >> 8, len(payload) & 0xFF, 0)) + payload
    crc = 0
    for b in body:
        crc ^= b
    return b"\xAA\xBB" + body + bytes((crc,))


class FakeUdpSocket:
    """A fake UDP socket that records sent datagrams and provides received bytes."""

    def __init__(self):
        self._buf = bytearray()
        self._sent = []
        self._closed = False

    def sendto(self, data, addr):
        self._sent.append((bytes(data), addr))

    def recvfrom(self, size):
        if not self._buf:
            raise BlockingIOError
        chunk = bytes(self._buf[:size])
        del self._buf[:size]
        return chunk, ("192.168.4.1", 14550)

    def setsockopt(self, *args):
        pass

    def settimeout(self, timeout):
        pass

    def bind(self, address):
        pass

    def close(self):
        self._closed = True

    def flush(self):
        pass

    def read(self, n):
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def reset_input_buffer(self):
        # Keep buffer intact in tests — _run_usart3 calls reset_input_buffer()
        # after subscribe; we want staged frames to survive for the loop.
        pass

    @property
    def in_waiting(self):
        return len(self._buf)


class FakeUsart3WifiTransport:
    """A fake Usart3WifiSubscribeTransport for unit tests.

    Mirrors the real interface: ``connect()``, ``subscribe(symbols, ...)``,
    ``_udp`` (a FakeUdpSocket), ``in_waiting``/``read`` (drainer), ``close()``.
    """

    def __init__(self, schema_frames=None, data_frames=None, port=14550,
                 module_ip="192.168.4.1"):
        self.port = port
        self.module_ip = module_ip
        self._udp = FakeUdpSocket()
        # Feed any pre-staged schema/data frames into the "wire" buffer
        self._schema_frames = schema_frames or []
        self._data_frames = data_frames or []
        self._subscribed_slot = None
        self.connect_called = False
        self.subscribe_calls = []

    def _stage(self, frame):
        self._udp._buf.extend(frame)

    def connect(self):
        self.connect_called = True
        return self

    def close(self):
        self._udp.close()

    def subscribe(self, symbols, divider=1, transport=1, slot=0):
        self.subscribe_calls.append({
            "symbols": symbols,
            "divider": divider,
            "transport": transport,
            "slot": slot,
        })
        self._subscribed_slot = slot
        # Do NOT stage frames here — _run_usart3 resets the buffer after
        # subscribe. Tests stage whatever frames they need after calling _run_*.
        ranges = symbols or [_TEST_RANGE]
        payload = _make_schema_payload(ranges, slot=slot, divider=divider)
        return decode_schema(len(ranges), payload, ranges)

    @property
    def in_waiting(self):
        return len(self._udp._buf)

    def read(self, n):
        out = bytes(self._udp._buf[:n])
        del self._udp._buf[:n]
        return out


def _patched_transport(fake):
    """Context manager that makes stream_log use ``fake`` as its transport."""
    return mock.patch(
        "ground_station.livewatch.stream_log.Usart3WifiSubscribeTransport",
        return_value=fake,
    )


def _run_timed():
    """Patch time.monotonic with an ever-increasing clock (never StopIteration)."""
    return mock.patch(
        "ground_station.livewatch.stream_log.time.monotonic",
        side_effect=itertools.count(0, 1),
    )


# ---------------------------------------------------------------------------
# Tests: _await_schema reads 0x08/0x7F from the UDP sink
# ---------------------------------------------------------------------------

def test_await_schema_reads_0x08_schema():
    fake = FakeUsart3WifiTransport()
    fake._stage(_schema_frame([_TEST_RANGE], slot=0, divider=1))
    schema = _await_schema(fake, [_TEST_RANGE], timeout=0.5)
    assert schema.slot == 0
    assert len(schema.ranges) == 1
    assert schema.ranges[0].address == _TEST_RANGE.address


def test_await_schema_raises_on_0x7f_error():
    fake = FakeUsart3WifiTransport()
    fake._stage(_error_frame("E:bad slot"))
    with pytest.raises(LiveTransportError, match="firmware rejected"):
        _await_schema(fake, [], timeout=0.5)


def test_await_schema_times_out_without_reply():
    fake = FakeUsart3WifiTransport()
    with pytest.raises(LiveTransportError, match="no 0x08 schema reply"):
        _await_schema(fake, [], timeout=0.001)


# ---------------------------------------------------------------------------
# Tests: _run_usart3 uses the UDP subscribe path
# ---------------------------------------------------------------------------

def test_run_usart3_sends_request_over_udp():
    req = build_stream_request([_TEST_RANGE], 1, TRANSPORT_USART3)
    stop = build_stream_request([], 0, TRANSPORT_USART3, slot=0)
    fake = FakeUsart3WifiTransport()

    with _patched_transport(fake), _run_timed():
        result = _run_usart3(
            "udp:14550", [_TEST_RANGE], 1, TRANSPORT_USART3, 0.05,
            "/tmp/fake.csv", True, req, stop, 921600, slot=0,
        )

    sent = [s for s, _ in fake._udp._sent]
    assert any(s[:2] == b"\xCC\xDE" for s in sent), \
        "subscribe request must go out over the UDP socket"
    assert fake.connect_called


def test_run_usart3_parses_schema_reply():
    req = build_stream_request([_TEST_RANGE], 1, TRANSPORT_USART3)
    stop = build_stream_request([], 0, TRANSPORT_USART3, slot=0)
    fake = FakeUsart3WifiTransport()

    with _patched_transport(fake), _run_timed():
        result = _run_usart3(
            "udp:14550", [_TEST_RANGE], 1, TRANSPORT_USART3, 0.05,
            "/tmp/fake2.csv", True, req, stop, 921600, slot=0,
        )
    assert result["columns"] == ["test"]
    assert len(fake.subscribe_calls) == 1
    assert fake.subscribe_calls[0]["slot"] == 0


def test_run_usart3_sends_stop_on_normal_exit():
    req = build_stream_request([_TEST_RANGE], 1, TRANSPORT_USART3)
    stop = build_stream_request([], 0, TRANSPORT_USART3, slot=0)
    fake = FakeUsart3WifiTransport()

    with _patched_transport(fake), _run_timed():
        _run_usart3(
            "udp:14550", [_TEST_RANGE], 1, TRANSPORT_USART3, 0.05,
            "/tmp/fake3.csv", True, req, stop, 921600, slot=0,
        )
    sent = [s for s, _ in fake._udp._sent]
    assert any(s == stop for s in sent), "stop frame must go out on exit"


def test_run_usart3_sends_stop_on_keyboard_interrupt():
    req = build_stream_request([_TEST_RANGE], 1, TRANSPORT_USART3)
    stop = build_stream_request([], 0, TRANSPORT_USART3, slot=0)
    fake = FakeUsart3WifiTransport()
    calls = []

    def _boom():
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt()
        return 0.0

    with _patched_transport(fake), \
            mock.patch("ground_station.livewatch.stream_log.time.monotonic",
                       side_effect=_boom):
        with pytest.raises(KeyboardInterrupt):
            _run_usart3(
                "udp:14550", [_TEST_RANGE], 1, TRANSPORT_USART3, 30.0,
                "/tmp/fake4.csv", True, req, stop, 921600, slot=0,
            )
    sent = [s for s, _ in fake._udp._sent]
    assert any(s == stop for s in sent), "stop frame must go out on Ctrl-C"


def test_run_usart3_writes_data_frames_to_csv(tmp_path):
    req = build_stream_request([_TEST_RANGE], 1, TRANSPORT_USART3)
    stop = build_stream_request([], 0, TRANSPORT_USART3, slot=0)
    fake = FakeUsart3WifiTransport()
    # Stage schema + data frame before the call; reset_input_buffer is
    # overridden as a no-op in FakeUdpSocket so they survive.
    data = struct.pack("<f", 3.14)
    fake._stage(_schema_frame([_TEST_RANGE], slot=0, divider=1))
    fake._stage(_data_frame(data, slot=0, seq=0, t_ms=1000))

    with _patched_transport(fake), _run_timed():
        out = tmp_path / "stream.csv"
        result = _run_usart3(
            "udp:14550", [_TEST_RANGE], 1, TRANSPORT_USART3, 5.0,
            str(out), True, req, stop, 921600, slot=0,
        )
    rows = list(csv.reader(out.open("r", newline="")))
    assert rows[0][:3] == ["t_src_ms", "t_host_s", "seq"]
    assert len(rows) == 2  # header + 1 data row
    # Value column is bytes repr (b'\xc3\xf5H@' = float 3.14 LE); decode it
    val_bytes = eval(rows[1][3])  # b'\xc3\xf5H@'
    assert struct.unpack("<f", val_bytes)[0] == pytest.approx(3.14)
    assert result["rows"] == 1


# ---------------------------------------------------------------------------
# Tests: _run_groups_usart3
# ---------------------------------------------------------------------------

def test_run_groups_usart3_subscribes_each_slot():
    plans = [
        (0, [_TEST_RANGE], 1, b"\xCC\xDE\x21"),
        (1, [_TEST_RANGE], 2, b"\xCC\xDE\x21"),
    ]
    fake = FakeUsart3WifiTransport()

    with _patched_transport(fake), _run_timed():
        _run_groups_usart3(
            "udp:14550", plans, 0.05, "/tmp/groups.csv", True,
            921600, TRANSPORT_USART3,
        )
    assert [c["slot"] for c in fake.subscribe_calls] == [0, 1]
    assert fake.subscribe_calls[0]["divider"] == 1
    assert fake.subscribe_calls[1]["divider"] == 2


def test_run_groups_usart3_stops_each_subscribed_slot():
    plans = [
        (0, [_TEST_RANGE], 1, b"\xCC\xDE\x21"),
        (1, [_TEST_RANGE], 1, b"\xCC\xDE\x21"),
    ]
    fake = FakeUsart3WifiTransport()

    with _patched_transport(fake), _run_timed():
        _run_groups_usart3(
            "udp:14550", plans, 0.05, "/tmp/groups2.csv", True,
            921600, TRANSPORT_USART3,
        )
    sent = [s for s, _ in fake._udp._sent]
    stops = [s for s in sent if s[:2] == b"\xCC\xDE" and len(s) >= 10 and s[5] == 0]
    assert len(stops) == 2, "one stop per subscribed slot"


# ---------------------------------------------------------------------------
# Tests: CLI defaults and uart5 warning
# ---------------------------------------------------------------------------

def test_cli_default_transport_is_usart3():
    from ground_station.livewatch.stream_log import main
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport", choices=sorted(_TRANSPORTS), default="usart3")
    args = ap.parse_args([])
    assert args.transport == "usart3"


def test_uart5_warning_mentions_firmware(capsys):
    from ground_station.livewatch.stream_log import _TRANSPORTS
    with mock.patch("ground_station.livewatch.stream_log.run") as mock_run:
        mock_run.return_value = {
            "rows": 0, "seconds": 0.1, "hz": 0.0, "dropped": 0,
            "loss_pct": 0.0, "malformed": 0, "columns": [], "path": "/tmp/x.csv",
        }
        from ground_station.livewatch.stream_log import main
        main(["--transport", "uart5", "--symbol", "x", "--seconds", "0.01"])
    err = capsys.readouterr().err
    assert "SUBSCRIBE_UART5_ENABLED = 0" in err
    assert "usart3" in err


# ---------------------------------------------------------------------------
# Tests: host parsers survive the idle VOFA JustFloat stream
# ---------------------------------------------------------------------------

def _vofa_idle_frame():
    """A whole 16 B VOFA JustFloat frame (4f + tail 00 00 80 7F)."""
    floats = struct.pack("<fff", 0.0, 0.0, 0.0)
    return floats + b"\x00\x00\x80\x7F"


def test_pop_frame_drops_false_aa_bb_sync_in_vofa_idle():
    """Verify VOFA idle frames (all-zero floats) are handled by CRC check.

    A false 0xAA 0xBB inside a VOFA JustFloat stream may look like a sync,
    but `pop_frame` validates the CRC and drops anything that isn't a valid
    protocol frame. The all-zero payload happens to have CRC=0, so it is
    parsed as an empty (0-length) 0x00 frame — not returned as 0x08/0x7F.
    """
    rx = bytearray()
    # One VOFA idle frame (4 f32 = 0x00, tail 00 00 80 7F)
    rx.extend(_vofa_idle_frame())
    # pop_frame may parse the leading AA BB as a 0-length 0x00 frame (CRC=0)
    # or drop it; either way it must NOT return a 0x08 or 0x7F frame.
    result = pop_frame(rx)
    if result is not None:
        frame_type, _, _ = result
        assert frame_type != 0x08 and frame_type != 0x7F


def test_schema_frame_still_parses_with_vofa_noise_tail():
    """pop_frame must find the real 0x08 frame even with VOFA noise around it."""
    payload = _make_schema_payload([_TEST_RANGE], slot=0, divider=1)
    body = bytes((0x08, len(payload) >> 8, len(payload) & 0xFF, 1)) + payload
    crc = 0
    for b in body:
        crc ^= b
    real_frame = b"\xAA\xBB" + body + bytes((crc,))
    rx = bytearray()
    # VOFA noise: one full idle frame containing a false 0xAA 0xBB sync
    rx.extend(_vofa_idle_frame())
    rx.extend(real_frame)
    result = pop_frame(rx)
    assert result is not None
    frame_type, count, pl = result
    assert frame_type == 0x08
    schema = decode_schema(count, pl, [_TEST_RANGE])
    assert schema.slot == 0
    assert len(schema.ranges) == 1