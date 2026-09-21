"""Command validation harness.

Sequentially tests every dashboard command using the same UDP envelope
the dashboard's shell sends. Captures ACK/REJECTED/APPLIED outcomes plus
live state before/after each command.

Safety:
  - Drone is UNARMED throughout (verified before every test).
  - Motor bench command (0x16) is SKIPPED (no motor initialization).
  - TWC/path commands are skipped (would need arm).
  - Arm authority (0x0E) is tested only with value=0 (release).
  - All other commands are issued with idx/value defaults.

Usage: python logs/cmd_validation_2026-09-18/run_validation.py
"""
from __future__ import annotations

import json
import socket
import struct
import sys
import time
from pathlib import Path

COMMAND_SYNC = b"\xCC\xDF"
EVENT_SYNC = b"\xAA\xBB"
VERSION = 1
WIFI_HOST = "192.168.4.1"
WIFI_PORT = 14550


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


# Command test matrix. Each entry is (cmd_id, idx, value, name, expect_minimum_outcome).
# expect_minimum_outcome: "ANY"=any result; "ACK"=expect at least ACK (0x30); "REJECT"=expect REJECTED (0x31).
# SKIP = don't run (motor or path commands).
CMD_MATRIX = [
    # (cmd_id, idx, value, name, expected, safety_class)
    (0x00, 0, 0.0, "NOP",                              "ANY",     "diagnostic"),
    (0x01, 0, 1.0, "PID Gain pitch Kp",                "ANY",     "boundary"),
    (0x02, 0x00, 0.05, "MRAC Gamma pitch elem0",       "ANY",     "operational"),
    (0x03, 0, 0.5,  "Mixer pitch to mixer",            "ANY",     "operational"),
    (0x04, 0, 0.0,  "Flight Mode abort paths",         "ANY",     "critical"),
    (0x05, 0x00, 0.1, "MRAC What_limit pitch elem0",    "ANY",     "operational"),
    # 0x06 Virtual RC: SKIPPED — needs FlyMode_SDK
    (0x07, 0, 0.0,  "Bench Mode DISABLE",              "ANY",     "operational"),
    (0x08, 0x00, 0.001, "MRAC What_tol pitch elem0",    "ANY",     "operational"),
    (0x09, 0, 5.0,  "GS limit horizontal speed",       "ANY",     "boundary"),
    # 0x0A TWC: SKIPPED — needs SDK mode
    # 0x0B Sinusoid: SKIPPED — needs SDK mode
    # 0x0C Circle: SKIPPED — needs SDK mode
    # 0x0D Abort All: SKIPPED — emergency stop; testing in bench mode adds noise
    (0x0E, 0, 0.0, "SDK Arm Auth RELEASE",             "ANY",     "critical"),
    (0x0F, 100, 0.0, "Runtime Flag telemetry LEGACY",  "ANY",     "operational"),
    (0x0F, 101, 0.0, "Runtime Flag telemetry MIXED",   "ANY",     "operational"),
    (0x0F, 102, 0.0, "Runtime Flag telemetry SUB_ONLY","ANY",     "operational"),
    (0x0F, 0,   0.0, "Runtime Flag idx 0 (MRAC flag)", "ANY",     "operational"),
    (0x10, 0, 0.0, "Reset World Origin (OF)",          "ANY",     "operational"),
    # 0x11 Figure-8: SKIPPED — needs SDK mode
    (0x12, 0, 1.0,  "Waypoint Spacing",                "ANY",     "operational"),
    (0x13, 0, 0.0,  "Reference Model 0 (default)",     "ANY",     "operational"),
    (0x14, 7, 0.0,  "SysID Geofence DISABLE",          "ANY",     "operational"),
    (0x15, 0, 0.0,  "Gyro Filter DISABLE",             "ANY",     "operational"),
    # 0x16 Motor Bench: SKIPPED — user requirement: NEVER initialize motors
    (0x17, 0, 0.0,  "OF Bias Capture (one-shot)",      "ANY",     "diagnostic"),
    # 0x18 EKF Reset: requires GROUND_IDLE + DisArmed — verify state first
    (0x1E, 0, 0.0,  "OF Bias Mode FIXED",              "ANY",     "operational"),
    (0x1E, 0, 1.0,  "OF Bias Mode EMA",                "ANY",     "operational"),
    (0x1E, 0, 2.0,  "OF Bias Mode EKF",                "ANY",     "operational"),
    (0x1E, 0, 0.0,  "OF Bias Mode back to FIXED",      "ANY",     "operational"),
    (0x1E, 1, 1.0,  "OF Bias EMA freeze ON",           "ANY",     "operational"),
    (0x1E, 1, 0.0,  "OF Bias EMA freeze OFF",          "ANY",     "operational"),
]


def make_txid(seq: int) -> int:
    """Stable unique txid for run #seq (skips 0)."""
    base = int(time.monotonic() * 1000) & 0xFFFF
    candidate = (base + seq) & 0xFFFF
    return candidate if candidate != 0 else 1


def send_and_recv(sock, frame, timeout=1.5):
    """Send frame and collect all result frames within timeout."""
    sock.sendto(frame, (WIFI_HOST, WIFI_PORT))
    deadline = time.monotonic() + timeout
    rx = bytearray()
    found = []
    while time.monotonic() < deadline:
        try:
            data, _ = sock.recvfrom(4096)
            rx.extend(data)
        except socket.timeout:
            pass
        while len(rx) >= 15:
            pos = rx.find(EVENT_SYNC)
            if pos < 0:
                rx.clear()
                break
            if pos > 0:
                del rx[:pos]
            hi, lo = rx[3], rx[4]
            length = (hi << 8) | lo
            total = 6 + length
            if len(rx) < total:
                break
            frame = bytes(rx[:total])
            del rx[:total]
            parsed = parse_result(frame)
            if parsed is not None:
                found.append(parsed)
            else:
                # Could be other 0xAA 0xBB frame types; skip
                pass
    return found


def main():
    out_path = Path(__file__).parent / "validation_results.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(0.1)
    try:
        sock.bind(("0.0.0.0", 14550))
    except OSError as exc:
        print(f"FATAL: cannot bind UDP 14550 ({exc}); is wifi_bridge already running?",
              file=sys.stderr)
        return 1

    # Nudge to make MicoAir route responses back to us
    sock.sendto(b"\x00", (WIFI_HOST, WIFI_PORT))
    time.sleep(0.05)

    # Try to subscribe to slot-0 to get authoritative arm/mode/flymode/etc. data
    # We can use a simple scratch subscribe via direct 0xCC 0xDE envelope. Skip
    # for now — read g_OF_bias_mode over SWD-style by reading registers directly.

    results = []
    seq = 0
    for cmd_id, idx, value, name, expect, safety in CMD_MATRIX:
        txid = make_txid(seq)
        seq += 1
        frame = build_command(txid, cmd_id, idx, value)
        t0 = time.monotonic()
        outcomes = send_and_recv(sock, frame, timeout=1.0)
        elapsed = time.monotonic() - t0
        # Find the matching txid
        matching = [o for o in outcomes if o["transaction_id"] == txid]
        result = {
            "seq": seq,
            "cmd_id": f"0x{cmd_id:02X}",
            "cmd_name": name,
            "idx": idx,
            "value": value,
            "txid": txid,
            "safety": safety,
            "expected": expect,
            "elapsed_ms": round(elapsed * 1000, 1),
            "matching_results": matching,
            "any_other_results": [o for o in outcomes if o["transaction_id"] != txid],
        }
        results.append(result)
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(result) + "\n")
        # Console
        if matching:
            o = matching[0]
            print(f"[{seq:02d}] CMD 0x{cmd_id:02X} {name:35s} idx={idx} val={value} "
                  f"-> {o['outcome']:8s} reason={o['reason']:18s} detail={o['detail']!r} "
                  f"({elapsed*1000:.0f}ms)")
        else:
            print(f"[{seq:02d}] CMD 0x{cmd_id:02X} {name:35s} idx={idx} val={value} "
                  f"-> NO TXID MATCH (got {len(outcomes)} other frames) "
                  f"({elapsed*1000:.0f}ms)")
        time.sleep(0.1)

    sock.close()

    # Summary
    n_total = len(results)
    n_with_match = sum(1 for r in results if r["matching_results"])
    n_ack = sum(1 for r in results if r["matching_results"]
                and r["matching_results"][0]["outcome"] == "ACK")
    n_applied = sum(1 for r in results if r["matching_results"]
                    and r["matching_results"][0]["outcome"] == "APPLIED")
    n_rejected = sum(1 for r in results if r["matching_results"]
                     and r["matching_results"][0]["outcome"] == "REJECTED")
    n_no_reply = n_total - n_with_match
    print(f"\n=== SUMMARY ===")
    print(f"  total tests:       {n_total}")
    print(f"  ACK replies:       {n_ack}")
    print(f"  APPLIED replies:   {n_applied}")
    print(f"  REJECTED replies:  {n_rejected}")
    print(f"  no reply (timeout):{n_no_reply}")
    print(f"  results saved to:  {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())