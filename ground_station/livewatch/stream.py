"""Host side of the 0x21 streaming subscription -- schema once, values after.

The 0x20 path in :mod:`transport` is a poll: one request, one 0x07 reply, done.
This module drives the streaming variant, which differs in three ways.

**Schema once.** A 0x07 reply repeats the address and size of every value in
every frame -- 10 bytes on the wire per float32, 60% of it constant. Streaming
sends that schema exactly once (frame 0x08) and the data frames (0x09) carry
values only::

    32 float32, repeating schema : 6 + 32*10 + 1 = 327 B  -> 26.3 kB/s at 80 Hz
    32 float32, schema once      : 6 + 32*4  + 1 = 135 B  -> 10.9 kB/s at 80 Hz

**Ranges, not scalars.** A tuple is ``(address, size, count)``, so one entry
names a whole contiguous array. Subscribing to a 128-weight MRAC vector costs
one range, not 128 tuples -- the request stays ~200 B regardless.

**Split transports.** The request always goes out on UART5 (the wireless
CMSIS-DAP link); the data stream can be routed to USART3 (the radio) instead.
Control plane on the debugger, data plane on the radio. `0xCC 0xDE 0x21` subscribe
requests are accepted on UART5 only (no reply DMA on USART3); `0xCC 0xDD` command
frames ARE wired on USART3 (`Handle_USART3_GroundStation_Command`, BSP/usart3.c).

Wire layout is pinned in ``API/subscribe.h``. The constants below mirror it,
``tests/test_stream.py`` re-reads that header on every run so the two cannot
drift, and the encoder was cross-checked against ``API/subscribe.c`` compiled
on the host before this module shipped.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass

from .transport import LiveTransportError, pop_frame

STREAM_CMD = 0x21
SCHEMA_FRAME = 0x08
DATA_FRAME = 0x09       # base; slot n uses DATA_FRAME + n
ERROR_FRAME = 0x7F

# Mirrors API/subscribe.h. Kept in sync by test_stream_firmware_parity.py, which
# greps the header rather than trusting these to be updated by hand.
# RAISED 24 -> 62 on 2026-09-07: the old cap was sized for UART5's 256 B staging.
# USART3's mailbox is 512 B; payload 3 + 62*8 = 499 B fits with 13 B to spare.
# Packing 6-element Whatf vectors as one range collapses 6 vars to 1 entry,
# so 62 ranges comfortably covers the 100+ scalar vars in the comprehensive
# flight-test manifests.
MAX_STREAM_RANGES = 62
STREAM_MAX_BYTES = 1024

# Concurrent subscriptions. Each slot has its own variables, rate and transport,
# so one link can carry signals that deserve very different attention:
#   slot 0  Theta weights + attitude   ~80 Hz
#   slot 1  battery, RPM               ~10 Hz
#   slot 2  EKF states                  ~2 Hz
# The firmware emits at most one data frame per Send_Task cycle and serves due
# slots round-robin, so a fast slot cannot starve a slow one.
MAX_SLOTS = 4

# Mirrors SUBSCRIBE_MAX_FRAMES_PER_TICK in API/subscribe.h (added in
# TASK_ID=telemetry-throughput-2026-09-08). The firmware wraps
# Subscribe_StreamTick() in a for-loop up to N times so a single Send_Task
# cycle can drain multiple due slots back-to-back instead of letting the
# rest stay due across the next 5 ms window. Default = MAX_SLOTS because
# that's the most slots that can ever be simultaneously due.
SUBSCRIBE_MAX_FRAMES_PER_TICK = 4

# Non-value bytes in a data frame: 6 header + 4 source timestamp + 2 CRC16.
# Mirrors SUBSCRIBE_STREAM_FRAME_OVERHEAD in API/subscribe.h.
FRAME_OVERHEAD = 12

# Two different Send_Task rates, on purpose.
#   SEND_TASK_HZ  — the NOMINAL 200 Hz Send_Task cycles at (USER/main.c
#                   Send_Task: 5 ms vTaskDelay floor). Used ONLY for the
#                   budget check, where over-estimating the rate makes the
#                   guard reject early -- which is the safe direction.
#                   Post-throughput-fix (TASK_ID=telemetry-throughput-2026-09-08)
#                   the busy-wait on UART4 DMA is gone and the measured
#                   cadence on a powered drone is ~200 Hz in SUBSCRIBE_ONLY
#                   mode but only ~80 Hz in MIXED mode (legacy Frame A/B/C
#                   still goes out, stealing ~3 ms per cycle from
#                   Send_Task's 5 ms floor).
#   SEND_TASK_MEASURED_HZ — what Send_Task actually cycles at in MIXED mode
#                   steady-state on a powered drone. The 2026-09-12 cold-boot
#                   4-slot capture fit ``effective_hz = 80 / divider`` across
#                   all four slots (slot 0 4.0 Hz @ div=20, slot 1 40 Hz @
#                   div=2, slots 2/3 20 Hz @ div=4) -- see
#                   ``.agent_contracts/2026-09-12-wire-budget/journal.md``.
#                   Use this constant for the divider arithmetic and the Hz
#                   reported back to the user, so "--rate 1" means 1 cycle
#                   ~ every 12.5 ms until a fresh on-drone measurement in
#                   SUBSCRIBE_ONLY confirms 200 Hz is safe. That is the safe
#                   direction: under-reporting cadence makes the user's
#                   scheduler over-budget, never under.
SEND_TASK_HZ = 200
SEND_TASK_MEASURED_HZ = 80.0

TRANSPORT_UART5 = 0
TRANSPORT_USART3 = 1
_TRANSPORT_NAMES = {TRANSPORT_UART5: "uart5", TRANSPORT_USART3: "usart3"}

# UART5 already carries frames A/B/C at a measured 8569 B/s = 74% of its
# 11520 B/s capacity, so a stream there gets a thin slice. A USART3 stream
# suppresses usart3_send() and owns the link. Must mirror API/subscribe.h's
# SUBSCRIBE_BUDGET_PCT_* (test_constants_match_firmware_header enforces it).
# The USART3 figure was raised 90 -> 95 on 2026-08-09: the TX ring rework
# proved the baud-derived cap IS the wire ceiling (90363 B/s measured clean =
# 98.8% of it), so the guard arithmetic is finally honest.
BUDGET_PCT = {TRANSPORT_UART5: 20, TRANSPORT_USART3: 95}

_SRAM = (0x20000000, 0x2001FFFF)
_CCM = (0x10000000, 0x1000FFFF)

# Host-side only -- never on the wire. The firmware ships raw bytes; the caller
# says how to read them back.
_FMT_WIDTH = {"f": 4, "i": 4, "I": 4, "h": 2, "H": 2, "b": 1, "B": 1}


@dataclass(frozen=True)
class StreamRange:
    """``count`` consecutive ``size``-byte elements starting at ``address``."""

    address: int
    size: int
    count: int = 1
    name: str = ""
    fmt: str | None = None

    @property
    def nbytes(self) -> int:
        return self.size * self.count

    def decode(self, raw: bytes):
        """Unpack this range's slice of a data frame."""
        if self.fmt is None:
            return raw
        return list(struct.unpack("<%d%s" % (self.count, self.fmt), raw))


def _validate(ranges, divider: int, transport: int, usart3_baud: int,
              slot: int = 0, other_bps: int = 0) -> int:
    """Mirror the firmware validator so failures surface here, not as a 0x7F.

    ``other_bps`` is the bandwidth already committed by the OTHER slots on this
    transport; the firmware budgets the sum, so the host must too or it would
    cheerfully build a request the drone then rejects.

    Returns the total data-frame payload width in bytes.
    """
    if transport not in BUDGET_PCT:
        raise LiveTransportError(f"stream: unknown transport {transport!r}")
    if not 0 <= slot < MAX_SLOTS:
        raise LiveTransportError(
            f"stream: slot {slot} outside 0..{MAX_SLOTS - 1}")
    if not 0 <= divider <= 255:
        raise LiveTransportError(f"stream: divider {divider} outside 0..255")
    if divider == 0:
        return 0  # explicit stop; ranges are ignored by the firmware
    if not ranges:
        raise LiveTransportError("stream: no ranges (use divider=0 to stop)")
    if len(ranges) > MAX_STREAM_RANGES:
        raise LiveTransportError(
            f"stream: {len(ranges)} ranges exceeds the firmware limit of "
            f"{MAX_STREAM_RANGES} -- widen a range's count instead of adding entries"
        )

    total = 0
    for rng in ranges:
        label = rng.name or f"0x{rng.address:08X}"
        if rng.size not in (1, 2, 4):
            raise LiveTransportError(f"stream: {label} size {rng.size} not in {{1,2,4}}")
        if rng.count < 1:
            raise LiveTransportError(f"stream: {label} count {rng.count} < 1")
        if rng.fmt is not None:
            if rng.fmt not in _FMT_WIDTH:
                raise LiveTransportError(f"stream: {label} unknown fmt {rng.fmt!r}")
            if _FMT_WIDTH[rng.fmt] != rng.size:
                raise LiveTransportError(
                    f"stream: {label} fmt {rng.fmt!r} is "
                    f"{_FMT_WIDTH[rng.fmt]} B but size is {rng.size}"
                )
        if rng.address % rng.size:
            raise LiveTransportError(
                f"stream: {label} address 0x{rng.address:08X} not aligned to {rng.size}"
            )
        last = rng.address + rng.nbytes - 1
        for lo, hi in (_SRAM, _CCM):
            if lo <= rng.address <= hi:
                if last > hi:
                    raise LiveTransportError(
                        f"stream: {label} spans past the end of its region "
                        f"(0x{rng.address:08X}+{rng.nbytes} > 0x{hi:08X})"
                    )
                break
        else:
            raise LiveTransportError(
                f"stream: {label} address 0x{rng.address:08X} outside SRAM/CCM"
            )
        total += rng.nbytes

    if total > STREAM_MAX_BYTES:
        raise LiveTransportError(
            f"stream: {total} B payload exceeds the firmware buffer "
            f"({STREAM_MAX_BYTES} B = {STREAM_MAX_BYTES // 4} float32)"
        )

    baud = usart3_baud if transport == TRANSPORT_USART3 else 115200
    frame_bytes = FRAME_OVERHEAD + total
    bps = frame_bytes * SEND_TASK_HZ // divider + other_bps
    allowed = (baud // 10) * BUDGET_PCT[transport] // 100
    if bps > allowed:
        others = f" (incl. {other_bps} B/s from other slots)" if other_bps else ""
        raise LiveTransportError(
            f"stream: {frame_bytes} B every {divider} cycle(s) = {bps} B/s{others} "
            f"exceeds the {_TRANSPORT_NAMES[transport]} budget of {allowed} B/s "
            f"({BUDGET_PCT[transport]}% of {baud} baud). Raise the divider, drop "
            f"variables, or raise the baud."
        )
    return total


def stream_bps(total_bytes: int, divider: int) -> int:
    """Bandwidth a subscription costs, by the same arithmetic the firmware uses."""
    if divider <= 0:
        return 0
    return (FRAME_OVERHEAD + total_bytes) * SEND_TASK_HZ // divider


def build_stream_request(ranges, divider: int,
                         transport: int = TRANSPORT_USART3,
                         # Mirrors USART3_BAUD in BSP/usart3.h. The old 115200
                         # default belonged to the retired 24RF and silently
                         # rejected every USART3 subscription above ~1 kB/s.
                         usart3_baud: int = 921600,
                         slot: int = 0,
                         other_bps: int = 0) -> bytes:
    """Build the 0x21 subscribe frame. ``divider=0`` stops that slot's stream."""
    _validate(ranges, divider, transport, usart3_baud, slot, other_bps)
    if divider == 0:
        ranges = []
    payload = bytes((divider, transport, slot)) + b"".join(
        struct.pack("<IHH", r.address, r.size, r.count) for r in ranges
    )
    body = bytes((STREAM_CMD, (len(payload) >> 8) & 0xFF,
                  len(payload) & 0xFF, len(ranges))) + payload
    crc = 0
    for byte in body:
        crc ^= byte
    return b"\xCC\xDE" + body + bytes((crc,))


# --------------------------------------------------------------------------
# 0x26 SUBSCRIBE_NAMED — host sends symbol hashes, firmware resolves to ranges
#
# Wire layout (mirrors API/subscribe.c:0x26 handler per
# docs/subscribe-symbol-table-contract.md):
#
#   [0xCC 0xDD] [0x26] [LEN_HI LEN_LO]
#               [divider:1] [transport:1] [slot:1] [n_named:1]
#               [(hash:uint16 LE, elem_type:uint8, count:uint8) × n_named]
#               [CRC8 XOR over CMD .. last named entry]
#
# Validated at build time by mirroring the firmware's per-entry rules
# (size = SUBSCRIBE_*_TYPE bytes * count, etc).
# --------------------------------------------------------------------------

# SUBSCRIBE_NAMED_CMD mirrors API/subscribe.h.
SUBSCRIBE_NAMED_CMD = 0x26


def build_named_request(named: list, divider: int,
                        transport: int = TRANSPORT_USART3,
                        usart3_baud: int = 921600,
                        slot: int = 0) -> bytes:
    """Build the 0x26 SUBSCRIBE_NAMED frame.

    `named` is a list of objects with (hash, elem_type, count) -- typically
    derived from catalog.CatalogEntry. The caller is responsible for
    consulting the catalog and resolving names.

    Payload bytes [divider, transport, slot, n_named, ...entries]
    """
    if not 0 <= divider <= 255:
        raise LiveTransportError(f"named: divider {divider} outside 0..255")
    if transport not in (TRANSPORT_UART5, TRANSPORT_USART3):
        raise LiveTransportError(f"named: transport {transport!r} invalid")
    if not 0 <= slot <= 3:
        raise LiveTransportError(f"named: slot {slot} outside 0..3")
    if divider == 0:
        n_named = 0
    else:
        if not named:
            raise LiveTransportError("named: empty list (use divider=0 to stop)")
        if len(named) > MAX_STREAM_RANGES:
            raise LiveTransportError(
                f"named: {len(named)} entries exceeds cap {MAX_STREAM_RANGES}")
        n_named = len(named)

    payload = bytes((divider, transport, slot, n_named))
    # Per-entry: 2 B hash + 1 B elem_type + 1 B count = 4 B (per contract).
    payload += b"".join(
        struct.pack("<HBB", e.hash, e.elem_type, e.count)
        for e in named
    ) if n_named else b""

    body = bytes((SUBSCRIBE_NAMED_CMD,
                  (len(payload) >> 8) & 0xFF,
                  len(payload) & 0xFF)) + payload
    crc = 0
    for byte in body:
        crc ^= byte
    return b"\xCC\xDD" + body + bytes((crc,))


@dataclass(frozen=True)
class StreamSchema:
    """The firmware's 0x08 acknowledgement of an accepted subscription."""

    divider: int
    transport: int
    total_bytes: int
    ranges: tuple
    slot: int = 0

    @property
    def hz(self) -> float:
        """Expected data-frame rate, from the MEASURED Send_Task cadence."""
        return SEND_TASK_MEASURED_HZ / self.divider if self.divider else 0.0

    @property
    def frame_bytes(self) -> int:
        return FRAME_OVERHEAD + self.total_bytes

    @property
    def data_frame_type(self) -> int:
        """The 0x09-based frame type this slot's data arrives under."""
        return DATA_FRAME + self.slot

    @property
    def bps(self) -> int:
        return stream_bps(self.total_bytes, self.divider)


def decode_schema(n_ranges: int, payload: bytes, requested=()) -> StreamSchema:
    """Decode a 0x08 payload. ``requested`` supplies names/fmt for the echo."""
    if len(payload) != 5 + n_ranges * 8:
        raise LiveTransportError(
            f"stream: schema payload is {len(payload)} B, expected {5 + n_ranges * 8}"
        )
    divider, transport, slot, total_bytes = struct.unpack_from(">BBBH", payload, 0)
    if not 0 <= slot < MAX_SLOTS:
        raise LiveTransportError(f"stream: schema names slot {slot}, out of range")
    by_addr = {(r.address, r.size): r for r in requested}
    ranges = []
    for i in range(n_ranges):
        address, size, count = struct.unpack_from("<IHH", payload, 5 + i * 8)
        hint = by_addr.get((address, size))
        if requested and hint is None:
            # The request travels under a CRC8 XOR, which cannot see a byte
            # transposition -- a corrupted request could subscribe to a
            # DIFFERENT valid address and quietly fill the CSV with the wrong
            # variable under the right column name. The echo is the check.
            raise LiveTransportError(
                "stream: firmware echoed range 0x%08X/%dB, which was not "
                "requested -- the subscription was corrupted in flight"
                % (address, size))
        ranges.append(StreamRange(
            address, size, count,
            name=hint.name if hint else "",
            fmt=hint.fmt if hint else None,
        ))
    schema = StreamSchema(divider, transport, total_bytes, tuple(ranges), slot)
    if sum(r.nbytes for r in ranges) != total_bytes:
        raise LiveTransportError(
            "stream: schema total_bytes disagrees with its own ranges"
        )
    return schema


class StreamDecoder:
    """Turns a byte stream of 0x09 frames into named values, counting gaps.

    The firmware skips a frame (rather than blocking) when the previous DMA
    transfer has not drained, and the radio itself drops frames. Both show up
    as a jump in ``SEQ``, so ``dropped`` is the honest count of frames that
    never arrived -- distinct from ``crc_errors``, which counts frames that
    arrived corrupted.
    """

    def __init__(self, schema: StreamSchema):
        self.schema = schema
        self._rx = bytearray()
        self.received = 0
        self.dropped = 0
        self.crc_errors = 0
        self._last_seq: int | None = None

    def feed(self, chunk: bytes) -> list[tuple[int, int, dict]]:
        """Add received bytes; return ``(seq, t_ms, values)`` per sample."""
        self._rx.extend(chunk)
        out = []
        while True:
            frame = pop_frame(self._rx)
            if frame is None:
                return out
            frame_type, byte5, payload = frame
            if frame_type == ERROR_FRAME:
                msg = payload.decode("utf-8", errors="replace").rstrip("\x00")
                raise LiveTransportError(f"stream: firmware error: {msg}")
            if frame_type != self.schema.data_frame_type:
                continue  # another slot, a 0x08 echo, or unrelated telemetry
            if len(payload) != 4 + self.schema.total_bytes:
                self.crc_errors += 1
                continue
            self.received += 1
            if self._last_seq is not None:
                gap = (byte5 - self._last_seq - 1) & 0xFF
                self.dropped += gap
            self._last_seq = byte5
            # Source timestamp, stamped in the cycle that copied the values.
            # This is the honest clock; the host's arrival time is not.
            t_ms = struct.unpack_from("<I", payload, 0)[0]
            out.append((byte5, t_ms, self._split(payload[4:])))

    def _split(self, payload: bytes) -> dict:
        values, offset = {}, 0
        for i, rng in enumerate(self.schema.ranges):
            raw = payload[offset:offset + rng.nbytes]
            offset += rng.nbytes
            values[rng.name or f"r{i}@0x{rng.address:08X}"] = rng.decode(raw)
        return values

    @property
    def loss_pct(self) -> float:
        total = self.received + self.dropped
        return 100.0 * self.dropped / total if total else 0.0


class MultiStreamDecoder:
    """Demultiplex several slots out of one byte stream.

    Slots share a wire, so their frames interleave. Each carries its own frame
    type (0x09 + slot) and its own SEQ counter, which is what makes them
    separable without any extra framing -- and what lets a 2 Hz slot's loss be
    measured independently of an 80 Hz one.
    """

    def __init__(self, schemas):
        self.decoders = {}
        for schema in schemas:
            if schema.slot in self.decoders:
                raise LiveTransportError(
                    f"stream: slot {schema.slot} subscribed twice")
            self.decoders[schema.slot] = StreamDecoder(schema)
        self._rx = bytearray()

    def feed(self, chunk: bytes) -> list[tuple[int, int, int, dict]]:
        """Return ``(slot, seq, t_ms, values)`` for every sample completed."""
        self._rx.extend(chunk)
        out = []
        while True:
            frame = pop_frame(self._rx)
            if frame is None:
                return out
            frame_type, byte5, payload = frame
            if frame_type == ERROR_FRAME:
                msg = payload.decode("utf-8", errors="replace").rstrip("\x00")
                raise LiveTransportError(f"stream: firmware error: {msg}")
            slot = frame_type - DATA_FRAME
            decoder = self.decoders.get(slot)
            if decoder is None or frame_type != decoder.schema.data_frame_type:
                continue
            if len(payload) != 4 + decoder.schema.total_bytes:
                decoder.crc_errors += 1
                continue
            decoder.received += 1
            if decoder._last_seq is not None:
                decoder.dropped += (byte5 - decoder._last_seq - 1) & 0xFF
            decoder._last_seq = byte5
            t_ms = struct.unpack_from("<I", payload, 0)[0]
            out.append((slot, byte5, t_ms, decoder._split(payload[4:])))

    @property
    def received(self) -> int:
        return sum(d.received for d in self.decoders.values())

    @property
    def dropped(self) -> int:
        return sum(d.dropped for d in self.decoders.values())

    @property
    def crc_errors(self) -> int:
        return sum(d.crc_errors for d in self.decoders.values())


def subscribe(control_serial, ranges, divider: int,
              transport: int = TRANSPORT_USART3,
              usart3_baud: int = 921600,   # mirrors USART3_BAUD, BSP/usart3.h
              slot: int = 0,
              other_bps: int = 0,
              timeout: float = 1.0) -> StreamSchema:
    """Send a 0x21 request on ``control_serial`` and wait for its 0x08 schema.

    ``control_serial`` is the UART5 port (the CMSIS-DAP VCP) -- the only port
    the firmware accepts commands on. The data frames then arrive on whichever
    port ``transport`` selected, which for USART3 is a *different* serial
    device that the caller opens separately.
    """
    control_serial.write(build_stream_request(
        ranges, divider, transport, usart3_baud, slot, other_bps))
    if divider == 0:
        return StreamSchema(0, transport, 0, (), slot)

    rx = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = pop_frame(rx)
        if frame is not None:
            frame_type, byte5, payload = frame
            if frame_type == SCHEMA_FRAME:
                return decode_schema(byte5, payload, ranges)
            if frame_type == ERROR_FRAME:
                msg = payload.decode("utf-8", errors="replace").rstrip("\x00")
                raise LiveTransportError(f"stream: firmware rejected request: {msg}")
            continue
        waiting = getattr(control_serial, "in_waiting", 0)
        chunk = control_serial.read(waiting or 1)
        if chunk:
            rx.extend(chunk)
    raise LiveTransportError(
        "stream: no 0x08 schema reply -- is the firmware with CMD 0x21 flashed?"
    )
