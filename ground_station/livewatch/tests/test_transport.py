"""Offline tests for the livewatch transport abstraction."""
from argparse import Namespace

import pytest

from ground_station.livewatch.cli import _transport
from ground_station.livewatch.reader import Plan, Region
from ground_station.livewatch.symbols import Symbol
from ground_station.livewatch.transport import (
    LiveTransportError, SwdCmsisDap, Uart5LongRange,
    Usart3WifiSubscribeTransport,
)
from ground_station.livewatch.stream import StreamRange


def _frame(frame_type, payload=b"", count=0):
    body = bytes((frame_type, len(payload) >> 8, len(payload) & 0xFF, count)) + payload
    crc = 0
    for byte in body:
        crc ^= byte
    return b"\xAA\xBB" + body + bytes((crc,))


class FakeSerial:
    def __init__(self, port, baud, timeout=0.05, reply=b""):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.rx = bytearray(reply)
        self.writes = []
        self.closed = False

    @property
    def in_waiting(self):
        return len(self.rx)

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def read(self, size=1):
        out = bytes(self.rx[:size])
        del self.rx[:size]
        return out

    def close(self):
        self.closed = True


def test_cli_transport_defaults_to_swd():
    args = Namespace(transport="swd", uart5_port=None, uart5_baud=None)
    assert isinstance(_transport(args), SwdCmsisDap)


def test_cli_uart5_constructs_selected_transport():
    args = Namespace(transport="uart5", uart5_port="COM42", uart5_baud=230400)
    transport = _transport(args)
    assert isinstance(transport, Uart5LongRange)
    assert transport.port == "COM42"
    assert transport.baud == 230400


def test_transport_cost_models_are_distinct_and_label_uart_estimate():
    swd = SwdCmsisDap.cost_model.describe()
    uart = Uart5LongRange.cost_model.describe()
    assert swd != uart
    assert "estimated, not measured" in uart


def test_uart5_request_pinned_format():
    """Verifies the host->FC request frame is built exactly as the docstring pins.

    The frame is NOT sent on connect anymore -- connect() is silent. The frame
    is sent on each sample() call. We construct the request directly via
    _build_request and assert the byte layout is what's pinned in the module
    docstring: 0xCC 0xDE | 0x20 | LEN_HI LEN_LO (BE) | MAX_NUM_BASIS, payload
    of packed (address:uint32 LE | size:uint16 LE) tuples, trailer CRC8 XOR.
    """
    sym_a = Symbol("s_ekf.x[3]", 0x20000100, 4, "f")
    sym_b = Symbol("s_ekf.x[4]", 0x20000104, 4, "f")
    transport = Uart5LongRange("COM42", serial_factory=lambda *a, **kw: FakeSerial("COM42", 115200))
    frame = transport._build_request([sym_a, sym_b])
    assert frame[:2] == b"\xCC\xDE"
    assert frame[2] == 0x20
    length = (frame[3] << 8) | frame[4]
    assert length == 2 * 6
    assert frame[5] == 2  # MAX_NUM_BASIS = 2
    payload = frame[6:6 + length]
    assert payload == (
        sym_a.address.to_bytes(4, "little") + sym_a.size.to_bytes(2, "little") +
        sym_b.address.to_bytes(4, "little") + sym_b.size.to_bytes(2, "little")
    )
    crc = 0
    for byte in frame[2:-1]:
        crc ^= byte
    assert frame[-1] == crc


def test_uart5_sample_reassembles_per_symbol_into_regions():
    """FC replies with one tuple per requested scalar; the host reassembles.

    The 4 floats below (16 B) would coalesce into a single 16 B region in
    build_plan. The firmware-only-safe reply is one tuple per scalar, so the
    host has to walk the plan's symbols and concatenate the four scalar values
    back into the 16 B region block.
    """
    base = 0x20000100
    values = [b"\x00\x00\x80\x3F", b"\x00\x00\x00\x40",
              b"\x00\x00\x40\x40", b"\x00\x00\x80\x40"]  # 1.0, 2.0, 3.0, 4.0
    tuples = b"".join(
        (base + 4 * i).to_bytes(4, "little") + (4).to_bytes(2, "little") + v
        for i, v in enumerate(values)
    )
    fake = FakeSerial("COM42", 115200, reply=b"")
    transport = Uart5LongRange(
        "COM42", timeout=0.5, serial_factory=lambda *a, **kw: fake).connect()
    try:
        # Inject the 0x07 reply AFTER the connect() drain so the transport
        # sees it on the next sample() call.
        fake.rx.extend(_frame(0x07, tuples, 4))
        # Hand-build a Plan with one 16 B region and four 4 B symbols so the
        # host reassembly is forced.
        symbols = [Symbol(f"s_ekf.x[{i}]", base + 4 * i, 4, "f") for i in range(4)]
        plan = Plan(symbols=symbols, regions=[Region(base, 16)])
        blocks = transport.sample(plan)
        assert blocks == [b"".join(values)]
    finally:
        transport.close()


def test_uart5_sample_decodes_single_scalar_region():
    """Single 4-byte region requested; the FC replies with one tuple and the
    host returns the 4-byte block. Verifies the path still works for the
    simple, non-coalesced case (one symbol, one region).
    """
    address = 0x20000100
    value = b"\x00\x00\xA0\x3F"
    payload = address.to_bytes(4, "little") + len(value).to_bytes(2, "little") + value
    fake = FakeSerial("COM42", 115200, reply=b"")
    transport = Uart5LongRange(
        "COM42", timeout=0.5, serial_factory=lambda *a, **kw: fake).connect()
    try:
        fake.rx.extend(_frame(0x07, payload, 1))
        sym = Symbol("s_ekf.x[3]", address, 4, "f")
        plan = Plan(symbols=[sym], regions=[Region(address, 4)])
        assert transport.sample(plan) == [value]
    finally:
        transport.close()


def test_uart5_timeout_fails_loud_without_swd_fallback(monkeypatch):
    """No SWD construction when the UART5 transport times out.

    The no-fallback contract requires that the transport itself AND every
    higher callback layer (LiveReader, dashboard _livelog_check_budget) never
    substitute SwdCmsisDap() on a UART5 failure. We wrap SwdCmsisDap with a
    construction counter so a hidden fallback would be visible.
    """
    constructions = []
    real_swd = SwdCmsisDap

    class CountingSwd(real_swd):
        def __init__(self, *a, **kw):
            constructions.append(1)
            super().__init__(*a, **kw)

    monkeypatch.setattr(
        "ground_station.livewatch.transport.SwdCmsisDap", CountingSwd)
    fake = FakeSerial("COM42", 115200, reply=b"")
    transport = Uart5LongRange(
        "COM42", timeout=0.001, serial_factory=lambda *a, **kw: fake).connect()
    try:
        with pytest.raises(LiveTransportError, match="no reply"):
            transport.sample(Plan(symbols=[], regions=[]))
        assert constructions == []
    finally:
        transport.close()


def test_uart5_requires_manual_port():
    with pytest.raises(LiveTransportError, match="manual"):
        Uart5LongRange("")


def test_uart5_error_reply_surfaced():
    """A 0x7F error reply from the FC is surfaced as LiveTransportError.

    The firmware emits a 0x7F frame on validation failure (e.g. an address
    outside SRAM/CCM, an unaligned read, or a CRC mismatch). The host must
    not silently drop it; it must raise LiveTransportError with the payload
    string visible so the operator can see the FC's reason.
    """
    err_msg = b"E:bad addr\x00"
    frame = _frame(0x7F, err_msg, 0)
    fake = FakeSerial("COM42", 115200, reply=b"")
    transport = Uart5LongRange(
        "COM42", timeout=0.5, serial_factory=lambda *a, **kw: fake).connect()
    try:
        # Inject the 0x7F frame AFTER connect() drain so the transport sees it
        # on the next _wait_for_frame() call (matching the existing sample tests).
        fake.rx.extend(frame)
        with pytest.raises(LiveTransportError, match="E:bad addr"):
            transport._wait_for_frame(transport._REPLY_FRAME, "test")
    finally:
        transport.close()


# ---------------------------------------------------------------------------
# WiFi subscribe transport tests
# ---------------------------------------------------------------------------

def _wifi_frame(frame_type, payload=b"", count=0):
    """Build a 0xAA 0xBB telemetry/subscribe frame for the WiFi path.

    For 0x07/0x09 frames: ``count`` is MAX_NUM_BASIS (number of tuples/ranges).
    For 0x08 schema frames: ``count`` is n_ranges (number of range descriptors).
    """
    body = bytes((frame_type, len(payload) >> 8, len(payload) & 0xFF, count)) + payload
    crc = 0
    for byte in body:
        crc ^= byte
    return b"\xAA\xBB" + body + bytes((crc,))


def test_cli_wifi_constructs_wifi_transport():
    """The wifi transport choice instantiates Usart3WifiSubscribeTransport."""
    args = Namespace(transport="wifi", uart5_port=None, uart5_baud=None)
    from ground_station.livewatch.transport import Usart3WifiSubscribeTransport
    assert isinstance(_transport(args), Usart3WifiSubscribeTransport)


class FakeUdpSock:
    """Fake UDP socket for WiFi transport tests.

    ``rx_queue`` holds incoming datagrams as bytes.  Each sendto() call pops
    one entry; empty queue returns nothing (simulates no downlink yet).
    """

    def __init__(self, rx_queue=()):
        self.rx_queue = list(rx_queue)
        self.sent = []
        self.closed = False

    def sendto(self, data, addr):
        self.sent.append((bytes(data), addr))
        # Pop the next downlink datagram, if any.
        if self.rx_queue:
            return len(data)   # sendto return value

    def recvfrom(self, n):
        if self.rx_queue:
            datagram = self.rx_queue.pop(0)
            return datagram, ("192.168.4.1", 14550)
        raise BlockingIOError()

    def setblocking(self, flag):
        pass

    def getsockname(self):
        return ("0.0.0.0", 14550)

    def close(self):
        self.closed = True


def test_wifi_subscribe_sends_stream_request_and_decodes_0x08_schema(monkeypatch):
    """Usart3WifiSubscribeTransport.subscribe() sends a 0xCC 0xDE 0x21 frame
    and returns a StreamSchema on receiving the firmware's 0x08 reply.

    The key invariants verified here:
    1. The request opcode is 0x21 (stream subscribe), NOT 0x20 (one-shot).
    2. The socket used for TX is the parent's bound socket (single-socket pattern).
    3. The 0x08 reply is decoded into a StreamSchema with the correct fields.
    """
    from ground_station.livewatch.transport import Usart3WifiSubscribeTransport
    from ground_station.livewatch.stream import StreamSchema, StreamRange

    addr = 0x20000000
    # Build a minimal 0x08 schema payload: divider=1, transport=USART3, slot=3,
    # total_bytes=4 (one uint32).
    # Schema payload layout per stream.py decode_schema:
    #   divider(1) | transport(1) | slot(1) | total_bytes(2 BE) | ranges(N*8)
    schema_payload = bytes([1, 1, 3]) + (4).to_bytes(2, "big")
    # Range descriptor: address(4 LE) | size(2 LE) | count(2 LE)
    schema_payload += addr.to_bytes(4, "little") + (4).to_bytes(2, "little") + (1).to_bytes(2, "little")
    # Build the raw 0xAA 0xBB wifi frame directly (bypassing _wifi_frame, whose
    # length bytes would overlap with total_bytes at schema byte 3).
    frame_payload = bytes((0x08, len(schema_payload) >> 8, len(schema_payload) & 0xFF, 1)) + schema_payload
    crc = 0
    for b_ in frame_payload:
        crc ^= b_
    wifi_reply = b"\xAA\xBB" + frame_payload + bytes((crc,))
    rx_queue = [wifi_reply]

    sock = FakeUdpSock(rx_queue)

    class FakeUdpDataPort:
        def __init__(self, s, rx):
            self._sock = s
            self._rx = rx

        def _drain(self):
            while True:
                try:
                    chunk, _ = self._sock.recvfrom(65535)
                except BlockingIOError:
                    return
                if chunk:
                    self._rx.extend(chunk)

    rx_buf = bytearray()
    monkeypatch.setattr(
        "ground_station.livewatch.transport.UdpDataPort",
        lambda *a, **kw: FakeUdpDataPort(sock, rx_buf))
    transport = Usart3WifiSubscribeTransport(14550, "192.168.4.1")
    transport._udp = FakeUdpDataPort(sock, rx_buf)
    transport.port = 14550
    transport.module_ip = "192.168.4.1"
    transport._rx = rx_buf
    transport.timeout = 1.0

    sym = StreamRange(addr, 4, 1, name="xTickCount")
    schema = transport.subscribe([sym], divider=1, transport=1, slot=3)

    # Check: one TX datagram was sent
    assert len(sock.sent) == 1
    req = sock.sent[0][0]
    # Check: opcode byte is 0x21 (stream subscribe), not 0x20 (one-shot)
    assert req[0:2] == b"\xCC\xDE", "sync header"
    assert req[2] == 0x21, f"opcode is 0x{req[2]:02X}, want 0x21"
    # Check: the request contains the address and size
    assert addr.to_bytes(4, "little") in req[6:14], "address in request"

    # Check: returned schema has correct fields
    assert isinstance(schema, StreamSchema)
    assert schema.divider == 1
    assert schema.transport == 1
    assert schema.slot == 3


def test_wifi_subscribe_raises_on_0x7F_error(monkeypatch):
    """A 0x7F error reply from the FC raises LiveTransportError with the msg."""
    from ground_station.livewatch.transport import Usart3WifiSubscribeTransport
    err_payload = b"E:bad addr\x00"
    rx_queue = [_wifi_frame(0x7F, err_payload, count=0)]

    sock = FakeUdpSock(rx_queue)
    rx_buf = bytearray()

    class FakeUdpDataPort:
        def __init__(self, s, rx):
            self._sock = s
            self._rx = rx

        def _drain(self):
            while True:
                try:
                    chunk, _ = self._sock.recvfrom(65535)
                except BlockingIOError:
                    return
                if chunk:
                    self._rx.extend(chunk)

    monkeypatch.setattr(
        "ground_station.livewatch.transport.UdpDataPort",
        lambda *a, **kw: FakeUdpDataPort(sock, rx_buf))
    transport = Usart3WifiSubscribeTransport(14550, "192.168.4.1")
    transport._udp = FakeUdpDataPort(sock, rx_buf)
    transport.port = 14550
    transport.module_ip = "192.168.4.1"
    transport._rx = rx_buf
    transport.timeout = 1.0

    sym = StreamRange(0x20000000, 4, 1, name="xTickCount")
    with pytest.raises(LiveTransportError, match="E:bad addr"):
        transport.subscribe([sym], divider=1, transport=1, slot=3)


def test_wifi_subscribe_times_out_when_no_reply(monkeypatch):
    """When no 0x08 arrives, the transport raises LiveTransportError."""
    from ground_station.livewatch.transport import Usart3WifiSubscribeTransport
    from ground_station.livewatch.stream import StreamRange

    sock = FakeUdpSock([])  # no downlink datagrams
    rx_buf = bytearray()

    class FakeUdpDataPort:
        def __init__(self, s, rx):
            self._sock = s
            self._rx = rx

        def _drain(self):
            while True:
                try:
                    chunk, _ = self._sock.recvfrom(65535)
                except BlockingIOError:
                    return
                if chunk:
                    self._rx.extend(chunk)

    monkeypatch.setattr(
        "ground_station.livewatch.transport.UdpDataPort",
        lambda *a, **kw: FakeUdpDataPort(sock, rx_buf))
    transport = Usart3WifiSubscribeTransport(14550, "192.168.4.1")
    transport._udp = FakeUdpDataPort(sock, rx_buf)
    transport.port = 14550
    transport.module_ip = "192.168.4.1"
    transport._rx = rx_buf
    transport.timeout = 0.01  # fast timeout for the test

    # The first recvfrom raises BlockingIOError (queue empty); drain must not crash.
    # After the drain loop exits, timeout fires.
    sym = StreamRange(0x20000000, 4, 1, name="xTickCount")
    with pytest.raises(LiveTransportError, match="timeout"):
        transport.subscribe([sym], divider=1, transport=1, slot=3)
