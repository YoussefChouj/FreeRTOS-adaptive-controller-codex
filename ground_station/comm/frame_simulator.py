"""
Synthetic UART4 telemetry generator for offline testing.

Sends Frame A / Frame B packets matching `serial_bridge.py` / firmware layout
over UDP (one datagram per frame). Run `serial_bridge.py --simulate` to
receive and forward to VOFA+ FireWater.

  python -m ground_station.comm.frame_simulator
  python -m ground_station.comm.serial_bridge --simulate
"""

from __future__ import annotations

import sys, os

# Allow `python ground_station/comm/frame_simulator.py` from the repo root
# without requiring PYTHONPATH.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import argparse
import math
import random
import socket
import struct
import threading
import time
from typing import List

from ground_station.comm.serial_bridge import _xor_crc8, load_config, GS_PROTO_VERSION

SYNC = (0xAA, 0xBB)
FRAME_A = 0x01
FRAME_B = 0x02
FRAME_BENCH = 0x04

# Mutable state for Frame B path tail (Section 8): updated each Frame B packet in loop_b.
_sim_path_b_state = {"t_elapsed": 0.0, "theta": 0.0}

# Bench-frame RPM channels: sin-curve one channel alive, three parked at 0 (matches
# the one-sensor bench reality).
_sim_bench_state = {"counter": 0}


def _pack_telemetry_frame(frame_type: int, max_num_basis: int, payload: bytes) -> bytes:
    """Same wire format as TASK/send_data.c: 16-bit LEN, then payload + XOR CRC8."""
    plen = len(payload)
    buf = bytearray(
        [
            SYNC[0],
            SYNC[1],
            frame_type & 0xFF,
            (plen >> 8) & 0xFF,
            plen & 0xFF,
            max_num_basis & 0xFF,
        ]
    )
    buf.extend(payload)
    crc = _xor_crc8(
        [
            frame_type & 0xFF,
            (plen >> 8) & 0xFF,
            plen & 0xFF,
            max_num_basis & 0xFF,
            *payload,
        ]
    )
    buf.append(crc)
    return bytes(buf)


def build_frame_a(max_num_basis: int, t_s: float) -> bytes:
    """8 floats (sine, distinct Hz) + status bytes + proto_version; LEN = 41 (v13).

    v13 status (9 u8): arm, flymode, sbus_lost, twc_execute, twc_arrived,
    rc_authority, of_hold, estimator_ready, GS_PROTO_VERSION. Matches send_data.c
    Frame A and serial_bridge `_unpack_frame_a`.
    """
    freqs_hz = [0.31, 0.47, 0.59, 0.71, 0.83, 0.97, 1.09, 1.21]
    floats: List[float] = [math.sin(2.0 * math.pi * f * t_s) for f in freqs_hz]
    arm_u8 = 1 if int(t_s * 2) % 2 == 0 else 0
    flymode_u8 = 2
    sbus_lost_u8 = 0
    twc_execute_u8 = 0
    twc_arrived_u8 = 1 if int(t_s * 3) % 2 == 0 else 0
    rc_authority_u8 = 1
    of_hold_u8 = 0
    estimator_ready_u8 = 1
    payload = struct.pack(
        "<8fBBBBBBBBB",
        *floats,
        arm_u8, flymode_u8, sbus_lost_u8, twc_execute_u8, twc_arrived_u8,
        rc_authority_u8, of_hold_u8, estimator_ready_u8,
        GS_PROTO_VERSION,
    )
    return _pack_telemetry_frame(FRAME_A, max_num_basis, payload)


def build_frame_b(
    max_num_basis: int,
    t_s: float,
    theta: List[List[float]],
    u_nom_xm_phase: float,
) -> bytes:
    """
    MRAC: 4 axes * (N theta + u_nom + xm) + 12 PID * (FB, Des, U).
    Theta: caller-owned random-walk state.
    u_nom / xm: sines. PID: sines with distinct phases.
    """
    floats: List[float] = []
    for ax in range(4):
        floats.extend(theta[ax])
        u_nom = 0.15 * math.sin(2.0 * math.pi * (0.15 + ax * 0.07) * t_s + u_nom_xm_phase)
        xm = 0.12 * math.sin(2.0 * math.pi * (0.12 + ax * 0.05) * t_s + u_nom_xm_phase * 1.3)
        floats.append(u_nom)
        floats.append(xm)

    # 12 loops * 3 (FB, Des, U); sine waves
    for k in range(36):
        floats.append(
            math.sin(2.0 * math.pi * (0.08 + k * 0.031) * t_s + 0.17 * k)
        )

    total = 4 * (max_num_basis + 2) + 36
    if len(floats) != total:
        raise RuntimeError(f"Frame B float count {len(floats)} != {total}")

    main = struct.pack("<" + "f" * total, *floats)
    # Path tail (send_data.c Frame B): matches firmware before CRC.
    st = _sim_path_b_state
    st["t_elapsed"] += 0.02
    st["theta"] += 0.1
    active_path_mode = int((t_s % 25.0) // 5.0) % 5  # 0..4, each mode ~5 s (incl. figure-8)
    twc_x, twc_y, twc_z = 1.0, 0.5, 0.7
    twc_arrived = 1 if st["t_elapsed"] > 10.0 else 0
    vbat = 16.0 + 0.4 * math.sin(0.05 * t_s)  # slow battery sag simulation
    tail = struct.pack(
        "<BfffffBf",
        active_path_mode,
        twc_x,
        twc_y,
        twc_z,
        float(st["t_elapsed"]),
        float(st["theta"]),
        twc_arrived,
        float(vbat),
    )
    payload = main + tail
    return _pack_telemetry_frame(FRAME_B, max_num_basis, payload)


def _step_theta(theta: List[List[float]], step_scale: float = 0.04) -> None:
    for ax in range(4):
        for i in range(len(theta[ax])):
            theta[ax][i] += random.uniform(-step_scale, step_scale)
            theta[ax][i] = max(-3.0, min(3.0, theta[ax][i]))


def build_frame_bench(t_s: float) -> bytes:
    """FRAME 0x04 v8 (20 B): u32 sample_counter | u8 motor_id | u16 commanded_ccr |
    f real_voltage | u8 active | {u16 rpm0..3}. Sin-curve one live RPM channel,
    three parked at 0 — matches the one-sensor bench reality described in ADR-0010."""
    counter = _sim_bench_state["counter"]
    _sim_bench_state["counter"] = (counter + 1) & 0xFFFFFFFF
    motor_id = 1
    # CCR step across a sweep: 2000 -> 4000 over 10 s.
    ccr = int(2000.0 + (math.sin(0.5 * t_s) * 0.5 + 0.5) * 2000.0)
    ccr = max(2000, min(4000, ccr))
    vbat = 16.0 + 0.4 * math.sin(0.05 * t_s)
    active = 1
    # Channel 0 has a live sensor: rpm roughly tracks CCR/4000 * 10000.
    rpm0 = int((ccr - 2000) / 2000.0 * 10000.0)
    rpm1 = 0
    rpm2 = 0
    rpm3 = 0
    payload = struct.pack(
        "<IBHfB4H",
        counter,
        motor_id & 0xFF,
        ccr & 0xFFFF,
        float(vbat),
        active & 0xFF,
        rpm0 & 0xFFFF,
        rpm1,
        rpm2,
        rpm3,
    )
    return _pack_telemetry_frame(FRAME_BENCH, 8, payload)


def main() -> None:
    cfg = load_config()
    default_host = str(cfg.get("vofa_host", "127.0.0.1"))
    default_telemetry_port = int(cfg.get("simulate_udp_port", 50007))
    vofa_a = int(cfg.get("vofa_port_a", cfg.get("vofa_port", 1347)))
    vofa_b = int(cfg.get("vofa_port_b", 1348))

    p = argparse.ArgumentParser(description="Synthetic telemetry to UDP (for serial_bridge --simulate).")
    p.add_argument("--host", default=default_host, help="Destination host (default from config vofa_host)")
    p.add_argument(
        "--port",
        type=int,
        default=default_telemetry_port,
        help="Telemetry UDP port for serial_bridge --simulate (default simulate_udp_port from config)",
    )
    p.add_argument("--max-num-basis", type=int, default=6, help="MAX_NUM_BASIS (default 6)")
    args = p.parse_args()
    print(
        f"Simulator: Frame A @ 100Hz, Frame B @ 20Hz, Frame Bench (0x04 v8) @ 50Hz -> {args.host}:{args.port} (raw telemetry for serial_bridge). "
        f"MAX_NUM_BASIS={args.max_num_basis}",
        flush=True,
    )
    print(
        f"VOFA+ JustFloat (via serial_bridge): Frame A -> {args.host}:{vofa_a}, Frame B -> {args.host}:{vofa_b}",
        flush=True,
    )
    print("Ctrl+C to stop.", flush=True)
    ev = threading.Event()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dest = (args.host, args.port)
    n = args.max_num_basis
    _sim_path_b_state["t_elapsed"] = 0.0
    _sim_path_b_state["theta"] = 0.0
    theta = [[0.0] * n for _ in range(4)]
    t0 = time.monotonic()
    ph = 0.0

    def loop_a() -> None:
        while not ev.is_set():
            t_s = time.monotonic() - t0
            pkt = build_frame_a(n, t_s)
            sock.sendto(pkt, dest)
            time.sleep(0.01)

    def loop_b() -> None:
        nonlocal ph
        while not ev.is_set():
            t_s = time.monotonic() - t0
            _step_theta(theta)
            pkt = build_frame_b(n, t_s, theta, ph)
            ph += 0.02
            sock.sendto(pkt, dest)
            time.sleep(0.05)

    def loop_bench() -> None:
        while not ev.is_set():
            t_s = time.monotonic() - t0
            pkt = build_frame_bench(t_s)
            sock.sendto(pkt, dest)
            time.sleep(0.02)

    th_a = threading.Thread(target=loop_a, daemon=True)
    th_b = threading.Thread(target=loop_b, daemon=True)
    th_bench = threading.Thread(target=loop_bench, daemon=True)
    th_a.start()
    th_b.start()
    th_bench.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        ev.set()
        th_a.join(timeout=1.0)
        th_b.join(timeout=1.0)
        th_bench.join(timeout=1.0)
        sock.close()


if __name__ == "__main__":
    main()
