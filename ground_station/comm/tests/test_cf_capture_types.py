"""CF capture regression: DWARF types survive request, echo, decode and manifest.

All sockets are mocks. Synthetic cells test the actual bridge path, including
coalesced mixed-type ranges; this is host pipeline evidence, not link evidence.
"""
from __future__ import annotations

import struct
from unittest.mock import MagicMock

import pytest

from ground_station.comm.wifi_bridge import WifiBridge
from ground_station.livewatch.stream import StreamRange
from ground_station.livewatch.transport import _xor_crc, crc16_ccitt
from ground_station.service.session_schema import variables


def _bridge():
    bridge = WifiBridge(vofa_enabled=False)
    for name in ("_wifi", "_cmd_udp", "_telem_udp", "_udp_send", "_wifi_send"):
        setattr(bridge, name, MagicMock())
    return bridge


def _echo(bridge, slot=1, divider=8):
    ranges = bridge._pending_schema_ranges[slot]
    total = sum(r.nbytes for r in ranges)
    payload = bytes((divider, 1, slot)) + struct.pack(">H", total)
    payload += b"".join(struct.pack("<IHH", r.address, r.size, r.count) for r in ranges)
    body = bytes((8, len(payload) >> 8, len(payload) & 255, len(ranges))) + payload
    assert bridge._handle_schema_frame(b"\xAA\xBB" + body + bytes((_xor_crc(body),))) == slot


def _data(payload, slot=1):
    payload = struct.pack("<I", 1000) + payload
    body = bytes((9 + slot, len(payload) >> 8, len(payload) & 255, 0)) + payload
    return b"\xAA\xBB" + body + struct.pack(">H", crc16_ccitt(body))


@pytest.mark.parametrize("names,fmts,values_,types", [
    (("alt_cf.h", "alt_cf.n_corr"), ("f", "I"), (0.8, 1234), ("float32", "uint32")),
    (("ano_of.of_alt_cm", "ano_of.of2_h"), ("I", "f"), (80, 0.8), ("uint32", "float32")),
    (("signed", "unsigned"), ("h", "H"), (-40, 1234), ("int16", "uint16")),
    (("signed", "unsigned"), ("b", "B"), (-40, 250), ("int8", "uint8")),
    (("signed", "unsigned"), ("i", "I"), (-40, 1234), ("int32", "uint32")),
])
def test_mixed_cells_keep_types_through_capture(names, fmts, values_, types):
    bridge = _bridge()
    width = struct.calcsize(fmts[0])
    requested = [StreamRange(0x20001000 + i * width, width, name=name, fmt=fmt)
                 for i, (name, fmt) in enumerate(zip(names, fmts))]
    bridge.subscribe_slot(slot=1, divider=8, ranges=requested)
    _echo(bridge)
    schema = bridge._stream_schemas[1]
    assert len(schema.ranges) == 1 and schema.ranges[0]._fmts == fmts
    decoded = bridge._decode_stream_frame(1, _data(struct.pack("<" + "".join(fmts), *values_)))
    assert decoded["names"] == list(names)
    assert decoded["values"] == pytest.approx(values_)
    assert decoded["json"]["slot1." + names[1]] == pytest.approx(values_[1])
    for name, fmt in zip(names, fmts):
        if fmt != "f":
            assert isinstance(decoded["json"]["slot1." + name], int)
    assert [v["type"] for v in variables({1: schema})] == list(types)


@pytest.mark.parametrize("mode,tick_hz", [("legacy", 100), ("mixed", 100), ("subscribe_only", 200)])
def test_manifest_uses_firmware_mode_clock_divided_by_slot_divider(mode, tick_hz):
    bridge = _bridge()
    bridge.set_telemetry_mode_now(mode)
    bridge.subscribe_slot(slot=1, divider=8, ranges=[StreamRange(0x20001000, 4, name="xTickCount", fmt="I")])
    _echo(bridge)
    schema = bridge._stream_schemas[1]
    assert schema.hz == tick_hz / 8
    assert schema.bps == int((12 + 4) * tick_hz / 8)
    assert variables({1: schema})[0]["rate_hz"] == tick_hz / 8


def test_cf_height_integer_siblings_match_dwarf_types():
    from ground_station.livewatch.symbols import SymbolResolver, firmware_elf

    elf = firmware_elf()
    if not elf.exists():
        pytest.skip("requires offline firmware ELF; no hardware")
    resolver = SymbolResolver(elf)
    # All integer siblings of n_corr in Z_CF, audited against DWARF, not name heuristics.
    expected = {"alt_cf.n_corr": "I", "g_alt_vz_mode": "B", "ano_of.alt_update_cnt": "B",
                "ano_of.of_alt_cm": "I", "s_alt_valid_tick": "B", "s_alt_reject_cnt": "H",
                "s_alt_cf_inited": "B", "Throttle_th": "h"}
    for name, fmt in expected.items():
        symbol = resolver.resolve(name)
        assert symbol.fmt == fmt
        bridge = _bridge()
        bridge.subscribe_slot(slot=1, divider=8, ranges=[name])
        _echo(bridge)
        value = 1234 if symbol.size > 1 else 123
        decoded = bridge._decode_stream_frame(1, _data(struct.pack("<" + fmt, value)))
        assert decoded["values"] == [value]
        assert variables(bridge._stream_schemas)[0]["type"] != "float32"
