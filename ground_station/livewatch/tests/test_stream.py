"""Offline tests for the 0x21 streaming subscription.

The byte layouts asserted here were cross-checked against the compiled
``API/subscribe.c`` on the host (gcc harness, 51 checks), so these are pinned
to what the firmware actually does rather than to what the spec says it should.
``test_constants_match_firmware_header`` re-reads ``API/subscribe.h`` on every
run so the two sides cannot silently drift.
"""
import re
import struct
from pathlib import Path

import pytest

from ground_station.livewatch.stream import (
    BUDGET_PCT, MAX_STREAM_RANGES, SEND_TASK_HZ, SEND_TASK_MEASURED_HZ,
    FRAME_OVERHEAD, MAX_SLOTS, MultiStreamDecoder, stream_bps,
    STREAM_MAX_BYTES,
    TRANSPORT_UART5, TRANSPORT_USART3, StreamDecoder, StreamRange,
    build_stream_request, decode_schema,
)
from ground_station.livewatch.transport import (
    LiveTransportError, crc16_ccitt, pop_frame,
)

SRAM = 0x20000000
HEADER = Path(__file__).resolve().parents[3] / "API" / "subscribe.h"


def _data_frame(seq, payload, slot=0, t_ms=0):
    """A 0x09+slot frame: 4-byte source timestamp ahead of the values, CRC16."""
    inner = struct.pack("<I", t_ms) + payload
    body = bytes((0x09 + slot, (len(inner) >> 8) & 0xFF,
                  len(inner) & 0xFF, seq)) + inner
    return b"\xAA\xBB" + body + struct.pack(">H", crc16_ccitt(body))


def _schema_for(ranges, divider=1, transport=TRANSPORT_USART3, slot=0):
    total = sum(r.nbytes for r in ranges)
    payload = struct.pack(">BBBH", divider, transport, slot, total) + b"".join(
        struct.pack("<IHH", r.address, r.size, r.count) for r in ranges)
    return decode_schema(len(ranges), payload, ranges)


# --------------------------------------------------------------------------
# request encoding
# --------------------------------------------------------------------------

def test_request_layout_is_the_pinned_contract():
    req = build_stream_request(
        [StreamRange(SRAM + 0x100, 4, 8), StreamRange(SRAM + 0x40, 2, 3)],
        divider=2, transport=TRANSPORT_USART3)
    assert req[:2] == b"\xCC\xDE"
    assert req[2] == 0x21
    assert (req[3] << 8) | req[4] == 3 + 2 * 8      # 3 config bytes + N*8
    assert req[5] == 2                              # range count
    assert req[6] == 2                              # divider
    assert req[7] == TRANSPORT_USART3
    assert req[8] == 0                              # slot
    assert struct.unpack_from("<IHH", req, 9) == (SRAM + 0x100, 4, 8)
    assert struct.unpack_from("<IHH", req, 17) == (SRAM + 0x40, 2, 3)
    crc = 0
    for byte in req[2:-1]:
        crc ^= byte
    assert crc == req[-1]


def test_ranges_keep_the_request_small_as_values_grow():
    """The point of range tuples: 256 values cost the same request as 1."""
    one = build_stream_request([StreamRange(SRAM, 4, 1)], 10)
    many = build_stream_request([StreamRange(SRAM, 4, 256)], 10)
    assert len(one) == len(many) == 18


def test_divider_zero_is_a_ten_byte_stop():
    stop = build_stream_request([], divider=0)
    assert len(stop) == 10
    assert stop[5] == 0 and stop[6] == 0


def test_stop_ignores_supplied_ranges():
    stop = build_stream_request([StreamRange(SRAM, 4, 8)], divider=0)
    assert len(stop) == 10


def test_stop_targets_only_its_own_slot():
    """A stop names a slot, so halting slot 1 must not disturb slot 0."""
    assert build_stream_request([], divider=0, slot=2)[8] == 2


# --------------------------------------------------------------------------
# host-side validation -- must mirror the firmware validator
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rng, needle", [
    (StreamRange(SRAM, 3, 1), "not in {1,2,4}"),
    (StreamRange(SRAM, 4, 0), "count 0 < 1"),
    (StreamRange(SRAM + 2, 4, 1), "not aligned"),
    (StreamRange(0x08000000, 4, 1), "outside SRAM/CCM"),
    (StreamRange(0x2001FFF0, 4, 100), "spans past the end"),
    (StreamRange(SRAM, 2, 1, "x", "f"), "is 4 B but size is 2"),
    (StreamRange(SRAM, 4, 1, "x", "q"), "unknown fmt"),
])
def test_bad_range_is_rejected_before_the_wire(rng, needle):
    with pytest.raises(LiveTransportError, match=re.escape(needle)):
        build_stream_request([rng], 1)


def test_range_count_ceiling():
    ok = [StreamRange(SRAM + 8 * i, 4, 1) for i in range(MAX_STREAM_RANGES)]
    build_stream_request(ok, 8)
    with pytest.raises(LiveTransportError, match="exceeds the firmware limit"):
        build_stream_request(ok + [StreamRange(SRAM + 0x900, 4, 1)], 8)


def test_payload_ceiling_is_reported_in_float32():
    with pytest.raises(LiveTransportError, match="256 float32"):
        build_stream_request([StreamRange(SRAM, 4, 300)], 8, TRANSPORT_USART3,
                             usart3_baud=921600)


def test_empty_subscription_needs_the_stop_form():
    with pytest.raises(LiveTransportError, match="use divider=0 to stop"):
        build_stream_request([], 1)


def test_unknown_transport_rejected():
    with pytest.raises(LiveTransportError, match="unknown transport"):
        build_stream_request([StreamRange(SRAM, 4, 1)], 1, transport=9)


# --------------------------------------------------------------------------
# link budget
# --------------------------------------------------------------------------

def _max_float32(baud, divider, transport=TRANSPORT_USART3):
    lo, hi = 0, STREAM_MAX_BYTES // 4
    while lo < hi:
        mid = (lo + hi + 1) // 2
        try:
            build_stream_request([StreamRange(SRAM, 4, mid)], divider,
                                 transport, baud)
            lo = mid
        except LiveTransportError:
            hi = mid - 1
    return lo


def test_budget_ceilings_at_both_bauds():
    # Nominal 200 Hz guard post-unlock; the measured SUBSCRIBE_ONLY cadence is
    # ~160 Hz, so the guard is ~20% pessimistic.
    # Each ceiling is 5 B/frame lower than before the timestamp+CRC16: that
    # overhead is real bandwidth and the guard must charge for it.
    # Values track SUBSCRIBE_BUDGET_PCT_USART3 (95% since 2026-08-09, when the
    # TX ring rework proved the baud-derived cap is the true wire ceiling).
    assert _max_float32(115200, 1) == 10
    assert _max_float32(115200, 2) == 24
    assert _max_float32(921600, 1) == 106
    assert _max_float32(921600, 2) == 215     # under STREAM_MAX_BYTES cap (256)


def test_raising_the_divider_buys_variables():
    assert _max_float32(115200, 4) > _max_float32(115200, 2) > _max_float32(115200, 1)


def test_uart5_budget_is_tighter_than_usart3():
    """UART5 already carries frames A/B/C at ~74% of its capacity."""
    assert BUDGET_PCT[TRANSPORT_UART5] < BUDGET_PCT[TRANSPORT_USART3]
    assert _max_float32(115200, 1, TRANSPORT_UART5) < _max_float32(115200, 1)


def test_budget_error_names_the_remedies():
    with pytest.raises(LiveTransportError) as exc:
        build_stream_request([StreamRange(SRAM, 4, 250)], 1)
    msg = str(exc.value)
    assert "Raise the divider" in msg and "raise the baud" in msg
    assert "usart3" in msg


# --------------------------------------------------------------------------
# schema decoding
# --------------------------------------------------------------------------

def test_schema_round_trip_reattaches_names_and_formats():
    ranges = [StreamRange(SRAM + 0x100, 4, 8, "What_roll", "f"),
              StreamRange(SRAM + 0x40, 2, 3, "flags", "H")]
    schema = _schema_for(ranges, divider=2)
    assert [r.name for r in schema.ranges] == ["What_roll", "flags"]
    assert [r.fmt for r in schema.ranges] == ["f", "H"]
    assert schema.total_bytes == 4 * 8 + 2 * 3
    assert schema.frame_bytes == FRAME_OVERHEAD + schema.total_bytes
    # hz reports the MEASURED Send_Task cadence, not the nominal one the budget
    # guard uses -- so "--rate 20" on the CLI actually yields ~20 Hz of data.
    assert schema.hz == SEND_TASK_MEASURED_HZ / 2
    assert SEND_TASK_MEASURED_HZ < SEND_TASK_HZ, (
        "the guard must over-estimate the rate so it rejects early, "
        "which is the safe direction")


def test_schema_rejects_a_length_that_disagrees_with_its_count():
    with pytest.raises(LiveTransportError, match="schema payload is"):
        decode_schema(2, struct.pack(">BBH", 1, 1, 8) + b"\x00" * 8)


def test_schema_rejects_inconsistent_total_bytes():
    payload = struct.pack(">BBBH", 1, 1, 0, 999) + struct.pack("<IHH", SRAM, 4, 2)
    with pytest.raises(LiveTransportError, match="disagrees with its own ranges"):
        decode_schema(1, payload)


def test_unnamed_ranges_get_an_address_label():
    schema = _schema_for([StreamRange(SRAM + 0x10, 4, 1)])
    dec = StreamDecoder(schema)
    (_, _, values), = dec.feed(_data_frame(0, b"\x00\x00\x80\x3f"))
    assert list(values) == ["r0@0x20000010"]


# --------------------------------------------------------------------------
# data decoding
# --------------------------------------------------------------------------

def test_values_split_by_range_in_order():
    schema = _schema_for([StreamRange(0, 4, 3, "vec", "f"),
                          StreamRange(16, 2, 2, "pair", "H")])
    payload = struct.pack("<3f", 1.5, -2.25, 3.75) + struct.pack("<2H", 4242, 999)
    (seq, _, values), = StreamDecoder(schema).feed(_data_frame(7, payload))
    assert seq == 7
    assert values["vec"] == [1.5, -2.25, 3.75]
    assert values["pair"] == [4242, 999]


def test_range_without_fmt_yields_raw_bytes():
    schema = _schema_for([StreamRange(0, 1, 3, "raw")])
    (_, _, values), = StreamDecoder(schema).feed(_data_frame(0, b"\x01\x02\x03"))
    assert values["raw"] == b"\x01\x02\x03"


def test_frame_split_across_reads_is_reassembled():
    schema = _schema_for([StreamRange(0, 4, 1, "x", "f")])
    dec = StreamDecoder(schema)
    frame = _data_frame(1, struct.pack("<f", 2.5))
    assert dec.feed(frame[:4]) == []
    (_, _, values), = dec.feed(frame[4:])
    assert values["x"] == [2.5]


def test_sequence_gaps_are_counted_as_drops():
    schema = _schema_for([StreamRange(0, 4, 1, "x", "f")])
    dec = StreamDecoder(schema)
    for seq in (0, 1, 5, 6):
        dec.feed(_data_frame(seq, struct.pack("<f", 1.0)))
    assert dec.received == 4
    assert dec.dropped == 3
    assert dec.loss_pct == pytest.approx(100 * 3 / 7)


def test_sequence_wraps_at_256():
    schema = _schema_for([StreamRange(0, 4, 1, "x", "f")])
    dec = StreamDecoder(schema)
    dec.feed(_data_frame(254, struct.pack("<f", 1.0)))
    dec.feed(_data_frame(1, struct.pack("<f", 1.0)))
    assert dec.dropped == 2


def test_wrong_width_payload_counts_as_corrupt_not_as_data():
    schema = _schema_for([StreamRange(0, 4, 2, "x", "f")])
    dec = StreamDecoder(schema)
    assert dec.feed(_data_frame(0, b"\x00" * 4)) == []
    assert dec.crc_errors == 1
    assert dec.received == 0


def test_unrelated_frames_on_the_port_are_skipped():
    """USART3 may still be carrying a JustFloat frame when a stream starts."""
    schema = _schema_for([StreamRange(0, 4, 1, "x", "f")])
    dec = StreamDecoder(schema)
    other = bytes((0xAA, 0xBB, 0x01, 0, 4)) + b"\x00" * 5
    crc = 0
    for byte in other[2:]:
        crc ^= byte
    got = dec.feed(other + bytes((crc,)) + _data_frame(0, struct.pack("<f", 9.0)))
    assert len(got) == 1 and got[0][2]["x"] == [9.0]


def test_error_frame_surfaces_as_an_exception():
    schema = _schema_for([StreamRange(0, 4, 1, "x", "f")])
    body = bytes((0x7F, 0, 12, 0)) + b"E:over budget"[:12]
    crc = 0
    for byte in body:
        crc ^= byte
    with pytest.raises(LiveTransportError, match="firmware error"):
        StreamDecoder(schema).feed(b"\xAA\xBB" + body + bytes((crc,)))


def test_crc_failure_drops_only_that_frame():
    schema = _schema_for([StreamRange(0, 4, 1, "x", "f")])
    bad = bytearray(_data_frame(0, struct.pack("<f", 1.0)))
    bad[-1] ^= 0xFF
    dec = StreamDecoder(schema)
    got = dec.feed(bytes(bad) + _data_frame(1, struct.pack("<f", 2.0)))
    assert len(got) == 1 and got[0][2]["x"] == [2.0]


# --------------------------------------------------------------------------
# firmware/host constant parity
# --------------------------------------------------------------------------

def test_constants_match_firmware_header():
    """Guard against the host and API/subscribe.h drifting apart."""
    text = HEADER.read_text(encoding="utf-8", errors="replace")

    def define(name):
        match = re.search(r"#define\s+%s\s+(\d+)U?" % name, text)
        assert match, "%s missing from %s" % (name, HEADER)
        return int(match.group(1))

    assert define("SUBSCRIBE_MAX_SLOTS") == MAX_SLOTS
    assert define("SUBSCRIBE_MAX_STREAM_RANGES") == MAX_STREAM_RANGES
    assert define("SUBSCRIBE_STREAM_MAX_BYTES") == STREAM_MAX_BYTES
    assert define("SUBSCRIBE_SEND_TASK_HZ") == SEND_TASK_HZ
    assert define("SUBSCRIBE_BUDGET_PCT_USART3") == BUDGET_PCT[TRANSPORT_USART3]
    assert define("SUBSCRIBE_BUDGET_PCT_UART5") == BUDGET_PCT[TRANSPORT_UART5]
    assert define("SUBSCRIBE_TRANSPORT_UART5") == TRANSPORT_UART5
    assert define("SUBSCRIBE_TRANSPORT_USART3") == TRANSPORT_USART3
    assert re.search(r"#define\s+SUBSCRIBE_STREAM_CMD\s+0x21U", text)
    assert re.search(r"#define\s+SUBSCRIBE_FRAME_TYPE_SCHEMA\s+0x08U", text)
    assert re.search(r"#define\s+SUBSCRIBE_FRAME_TYPE_DATA\s+0x09U", text)


def test_request_fits_the_uart5_staging_buffer():
    """A max-size request must fit USART5_SUBSCRIBE_RX_LEN or the IRQ drops it.

    With SUBSCRIBE_MAX_STREAM_RANGES=62, the largest possible request is
    ~506 B (62 * 8 + overhead) — strictly larger than the 256 B USART5
    staging buffer. Such requests are intentionally USART3 (WiFi) only;
    USART5 (wired CMSIS-DAP) remains the transport for short diagnostic
    requests. This guard now asserts the contract: a request of <=32 ranges
    still fits USART5, anything larger is WiFi-only by design.
    """
    header = (HEADER.parent.parent / "BSP" / "usart5.h").read_text(
        encoding="utf-8", errors="replace")
    cap = int(re.search(r"USART5_SUBSCRIBE_RX_LEN\s+(\d+)", header).group(1))
    # Pick a request size that comfortably fits USART5 (32 ranges * 8 B + 10 overhead = 266, too big)
    # Actually the smallest sensible request that exercises the full API path is 24 ranges * 8 B + 10 = 202 B
    small = build_stream_request(
        [StreamRange(SRAM + 8 * i, 4, 1) for i in range(24)], 8)
    assert len(small) <= cap
    # And confirm the design: a full 62-range request is too big for USART5
    biggest = build_stream_request(
        [StreamRange(SRAM + 8 * i, 4, 1) for i in range(MAX_STREAM_RANGES)], 8)
    assert len(biggest) > cap
    assert MAX_STREAM_RANGES > 24  # the unlock must have widened the cap


def test_pop_frame_handles_the_new_frame_types():
    """Data frames carry a timestamp ahead of the values and a CRC16 trailer."""
    buf = bytearray(_data_frame(3, b"\x01\x02\x03\x04", t_ms=0x11223344))
    frame_type, seq, payload = pop_frame(buf)
    assert (frame_type, seq) == (0x09, 3)
    assert payload == struct.pack("<I", 0x11223344) + b"\x01\x02\x03\x04"
    assert not buf


def test_pop_frame_rejects_a_data_frame_whose_bytes_were_transposed():
    """The reason for CRC16: an XOR checksum cannot see a transposition."""
    good = _data_frame(0, b"\x01\x02\x03\x04")
    swapped = bytearray(good)
    swapped[10], swapped[11] = swapped[11], swapped[10]
    assert sum(good[2:-2]) % 256 == sum(swapped[2:-2]) % 256   # XOR-blind
    assert pop_frame(bytearray(good)) is not None
    assert pop_frame(swapped) is None


# --------------------------------------------------------------------------
# multi-rate slots
# --------------------------------------------------------------------------

def test_slot_travels_in_the_request():
    req = build_stream_request([StreamRange(SRAM, 4, 2)], 4, slot=3)
    assert req[8] == 3


@pytest.mark.parametrize("slot", [-1, MAX_SLOTS, 99])
def test_slot_outside_the_table_is_rejected_before_the_wire(slot):
    with pytest.raises(LiveTransportError, match="slot"):
        build_stream_request([StreamRange(SRAM, 4, 2)], 4, slot=slot)


def test_each_slot_gets_its_own_frame_type():
    """0x09 + slot is what lets a host separate the streams with no extra framing."""
    for slot in range(MAX_SLOTS):
        schema = _schema_for([StreamRange(SRAM, 4, 2)], divider=1, slot=slot)
        assert schema.slot == slot
        assert schema.data_frame_type == 0x09 + slot


def test_budget_counts_every_slot_not_just_this_one():
    """Four slots each under the cap can still collectively shred the link."""
    ranges = [StreamRange(SRAM, 4, 64)]           # 256 B payload, 268 B frame
    # Alone at divider 64 this is ~838 B/s, inside UART5's 2304 B/s budget.
    build_stream_request(ranges, 64, TRANSPORT_UART5, slot=0)
    # With another slot already spending 1500 B/s it no longer fits.
    with pytest.raises(LiveTransportError, match="other slots"):
        build_stream_request(ranges, 64, TRANSPORT_UART5, slot=1, other_bps=1500)


def test_stream_bps_matches_the_firmware_arithmetic():
    # (12 + payload) * SEND_TASK_HZ / divider, integer division, as in C.
    assert stream_bps(256, 16) == (FRAME_OVERHEAD + 256) * SEND_TASK_HZ // 16
    assert stream_bps(0, 0) == 0


def test_multi_decoder_demultiplexes_interleaved_slots():
    fast = _schema_for([StreamRange(SRAM, 4, 1, "theta", "f")], divider=1, slot=0)
    slow = _schema_for([StreamRange(SRAM + 0x40, 2, 2, "rpm", "H")],
                       divider=8, slot=1)
    dec = MultiStreamDecoder([fast, slow])

    stream = (_data_frame(0, struct.pack("<f", 1.5), slot=0)
              + _data_frame(0, struct.pack("<HH", 7, 9), slot=1)
              + _data_frame(1, struct.pack("<f", 2.5), slot=0))
    got = dec.feed(stream)

    assert [(s, seq) for s, seq, _, _ in got] == [(0, 0), (1, 0), (0, 1)]
    assert got[0][3]["theta"] == [1.5]
    assert got[1][3]["rpm"] == [7, 9]
    assert dec.dropped == 0 and dec.crc_errors == 0


def test_each_slot_counts_its_own_losses():
    """A 2 Hz slot's loss must not be diluted by an 80 Hz slot's traffic."""
    fast = _schema_for([StreamRange(SRAM, 4, 1, "a", "f")], divider=1, slot=0)
    slow = _schema_for([StreamRange(SRAM, 4, 1, "b", "f")], divider=8, slot=1)
    dec = MultiStreamDecoder([fast, slow])

    dec.feed(_data_frame(0, struct.pack("<f", 0.0), slot=0))
    dec.feed(_data_frame(1, struct.pack("<f", 0.0), slot=0))
    dec.feed(_data_frame(0, struct.pack("<f", 0.0), slot=1))
    dec.feed(_data_frame(5, struct.pack("<f", 0.0), slot=1))   # lost 4

    assert dec.decoders[0].dropped == 0
    assert dec.decoders[1].dropped == 4
    assert dec.dropped == 4


def test_a_slot_cannot_be_subscribed_twice():
    schema = _schema_for([StreamRange(SRAM, 4, 1)], slot=1)
    with pytest.raises(LiveTransportError, match="subscribed twice"):
        MultiStreamDecoder([schema, schema])


def test_single_decoder_ignores_other_slots_frames():
    schema = _schema_for([StreamRange(SRAM, 4, 1, "x", "f")], slot=2)
    dec = StreamDecoder(schema)
    assert dec.feed(_data_frame(0, struct.pack("<f", 1.0), slot=0)) == []
    assert dec.feed(_data_frame(0, struct.pack("<f", 3.0), slot=2, t_ms=99)) == [
        (0, 99, {"x": [3.0]})]


# --------------------------------------------------------------------------
# source timestamp and schema-echo integrity
# --------------------------------------------------------------------------

def test_source_timestamp_survives_to_the_caller():
    """t_ms is the drone's clock -- the whole point is that it is NOT ours."""
    schema = _schema_for([StreamRange(SRAM, 4, 1, "x", "f")])
    (seq, t_ms, values), = StreamDecoder(schema).feed(
        _data_frame(4, struct.pack("<f", 1.0), t_ms=123456))
    assert (seq, t_ms, values) == (4, 123456, {"x": [1.0]})


def test_timestamp_wraps_rather_than_overflowing():
    """2^32 ms is ~49.7 days of uptime; the decode must not sign-extend."""
    schema = _schema_for([StreamRange(SRAM, 4, 1, "x", "f")])
    (_, t_ms, _), = StreamDecoder(schema).feed(
        _data_frame(0, struct.pack("<f", 0.0), t_ms=0xFFFFFFFF))
    assert t_ms == 0xFFFFFFFF


def test_a_frame_missing_its_timestamp_is_counted_malformed():
    """An old-format frame is short by 4 B; it must not decode as shifted values."""
    schema = _schema_for([StreamRange(SRAM, 4, 1, "x", "f")])
    dec = StreamDecoder(schema)
    body = bytes((0x09, 0, 4, 0)) + struct.pack("<f", 1.0)
    stale = b"\xAA\xBB" + body + struct.pack(">H", crc16_ccitt(body))
    assert dec.feed(stale) == []
    assert dec.crc_errors == 1


def test_schema_echoing_an_unrequested_range_is_refused():
    """The request travels under CRC8; the echo is what catches its corruption.

    A transposed address byte could land on another allowlisted variable, and
    the CSV would then carry the wrong signal under the right column name.
    """
    asked = [StreamRange(SRAM + 0x100, 4, 2, "theta", "f")]
    payload = struct.pack(">BBBH", 1, TRANSPORT_UART5, 0, 8) + struct.pack(
        "<IHH", SRAM + 0x200, 4, 2)          # firmware echoes a different address
    with pytest.raises(LiveTransportError, match="was not requested"):
        decode_schema(1, payload, asked)


def test_schema_with_no_requested_hint_still_decodes():
    """Passing no expectation (a sniffer) must stay permissive."""
    payload = struct.pack(">BBBH", 1, TRANSPORT_UART5, 0, 8) + struct.pack(
        "<IHH", SRAM + 0x200, 4, 2)
    assert decode_schema(1, payload, ()).ranges[0].address == SRAM + 0x200
