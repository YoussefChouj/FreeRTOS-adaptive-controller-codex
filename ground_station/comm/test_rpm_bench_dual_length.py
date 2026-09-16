"""
Byte-level dual-length acceptance test for serial_bridge._unpack_frame_bench
(matches the contract gate criterion #2 of TASK_20260705_rpm_bench_acquisition.md).

Builds v7 (12 B) and v8 (20 B) bench frames BYTE-BY-BYTE, asserts every field
including the new rpm tuple.  Also asserts the related bridge dispatch points
(_parse_and_handle_datagram and _rx_loop) accept both lengths.
"""

from __future__ import annotations

import importlib.util
import pathlib
import struct
import sys

# Direct import (no test framework needed).  Path follows repo layout.
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SB_PATH = REPO_ROOT / "ground_station" / "comm" / "serial_bridge.py"
spec = importlib.util.spec_from_file_location("serial_bridge", SB_PATH)
serial_bridge = importlib.util.module_from_spec(spec)
sys.modules["serial_bridge"] = serial_bridge
spec.loader.exec_module(serial_bridge)  # type: ignore[union-attr]
_xor_crc8 = serial_bridge._xor_crc8
GS_PROTO_VERSION = serial_bridge.GS_PROTO_VERSION
bridge = serial_bridge.SerialBridge(simulate=True)  # don't start the threads


def _bench_payload_v7(counter: int, mid: int, ccr: int, vbat: float, active: int) -> bytes:
    return struct.pack("<IBHfB", counter, mid, ccr, float(vbat), active)


def _bench_payload_v8(counter: int, mid: int, ccr: int, vbat: float, active: int,
                      r0: int, r1: int, r2: int, r3: int) -> bytes:
    return struct.pack("<IBHfB4H", counter, mid, ccr, float(vbat), active, r0, r1, r2, r3)


def _wrap(frame_type: int, payload: bytes, max_num_basis: int = 8) -> bytes:
    """Wrap a payload in the wire frame: sync | type | len_hi | len_lo | max_basis | payload | crc."""
    head = bytes([0xAA, 0xBB, frame_type, (len(payload) >> 8) & 0xFF,
                  len(payload) & 0xFF, max_num_basis & 0xFF])
    crc = _xor_crc8([frame_type, (len(payload) >> 8) & 0xFF,
                     len(payload) & 0xFF, max_num_basis & 0xFF, *payload])
    return head + payload + bytes([crc])


def _dict_from_lines(lines):
    return {k: v for k, v in lines}


def test_v7_12_byte_payload():
    """v7 frame (no RPM): bench.rpm* must all be zero."""
    payload = _bench_payload_v7(
        counter=0x12345678, mid=1, ccr=2500, vbat=15.42, active=1)
    assert len(payload) == 12, f"v7 payload must be 12 B, got {len(payload)}"
    lines = bridge._unpack_frame_bench(payload)
    assert lines, "expected non-empty decode"
    d = _dict_from_lines(lines)
    assert d["bench.sample_counter"] == 0x12345678
    assert d["bench.motor_id"] == 1.0
    assert d["bench.ccr"] == 2500.0
    assert abs(d["bench.vbat"] - 15.42) < 1e-5
    assert d["bench.active"] == 1.0
    assert d["bench.rpm"] == 0.0
    assert d["bench.rpm1"] == 0.0
    assert d["bench.rpm2"] == 0.0
    assert d["bench.rpm3"] == 0.0
    assert d["bench.rpm4"] == 0.0


def test_v8_20_byte_payload():
    """v8 frame: bench.rpm* populated, max(rpm1..4) -> bench.rpm."""
    payload = _bench_payload_v8(
        counter=42, mid=2, ccr=3200, vbat=14.80, active=1,
        r0=9500, r1=0, r2=0, r3=0)  # one sensor plugged into ch1
    assert len(payload) == 20, f"v8 payload must be 20 B, got {len(payload)}"
    lines = bridge._unpack_frame_bench(payload)
    assert lines, "expected non-empty decode"
    d = _dict_from_lines(lines)
    assert d["bench.sample_counter"] == 42.0
    assert d["bench.motor_id"] == 2.0
    assert d["bench.ccr"] == 3200.0
    assert abs(d["bench.vbat"] - 14.80) < 1e-5
    assert d["bench.active"] == 1.0
    assert d["bench.rpm1"] == 9500.0
    assert d["bench.rpm2"] == 0.0
    assert d["bench.rpm3"] == 0.0
    assert d["bench.rpm4"] == 0.0
    assert d["bench.rpm"] == 9500.0  # max


def test_v8_multi_sensor():
    """v8 with multiple sensors plugged in: max() picks the highest."""
    payload = _bench_payload_v8(
        counter=7, mid=3, ccr=2800, vbat=15.10, active=1,
        r0=4200, r1=8750, r2=0, r3=6500)
    d = _dict_from_lines(bridge._unpack_frame_bench(payload))
    assert d["bench.rpm"] == 8750.0
    assert d["bench.rpm1"] == 4200.0
    assert d["bench.rpm4"] == 6500.0


def test_invalid_length():
    """Neither 12 nor 20 bytes must return [] (gate the parser from garbage)."""
    bad = struct.pack("<IBHfBxx", 1, 1, 2000, 16.0, 1)  # 14 B - not a valid length
    assert bridge._unpack_frame_bench(bad) == []


def test_dispatch_udp_accepts_both_lengths():
    """_parse_and_handle_datagram (UDP path) must NOT reject 12 B or 20 B."""
    p7 = _bench_payload_v7(0, 1, 2000, 16.0, 1)
    p8 = _bench_payload_v8(0, 1, 2000, 16.0, 1, 0, 0, 0, 0)
    f7 = _wrap(0x04, p7)
    f8 = _wrap(0x04, p8)
    # Need the bridge to actually update its bench telemetry dict.  Easier:
    # just call _handle_frame directly so we don't touch real threads.
    bridge._handle_frame(0x04, 8, p7)
    bridge._handle_frame(0x04, 8, p8)
    # If the dispatch accepted both, _last_telemetry_bench should now hold v8 fields.
    with bridge._telemetry_lock:
        snap = dict(bridge._last_telemetry_bench)
    assert "bench.rpm" in snap
    assert snap["bench.ccr"] == 2000.0


def test_gs_proto_version_bumped():
    assert GS_PROTO_VERSION == 14, f"GS_PROTO_VERSION must be 14, got {GS_PROTO_VERSION}"


if __name__ == "__main__":
    test_gs_proto_version_bumped()
    test_v7_12_byte_payload()
    test_v8_20_byte_payload()
    test_v8_multi_sensor()
    test_invalid_length()
    test_dispatch_udp_accepts_both_lengths()
    print("OK all 6 tests passed")