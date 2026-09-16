#!/usr/bin/env python3
"""
Minimal VOFA+ UDP test (2 channels).

VOFA+ 1.3.x on UDP typically does NOT create named FireWater channels from text;
use JustFloat (default) so you get I0 = sin, I1 = cos. In VOFA+, set protocol to JustFloat.

  python ground_station/comm/test_vofa.py --once              # one JustFloat packet + hex dump
  python ground_station/comm/test_vofa.py --once --mode firewater   # text packet (Rx log only)
  python ground_station/comm/test_vofa.py                     # loop at 10 Hz (JustFloat)
"""
from __future__ import annotations

import argparse
import math
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

JUSTFLOAT_TAIL = b"\x00\x00\x80\x7f"


def main() -> None:
    p = argparse.ArgumentParser(description="Minimal 2-channel VOFA+ UDP test.")
    p.add_argument("--host", default="127.0.0.1", help="UDP destination host")
    p.add_argument("--port", type=int, default=1347, help="UDP destination port")
    p.add_argument("--hz", type=float, default=10.0, help="Transmit rate when not --once")
    p.add_argument(
        "--mode",
        choices=("justfloat", "firewater"),
        default="justfloat",
        help="justfloat: binary float32 x2 + tail (I0,I1). firewater: text lines (often single I0 in VOFA 1.3 UDP).",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="Send one sample (t=0), print payload, then exit",
    )
    args = p.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dest = (args.host, args.port)
    t0 = time.monotonic()

    def packet_at(t: float) -> bytes:
        s = math.sin(t)
        c = math.cos(t)
        if args.mode == "justfloat":
            return struct.pack("<ff", s, c) + JUSTFLOAT_TAIL
        text = f"ch1: {s:.6f}\nch2: {c:.6f}\n\n"
        return text.encode("utf-8")

    if args.once:
        t = 0.0
        raw = packet_at(t)
        if args.mode == "justfloat":
            print("Mode: JustFloat (set VOFA+ protocol to JustFloat, not UTF-8 FireWater)")
            print("I0=sin(t), I1=cos(t); tail:", JUSTFLOAT_TAIL.hex())
            print("Raw bytes (len=%d):" % len(raw))
            print(" ", raw.hex())
            print(" floats:", struct.unpack("<ff", raw[:8]))
        else:
            print("Mode: FireWater text (may only show as I0 on VOFA+ 1.3 UDP)")
            print("UTF-8:", repr(raw.decode("utf-8")))
            print("hex:", raw.hex())
        sock.sendto(raw, dest)
        sock.close()
        return

    try:
        period = 1.0 / float(args.hz)
        while True:
            t = time.monotonic() - t0
            sock.sendto(packet_at(t), dest)
            time.sleep(period)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()
