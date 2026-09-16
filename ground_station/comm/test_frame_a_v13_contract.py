"""
Regression test for the v13 Frame A telemetry contract (stale-telemetry bug).

Root cause: firmware bumped Frame A to a 41-byte payload (added of_hold +
estimator_ready) and appended a Frame C (0x06) inside the same DMA buffer, while
the ground station still hard-required a 39-byte Frame A. Every 100 Hz Frame A
was dropped -> ARM/FlyMode/SBUS + MRAC-tracking fields went stale.

This locks down the fixed contract:
  * _unpack_frame_a decodes BOTH 39 B (v10) and 41 B (v13) payloads.
  * the 41 B path exposes status.of_hold + status.estimator_ready.
  * both dispatch gates (_parse_and_handle_datagram + _rx_loop) accept 39 and 41.
  * a 41 B Frame A + a Frame C (0x06) + a Frame B stream decodes end-to-end
    (Frame C must not desync the parser) — the exact firmware wire layout.
"""

from __future__ import annotations

import importlib.util
import pathlib
import struct
import sys
import threading
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SB_PATH = REPO_ROOT / "ground_station" / "comm" / "serial_bridge.py"
spec = importlib.util.spec_from_file_location("serial_bridge", SB_PATH)
serial_bridge = importlib.util.module_from_spec(spec)
sys.modules["serial_bridge"] = serial_bridge
spec.loader.exec_module(serial_bridge)  # type: ignore[union-attr]

_xor_crc8 = serial_bridge._xor_crc8
GS_PROTO_VERSION = serial_bridge.GS_PROTO_VERSION
bridge = serial_bridge.SerialBridge(simulate=True)  # don't start the threads

MAX_NUM_BASIS = 6
SYNC = bytes([0xAA, 0xBB])


def crc16_xmodem(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= (byte << 8) & 0xFFFF
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc & 0xFFFF


def _frame_a_v10_payload() -> bytes:
    body = struct.pack("<8f", *[0.1 * i for i in range(8)])
    body += bytes([1, 2, 0, 0, 1, 1, GS_PROTO_VERSION])  # 7 status bytes
    assert len(body) == 39
    return body


def _frame_a_v13_payload(of_hold: int, est_ready: int) -> bytes:
    body = struct.pack("<8f", *[0.1 * i for i in range(8)])
    body += bytes([1, 2, 0, 0, 1, 1, of_hold, est_ready, GS_PROTO_VERSION])  # 9 status bytes
    assert len(body) == 41
    return body


def _wrap_crc8(frame_type: int, payload: bytes) -> bytes:
    hdr = bytes([frame_type, (len(payload) >> 8) & 0xFF, len(payload) & 0xFF, MAX_NUM_BASIS])
    return SYNC + hdr + payload + bytes([_xor_crc8([*hdr, *payload])])


def _frame_c() -> bytes:
    body = struct.pack("<9f", *range(1, 10)) + struct.pack("<4H", 100, 0, 0, 0) + struct.pack("<H", 7)
    body += b"\x00\x00\x00\x00"  # pad to declared LEN=50
    assert len(body) == 50
    hdr = bytes([0x06, 0x00, 50, MAX_NUM_BASIS])
    crc = crc16_xmodem(hdr + body)
    return SYNC + hdr + body + bytes([(crc >> 8) & 0xFF, crc & 0xFF])


def _frame_b() -> bytes:
    total_floats = 4 * (MAX_NUM_BASIS + 2) + 36
    main = struct.pack("<" + "f" * total_floats, *[0.5] * total_floats)
    tail = struct.pack("<BfffffBf", 0, 1.0, 0.5, 0.7, 0.0, 0.0, 0, 16.0)
    return _wrap_crc8(0x02, main + tail)


def test_unpack_frame_a_v10_39byte():
    out = dict(bridge._unpack_frame_a(MAX_NUM_BASIS, _frame_a_v10_payload()))
    assert out["status.arm"] == 1.0
    assert out["status.flymode"] == 2.0
    # v10 has no of_hold/estimator_ready fields but must still default them.
    assert out["status.of_hold"] == 0.0
    assert out["status.estimator_ready"] == 0.0


def test_unpack_frame_a_v13_41byte():
    out = dict(bridge._unpack_frame_a(MAX_NUM_BASIS, _frame_a_v13_payload(of_hold=1, est_ready=1)))
    assert out["status.arm"] == 1.0
    assert out["status.of_hold"] == 1.0
    assert out["status.estimator_ready"] == 1.0


def test_unpack_frame_a_rejects_wrong_length():
    assert bridge._unpack_frame_a(MAX_NUM_BASIS, b"\x00" * 40) == []


def test_dispatch_udp_accepts_39_and_41():
    bridge._parse_and_handle_datagram(_wrap_crc8(0x01, _frame_a_v10_payload()))
    with bridge._telemetry_lock:
        assert dict(bridge._last_telemetry_a)  # non-empty
    bridge._parse_and_handle_datagram(_wrap_crc8(0x01, _frame_a_v13_payload(1, 0)))
    with bridge._telemetry_lock:
        assert dict(bridge._last_telemetry_a)["status.of_hold"] == 1.0


def test_rx_loop_stream_a_then_c_then_b():
    """Byte-stream seam: 41 B Frame A + Frame C + Frame B — the firmware wire layout."""

    class _FakeSerial:
        def __init__(self, data: bytes):
            self._d = data
            self._i = 0

        def read(self, n: int) -> bytes:
            if self._i >= len(self._d):
                time.sleep(0.005)
                return b""
            c = self._d[self._i : self._i + n]
            self._i += len(c)
            return c

        def close(self):
            pass

        def cancel_read(self):
            pass

    stream = _wrap_crc8(0x01, _frame_a_v13_payload(1, 0)) + _frame_c() + _frame_b()
    br = serial_bridge.SerialBridge(serial_port="FAKE", baud_rate=115200)
    br._serial = _FakeSerial(stream)
    br._stop_event.clear()
    t = threading.Thread(target=br._rx_loop, daemon=True)
    t.start()
    time.sleep(0.5)
    br._stop_event.set()
    t.join(timeout=1.0)

    # Read the decoder's output directly, not get_telemetry_snapshot(): this test is
    # about framing, and the snapshot additionally applies a wall-clock staleness
    # guard. The 0.5 s sleep above is itself longer than Frame A's staleness window,
    # so going through the snapshot asserted on the guard rather than on the decode
    # and failed for a reason that had nothing to do with desync.
    with br._telemetry_lock:
        a = dict(br._last_telemetry_a)
        b = dict(br._last_telemetry_b)
    assert a, "Frame A must decode from the byte stream"
    assert a["status.of_hold"] == 1.0
    assert b, "Frame B must still decode after an interleaved Frame C (no desync)"


if __name__ == "__main__":
    test_unpack_frame_a_v10_39byte()
    test_unpack_frame_a_v13_41byte()
    test_unpack_frame_a_rejects_wrong_length()
    test_dispatch_udp_accepts_39_and_41()
    test_rx_loop_stream_a_then_c_then_b()
    print("OK all 5 tests passed")
