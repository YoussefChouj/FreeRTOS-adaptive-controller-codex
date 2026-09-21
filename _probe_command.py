"""Send a command to the FC and wait for the transaction result.

Used by the live test workflow to validate every dashboard command
sequentially, the same way the dashboard HTTP API would.

Usage: python -m ground_station._probe_command <id> <idx> <value>

For commands that just need a query, pass idx=0 value=0 (e.g. CMD 0x07
bench mode toggle, CMD 0x10 reset world origin).
"""
from __future__ import annotations

import socket
import struct
import sys
import time

COMMAND_SYNC = b"\xCC\xDF"
EVENT_SYNC = b"\xAA\xBB"
VERSION = 1


def _xor(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
    return crc


def build_command(txid: int, cmd_id: int, idx: int, value: float, flags: int = 0) -> bytes:
    body = struct.pack("<BBHBBHf", VERSION, flags, txid, cmd_id, idx, 4, value)
    return COMMAND_SYNC + body + bytes((_xor(body),))


def parse_result(frame: bytes):
    if len(frame) < 15 or frame[:2] != EVENT_SYNC:
        return None
    frame_type = frame[2]
    if frame_type not in (0x30, 0x31, 0x32):
        return None
    body = frame[5:-1]
    if frame[-1] != _xor(frame[2:5] + body):
        return None
    hi, lo = frame[3], frame[4]
    length = (hi << 8) | lo
    if length != len(body):
        return None
    version, outcome, txid, cmd_id, idx, reason, detail_len = struct.unpack_from("<BBHBBBB", body)
    detail = body[8:8 + detail_len].decode("utf-8", errors="replace")
    outcome_name = {0x30: "ACK", 0x31: "REJECTED", 0x32: "APPLIED"}[frame_type]
    reason_name = {0: "NONE", 1: "BAD_VERSION", 2: "BAD_LENGTH", 3: "BAD_CRC",
                   4: "UNKNOWN_COMMAND", 5: "INVALID_ARGUMENT",
                   6: "SAFETY_INTERLOCK", 7: "DUPLICATE", 8: "QUEUE_FULL"}.get(reason, "?")
    return {
        "transaction_id": txid,
        "outcome": outcome_name,
        "command_id": cmd_id,
        "index": idx,
        "reason": reason_name,
        "detail": detail,
        "version": version,
    }


def main(argv):
    if len(argv) != 4:
        print("usage: _probe_command <cmd_id> <idx> <value>", file=sys.stderr)
        return 1
    cmd_id = int(argv[1], 0)
    idx = int(argv[2], 0)
    value = float(argv[3])

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(0.1)
    sock.bind(("0.0.0.0", 14550))
    # MicoAir needs the nudge to route responses back to us
    sock.sendto(b"\x00", ("192.168.4.1", 14550))
    time.sleep(0.05)

    # Use a random initial txid derived from monotonic time so successive
    # runs of this script don't collide with prior transactions still in the
    # firmware's idempotence cache. Wrap at uint16 like the wire protocol
    # expects (skip 0, which the firmware treats as "no txid").
    _PROBE_TXID_BASE = int(time.monotonic() * 1000) & 0xFFFF
    _PROBE_TXID_COUNTER = 0
    while _PROBE_TXID_COUNTER == 0:
        _PROBE_TXID_BASE = (_PROBE_TXID_BASE + 1) & 0xFFFF

    txid = (_PROBE_TXID_BASE + _PROBE_TXID_COUNTER) & 0xFFFF
    if txid == 0:
        txid = 1
    frame = build_command(txid, cmd_id, idx, value)
    sock.sendto(frame, ("192.168.4.1", 14550))
    print(f"[probe] sent CMD 0x{cmd_id:02X} idx={idx} val={value} txid={txid}")

    deadline = time.monotonic() + 1.5
    rx = bytearray()
    while time.monotonic() < deadline:
        try:
            data, _ = sock.recvfrom(4096)
            rx.extend(data)
        except socket.timeout:
            pass
        while True:
            # Try to find the next 0xAA 0xBB envelope
            if len(rx) < 15:
                break
            pos = rx.find(EVENT_SYNC)
            if pos < 0:
                rx.clear()
                break
            if pos > 0:
                del rx[:pos]
            # Parse the frame
            hi, lo = rx[3], rx[4]
            length = (hi << 8) | lo
            total = 6 + length
            if len(rx) < total:
                break
            frame = bytes(rx[:total])
            parsed = parse_result(frame)
            del rx[:total]
            if parsed and parsed["transaction_id"] == txid:
                print(f"[probe] result: {parsed}")
                return 0
            # Otherwise keep scanning
    print("[probe] TIMEOUT waiting for transaction result", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
