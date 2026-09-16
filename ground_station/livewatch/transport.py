"""Read-only livewatch transport seam for SWD, UART5, and USART3 over the MicoAir WiFi link.

All three transports expose only block-read paths; none halts, resets, writes core
memory, arms, or spins motors. The wireless CMSIS-DAP transport preserves
``connect_mode=attach`` / ``target_override=cortex_m`` / ``resume_on_disconnect=False``.

Pinned UART5 wire contract (host <-> FC):

* Host -> FC request frame (firmware-side spec pins the form):
  ``0xCC 0xDE | 0x20 | LEN_HI LEN_LO (BE) | MAX_NUM_BASIS``, payload of LEN bytes,
  trailer CRC8 XOR. PDB: ``0x20`` is a placeholder command ID; the firmware-side
  spec may renumber it, and the host constant updates with it. The payload is a
  packed list of scalar-request tuples, one per resolved symbol:
  ``address:uint32 LE | size:uint16 LE``. ``size`` MUST be in ``{1, 2, 4}`` so the
  firmware-side validator in HANDOFF §5 item 1 accepts every request without
  raising a contiguous-region exception. ``MAX_NUM_BASIS`` carries the tuple
  count. The host sends this request ONCE per ``sample()`` call.
* FC -> host reply frame:
  ``0xAA 0xBB | 0x07 | LEN_HI LEN_LO (BE) | MAX_NUM_BASIS``, payload of LEN bytes,
  trailer CRC8 XOR (mirrors IF-02's XOR-CRC8 since 0x07 is read-only observation,
  not Frame C's CRC16). Each tuple is ``address:uint32 LE | size:uint16 LE |
  value:size bytes``. The firmware produces one tuple per scalar request and the
  host reassembles them into the contiguous regions that ``build_plan`` produced.
* FC -> host error-reply frame (``0x7F``):
  ``0xAA 0xBB | 0x7F | LEN_HI LEN_LO (BE) | MAX_NUM_BASIS``, payload of LEN bytes
  (UTF-8 NUL-terminated string, <= 240 bytes), trailer CRC8 XOR. Surfaced by
  ``_wait_for_frame`` as a ``LiveTransportError`` carrying the decoded message
  (e.g. ``"E:bad addr"``); the host does not treat 0x7F as a fatal disconnect.

UART5 never falls back to SWD: a missing or malformed reply raises
``LiveTransportError``. The contract above is the only one this transport expects.
"""
from __future__ import annotations

import socket
import struct
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from .manifest import CostModel

if TYPE_CHECKING:
    from .reader import Plan


# Subscription data frames: 0x09 + slot, one per slot. They use a CRC16-CCITT
# trailer where the control-plane frames use a single XOR byte.
STREAM_DATA_FRAMES = frozenset(range(0x09, 0x0D))


def _xor_crc(data: bytes) -> int:
    """Return the protocol's byte-wise XOR checksum."""
    value = 0
    for byte in data:
        value ^= byte
    return value


def crc16_ccitt(data: bytes) -> int:
    """XModem parameters: poly 0x1021, init 0x0000, no reflection, no final XOR.

    Same as Frame C's checksum and ``Crc16Ccitt`` in ``API/subscribe.c``.
    """
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def pop_frame(rx: bytearray) -> tuple[int, int, bytes] | None:
    """Pop one complete ``0xAA 0xBB`` frame off ``rx``, consuming its bytes.

    Returns ``(frame_type, byte5, payload)`` or ``None`` when no whole frame is
    buffered yet. ``byte5`` is the envelope's sixth byte, whose meaning is
    per-frame: tuple count for 0x07, range count for 0x08, sequence number for
    0x09. Frames whose CRC fails are dropped and the scan continues, so a
    corrupted frame costs one frame rather than resynchronising the stream.

    Module-level (not a method) because the 0x21 streaming path in
    ``stream.py`` decodes the same envelope from a different serial port.

    **0x7F error frames**: Returns ``(0x7F, byte5, payload)`` where ``payload``
    is the UTF-8 error message from the firmware. The caller should decode it
    and raise ``LiveTransportError`` with the message.
    """
    while True:
        sync = rx.find(b"\xAA\xBB")
        if sync < 0:
            if len(rx) > 1:
                del rx[:-1]
            return None
        if sync:
            del rx[:sync]
        if len(rx) < 6:
            return None
        frame_type = rx[2]
        length = (rx[3] << 8) | rx[4]
        crc16_framed = frame_type == 0x06 or frame_type in STREAM_DATA_FRAMES
        total = 6 + length + (2 if crc16_framed else 1)
        if len(rx) < total:
            return None
        raw = bytes(rx[:total])
        del rx[:total]
        if frame_type == 0x06:
            continue  # Frame C is decoded by serial_bridge, not here
        if frame_type == 0x7F:
            # Error frame: payload is a NUL-terminated UTF-8 string
            payload = raw[6:-1]  # Strip header + CRC
            crc_expected = raw[-1]
            crc_actual = _xor_crc(raw[2:-1])
            if crc_actual != crc_expected:
                continue  # Corrupted error frame, skip
            return (frame_type, raw[5], payload)
        if crc16_framed:
            # Subscription data frames carry CRC16-CCITT: they ARE the recorded
            # dataset, and an XOR checksum cannot see a byte transposition.
            if crc16_ccitt(raw[2:-2]) != int.from_bytes(raw[-2:], "big"):
                continue
            return frame_type, raw[5], raw[6:-2]
        crc = 0
        for byte in raw[2:-1]:
            crc ^= byte
        if crc != raw[-1]:
            continue
        return frame_type, raw[5], raw[6:-1]


class LiveTransportError(RuntimeError):
    """A transport could not establish or complete a livewatch read."""


class LiveTransport(ABC):
    """Uniform read-only block-sampling transport used by ``LiveReader``."""

    name: str
    cost_model: CostModel
    gap_merge_bytes: int = 48

    @abstractmethod
    def connect(self) -> "LiveTransport":
        pass

    @abstractmethod
    def close(self) -> None:
        pass

    @abstractmethod
    def sample(self, plan: "Plan") -> list[bytes]:
        pass

    def calibrate(self, reader, plan: "Plan", n: int = 15):
        from .manifest import calibrate
        return calibrate(reader, plan, cost_model=self.cost_model, n=n)

    def read_raw(self, address: int, size: int) -> bytes:
        """Subclass override point: raw read with retry support. Default raises."""
        raise LiveTransportError(
            f"{self.name}-read: raw read not supported "
            "(use sample() or switch to the swd transport)"
        )


class SwdCmsisDap(LiveTransport):
    """Safe pyOCD attach-mode RAM reader; never halts, resets, or writes.

    Retry strategy for flaky wireless probes (e.g. CMSIS-DAP over WiFi):
    each region is read individually with exponential backoff on TransferError,
    and the CMSIS-DAP backend is put into single-in-flight mode so USB
    multiplexing cannot inject gaps between commands.

    References:
    - pyOCD v0.39+: wait_retry bumped to 150 internally (DAPTransferConfig).
    - PR #1505: retry count raised to max for flash-stall tolerance.
    - cmsis_dap.limit_packets=True forces single-in-flight for problematic USB.
    """

    name = "swd"
    cost_model = CostModel(
        2.2, 0.031, "swd-cmsis-dap",
        "rounded up from 2026-07-26 wireless CMSIS-DAP measurements",
    )
    gap_merge_bytes = 48

    # Exponential backoff for TransferError on individual region reads.
    _RETRY_BASE_S  = 0.05   # first delay
    _RETRY_MAX_S  = 2.0    # cap
    _RETRY_MAX_N  = 5       # give up after this many retries

    def __init__(self, *, limit_packets: bool = True):
        """
        limit_packets: when True (default), forces single in-flight CMSIS-DAP
        command. Eliminates USB multiplexing overhead that causes spurious
        TransferErrors on wireless/debug probes. Set False only for wired
        probes where throughput matters more than reliability.
        """
        self._limit_packets = bool(limit_packets)
        self._session = None
        self.target = None

    def connect(self) -> "SwdCmsisDap":
        from pyocd.core.helpers import ConnectHelper
        options = {
            "target_override": "cortex_m",
            "connect_mode": "attach",
            "resume_on_disconnect": False,
        }
        if self._limit_packets:
            # Force single-in-flight USB transactions. Eliminates the multiplexing
            # gaps that generate spurious TransferErrors on wireless/debug probes.
            # See: pyOCD session options — cmsis_dap.limit_packets.
            options["cmsis_dap.limit_packets"] = True

        # If multiple CMSIS-DAP probes are visible (e.g. ATK wireless debugger +
        # a Segger J-Link on the same machine), session_with_chosen_probe()
        # prompts interactively. Pin the probe by unique_id from config.yaml.
        try:
            import yaml as _yaml
            cfg_path = Path(__file__).resolve().parents[2] / "config.yaml"
            cfg = _yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            unique_id = cfg.get("swd_probe_unique_id")
        except Exception:
            unique_id = None

        if unique_id:
            self._session = ConnectHelper.session_with_chosen_probe(
                options=options, unique_id=unique_id,
            )
        else:
            self._session = ConnectHelper.session_with_chosen_probe(options=options)
        if self._session is None:
            raise LiveTransportError(
                "no CMSIS-DAP probe found (is Keil holding it? close its debug session)"
            )
        self._session.open()
        self.target = self._session.target
        return self

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
        self._session = None
        self.target = None

    def sample(self, plan: "Plan") -> list[bytes]:
        if self.target is None:
            raise LiveTransportError("swd-read: not connected")
        blocks = []
        for region in plan.regions:
            block = self._read_region_with_retry(region)
            blocks.append(block)
        return blocks

    def _read_region_with_retry(self, region: "Region") -> bytes:
        """Read one memory region with exponential-backoff retry on TransferError.

        Wireless probes (especially over USB/WiFi) occasionally fail a transfer
        with a spurious TransferError. The retry loop recovers transparently in
        the vast majority of cases. If all retries are exhausted the original
        exception propagates so the caller still gets a clean failure signal.
        """
        delay = self._RETRY_BASE_S
        for attempt in range(self._RETRY_MAX_N + 1):
            try:
                return bytes(self.target.read_memory_block8(region.start, region.size))
            except Exception as exc:
                if attempt >= self._RETRY_MAX_N:
                    raise  # propagate after all retries exhausted
                import time as _time
                _time.sleep(delay)
                delay = min(delay * 2, self._RETRY_MAX_S)
        # unreachable; silence mypy
        raise RuntimeError("unreachable")  # pragma: no cover


class Uart5LongRange(LiveTransport):
    """Full-duplex 115200-baud UART5 observer with a request-then-reassemble read."""

    name = "uart5"
    cost_model = CostModel(
        8.0, 0.31, "uart5-long-range",
        "estimated, not measured; conservative 115200-baud model until calibrate() runs",
    )
    gap_merge_bytes = 26

    _REPLY_FRAME = 0x07
    _ERROR_FRAME = 0x7F
    _SUBSCRIBE_CMD = 0x20
    _QUIET_DRAIN_S = 0.05

    def __init__(self, port: str, baud: int = 115200, timeout: float = 1.0,
                 serial_factory=None):
        if not port or str(port).upper() == "AUTO":
            raise LiveTransportError(
                "uart5-read: set a manual --uart5-port or livewatch_uart5_port"
            )
        self.port = str(port)
        self.baud = int(baud)
        self.timeout = float(timeout)
        self._serial_factory = serial_factory
        self._serial = None
        self._rx = bytearray()

    def connect(self) -> "Uart5LongRange":
        factory = self._serial_factory
        if factory is None:
            import serial
            factory = serial.Serial
        try:
            self._serial = factory(self.port, self.baud, timeout=0.05)
            self._drain_quiet_window()
        except LiveTransportError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise LiveTransportError(f"uart5-read: could not open {self.port}: {exc}") from exc
        return self

    def close(self) -> None:
        serial_obj = self._serial
        self._serial = None
        self._rx.clear()
        if serial_obj is not None:
            serial_obj.close()

    def _drain_quiet_window(self) -> None:
        """Discard any in-flight bytes from a previous operator session.

        Read-only observation: no command frame is sent on connect. The transport
        waits for the next 0x07 reply to arrive naturally during a sample() call.
        """
        deadline = time.monotonic() + self._QUIET_DRAIN_S
        while time.monotonic() < deadline:
            waiting = getattr(self._serial, "in_waiting", 0)
            chunk = self._serial.read(waiting or 1)
            if not chunk:
                time.sleep(0.005)
        if self._rx:
            self._rx.clear()

    def sample(self, plan: "Plan") -> list[bytes]:
        if self._serial is None:
            raise LiveTransportError("uart5-read: not connected")
        request = self._build_request(plan.symbols)
        try:
            self._serial.write(request)
        except Exception as exc:
            raise LiveTransportError(f"uart5-read: write failed: {exc}") from exc
        _, count, payload = self._wait_for_frame(
            self._REPLY_FRAME,
            "firmware subscription reply (uart5_address_subscription_cmd has not shipped)",
        )
        scalars = self._decode_tuples(payload, count)
        return self._reassemble(plan, scalars)

    def _build_request(self, symbols) -> bytes:
        """Build the pinned subscribe request frame for the resolved symbols.

        See module docstring. ``MAX_NUM_BASIS`` carries the resolved tuple count.
        The size for each symbol is constrained to ``{1, 2, 4}`` per the
        firmware-side validator; the host walker raises ``LiveTransportError``
        if any symbol violates that, rather than letting the firmware silently
        reject an out-of-range scalar.
        """
        tuples = []
        for sym in symbols:
            if sym.size not in (1, 2, 4):
                raise LiveTransportError(
                    f"uart5-read: symbol {sym.name!r} size {sym.size} not in {{1,2,4}}"
                )
            tuples.append(struct.pack("<IH", sym.address, sym.size))
        payload = b"".join(tuples)
        count = len(tuples)
        body = bytes((self._SUBSCRIBE_CMD, (len(payload) >> 8) & 0xFF,
                      len(payload) & 0xFF, count)) + payload
        crc = 0
        for byte in body:
            crc ^= byte
        # Request frame header is 0xCC 0xDE (extended IF-01 prefix); reply
        # frames (parsed by _pop_frame) still key on 0xAA 0xBB. The two
        # headers are deliberately distinct so a stray request byte in the
        # reply stream cannot be mis-decoded as the start of a reply.
        return b"\xCC\xDE" + body + bytes((crc,))

    @staticmethod
    def _reassemble(plan, scalars: dict[tuple[int, int], bytes]) -> list[bytes]:
        """Reassemble per-symbol scalar tuples into the coalesced ``plan.regions``.

        The firmware replies with one tuple per requested ``(address, size)``.
        ``build_plan`` may coalesce adjacent scalars into a larger region, so the
        host has to reconstruct the original region bytes by walking the
        plan's symbols in order and concatenating their scalar values.
        """
        blocks = []
        for region in plan.regions:
            buf = bytearray()
            for sym in plan.symbols:
                if region.start <= sym.address < region.end:
                    key = (sym.address, sym.size)
                    if key not in scalars:
                        raise LiveTransportError(
                            f"uart5-read: reply omitted {sym.name!r} "
                            f"at 0x{sym.address:08X}+{sym.size}"
                        )
                    buf.extend(scalars[key])
            if not buf:
                raise LiveTransportError(
                    f"uart5-read: empty region 0x{region.start:08X}+{region.size}"
                )
            blocks.append(bytes(buf))
        return blocks

    def _wait_for_frame(self, wanted_type: int, purpose: str) -> tuple[int, int, bytes]:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            frame = self._pop_frame()
            if frame is not None:
                frame_type, _count, payload = frame
                if frame_type == wanted_type:
                    return frame
                if frame_type == self._ERROR_FRAME:
                    msg = payload.decode("utf-8", errors="replace").rstrip("\x00")
                    raise LiveTransportError(f"uart5-read: firmware error: {msg}")
                continue
            waiting = getattr(self._serial, "in_waiting", 0)
            chunk = self._serial.read(waiting or 1)
            if chunk:
                self._rx.extend(chunk)
        raise LiveTransportError(f"uart5-read: no reply on {purpose}")

    def _pop_frame(self) -> tuple[int, int, bytes] | None:
        return pop_frame(self._rx)

    @staticmethod
    def _decode_tuples(payload: bytes, count: int) -> dict[tuple[int, int], bytes]:
        out = {}
        offset = 0
        for _ in range(count):
            if offset + 6 > len(payload):
                raise LiveTransportError("uart5-read: truncated address+value tuple")
            address, size = struct.unpack_from("<IH", payload, offset)
            offset += 6
            end = offset + size
            if end > len(payload):
                raise LiveTransportError("uart5-read: truncated tuple value")
            out[(address, size)] = payload[offset:end]
            offset = end
        if offset != len(payload):
            raise LiveTransportError("uart5-read: trailing bytes after address+value tuples")
        return out


# ----------------------------------------------------------------------
# USART3 over MicoAir WiFi link
# ----------------------------------------------------------------------

class UdpDataPort:
    """serial.Serial look-alike over the MicoAir module's UDP downlink.

    The module forwards USART3's byte stream as datagrams to UDP 14550; a data
    frame can span several datagrams, so datagrams are reassembled into one
    byte stream and exposed through the same ``in_waiting`` / ``read`` interface
    the serial path uses.

    THE NUDGE: the module routes its UDP downlink to the source of the most
    recent uplink datagram. A bind alone receives NOTHING. ``__init__`` sends
    one 1-byte datagram from THIS socket so the downlink lands here. The byte
    is harmless on the FC: `Handle_USART3_GroundStation_Command` parses `0xCC 0xDD`
    command frames; a single-byte nudge is a malformed frame and is ignored.
    """

    def __init__(self, port: int = 14550, module_ip: str = "192.168.4.1"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
        sock.settimeout(0)  # non-blocking drain; caller paces itself
        try:
            sock.bind(("0.0.0.0", port))
        except OSError as exc:
            sock.close()
            raise LiveTransportError(
                "usart3-read: cannot bind UDP %d (%s) -- "
                "is another process holding it?" % (port, exc))
        sock.sendto(b"\x00", (module_ip, port))  # aim the downlink here
        self._sock = sock
        self._buf = bytearray()

    def _drain(self):
        while True:
            try:
                chunk, _ = self._sock.recvfrom(65535)
            except OSError:  # timeout(0) raises BlockingIOError, an OSError
                return
            if not chunk:
                return
            self._buf.extend(chunk)

    @property
    def in_waiting(self) -> int:
        self._drain()
        return len(self._buf)

    def read(self, n: int) -> bytes:
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def reset_input_buffer(self):
        self._buf.clear()
        self._drain()

    def close(self):
        self._sock.close()


class Usart3LongRange(LiveTransport):
    """Read-only USART3 telemetry from the MicoAir WiFi module.

    Subscribe requests go out over WiFi (Usart3WifiSubscribeTransport) -- the
    unified subscribe lane.  Data frames arrive on this socket (UDP 14550,
    forwarded by the module from USART3).  Measured 2026-08-09: 90363 B/s at
    0.00%% loss = 98.8%% of the 921600-baud wire.
    """

    name = "usart3"
    cost_model = CostModel(
        8.0, 0.31, "usart3-long-range",
        "conservative 921600-baud model; 90363 B/s measured 2026-08-09",
    )
    gap_merge_bytes = 26

    def __init__(self, port: int = 14550, module_ip: str = "192.168.4.1"):
        self.port = int(port)
        self.module_ip = module_ip
        self._udp: UdpDataPort = UdpDataPort(self.port, self.module_ip)
        self._rx = bytearray()
        self.timeout = 2.0
        self._subscribed_slot = None

    def connect(self) -> "Usart3LongRange":
        return self

    def close(self) -> None:
        udp = self._udp
        self._udp = None
        self._rx.clear()
        if udp is not None:
            udp.close()

    def sample(self, plan) -> list[bytes]:
        # Usart3LongRange is data-only; caller must send subscribe over UART5 first.
        # The Live Log tab wires the UART5 command path separately.
        raise LiveTransportError(
            "usart3-read: subscribe requests must go over UART5; "
            "use 'WiFi (USART3 + UART5)' in the transport selector"
        )

    def _drain_quiet_window(self) -> None:
        deadline = time.monotonic() + 0.05
        while time.monotonic() < deadline:
            self._udp._drain()
            if not self._udp.in_waiting:
                time.sleep(0.005)
        self._rx.clear()

    def _pop_frame(self) -> tuple[int, int, bytes] | None:
        return pop_frame(self._rx)


# ----------------------------------------------------------------------
# WiFi subscribe: send 0xCC 0xDE 0x21 stream request over WiFi UDP, receive on same socket
# ----------------------------------------------------------------------

class Usart3WifiSubscribeTransport(Usart3LongRange):
    """Send 0x21 stream-subscribe requests over WiFi; receive 0x08/0x7F replies on WiFi.

    This is the transport for the unified subscribe lane: the subscribe control
    plane now lives on USART3 (WiFi) instead of UART5.  The firmware accepts
    0xCC 0xDE 0x21 requests on USART3 (Handle_USART3_GroundStation_Command with
    allow_subscribe=1) and replies with 0x08 schema / 0x7F error back on the
    same USART3 TX DMA ring.  Replies arrive as UDP datagrams on the same port
    the request was sent from.

    One socket (the parent's _udp._sock, bound to port) is used for both TX
    and RX so the MicoAir routes replies to the same source port the request
    came from.  This matches UdpDataPort's documented nudge pattern.
    """

    name = "wifi"
    cost_model = CostModel(
        8.0, 0.31, "wifi-subscribe",
        "inherits Usart3LongRange cost model; USART3 wire is the bottleneck",
    )

    _SCHEMA_FRAME = 0x08   # firmware's acknowledgement of an accepted subscription
    _ERROR_FRAME  = 0x7F   # firmware's error reply

    def close(self) -> None:
        """Stop this transport's subscription before releasing the UDP socket."""
        slot = self._subscribed_slot
        if slot is not None:
            try:
                from ground_station.livewatch.stream import (
                    TRANSPORT_USART3, build_stream_request)
                request = build_stream_request(
                    [], divider=0, transport=TRANSPORT_USART3,
                    slot=slot, usart3_baud=921600)
                self._udp._sock.sendto(request, (self.module_ip, self.port))
            except OSError:
                pass
            self._subscribed_slot = None
        super().close()

    def connect(self) -> "Usart3WifiSubscribeTransport":
        # Parent sets up self._udp (UdpDataPort: bound socket, ready to RX).
        # Do NOT open a separate TX socket -- using the same socket keeps the
        # source port stable so the module routes replies back here.
        super().connect()
        return self

    def subscribe(self, symbols, divider: int = 1,
                  transport: int = 1, slot: int = 0) -> "StreamSchema":
        """Send a 0xCC 0xDE 0x21 subscribe request and return the firmware's 0x08 schema.

        Used by manifest_layer to establish a subscription and retrieve the channel
        layout.  Call ``subscribe()`` first, then use ``self`` as a normal
        Usart3LongRange to read the streaming data.
        """
        from ground_station.livewatch.stream import build_stream_request, decode_schema
        request = build_stream_request(
            [self._sym_to_range(s) for s in symbols],
            divider=divider,
            transport=transport,
            slot=slot,
            usart3_baud=921600,
        )
        self._subscribed_slot = slot if divider else None
        self._udp._sock.sendto(request, (self.module_ip, self.port))
        _, _, payload = self._wait_for_frame(
            self._SCHEMA_FRAME,
            "firmware 0x08 schema reply on WiFi",
        )
        self._subscribed_slot = slot if divider else None
        return decode_schema(len(symbols), payload, symbols)

    def sample(self, plan: "Plan") -> list[bytes]:
        """Subscribe the plan once, then return the next 0x09 data frame."""
        key = tuple((symbol.address, symbol.size) for symbol in plan.symbols)
        if getattr(self, "_schema_key", None) != key:
            self._schema = self.subscribe(plan.symbols, divider=1, transport=1, slot=3)
            self._schema_key = key

        _, _, payload = self._wait_for_frame(
            self._schema.data_frame_type,
            "firmware 0x09 stream data on WiFi",
        )
        if len(payload) != 4 + self._schema.total_bytes:
            raise LiveTransportError(
                "wifi: stream payload is %d B, expected %d B"
                % (len(payload), 4 + self._schema.total_bytes))

        value_bytes = payload[4:]
        by_address = {}
        offset = 0
        for stream_range in self._schema.ranges:
            by_address[stream_range.address] = value_bytes[
                offset:offset + stream_range.nbytes]
            offset += stream_range.nbytes

        blocks = []
        for region in plan.regions:
            block = bytearray(region.size)
            for symbol in plan.symbols:
                if region.start <= symbol.address < region.end:
                    raw = by_address.get(symbol.address)
                    if raw is None or len(raw) != symbol.size:
                        raise LiveTransportError(
                            "wifi: stream schema does not cover %s" % symbol.name)
                    start = symbol.address - region.start
                    block[start:start + symbol.size] = raw
            blocks.append(bytes(block))
        return blocks

    def _sym_to_range(self, sym) -> "StreamRange":
        from ground_station.livewatch.stream import StreamRange
        return StreamRange(sym.address, sym.size, 1, name=sym.name)

    def _wait_for_frame(self, wanted_type: int, purpose: str,
                        timeout: float | None = None) -> tuple[int, int, bytes]:
        deadline = time.monotonic() + (timeout if timeout is not None
                                       else self.timeout)
        while time.monotonic() < deadline:
            frame = self._pop_frame()
            if frame is not None:
                frame_type, _count, payload = frame
                if frame_type == wanted_type:
                    return frame
                if frame_type == self._ERROR_FRAME:
                    msg = payload.decode("utf-8", errors="replace").rstrip("\x00")
                    raise LiveTransportError(f"wifi: firmware error: {msg}")
                continue
            self._udp._drain()
            udp_buffer = getattr(self._udp, "_buf", None)
            if udp_buffer:
                self._rx.extend(self._udp.read(len(udp_buffer)))
            time.sleep(0.002)
        raise LiveTransportError(f"wifi: timeout waiting for {purpose}")

