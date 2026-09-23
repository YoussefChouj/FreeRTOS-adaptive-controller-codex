#!/usr/bin/env python3
"""
Print exact UDP payload bytes for one Frame A burst for each VOFA mode
(same encoding as serial_bridge.SerialBridge). No network I/O.

With vofa_format=justfloat, Frame A is sent to vofa_port_a (default 1347) only;
Frame B uses vofa_port_b (default 1348). This script only shows Frame A layouts.

  python ground_station/comm/show_frame_a_vofa_bytes.py
"""
from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from ground_station.comm.serial_bridge import vofa_channel_name  # noqa: E402


def _sample_frame_a_lines() -> list[tuple[str, float]]:
    """Same keys/order as _unpack_frame_a; example floats for byte dump."""
    return [
        ("mrac.pitch.e", 0.254536),
        ("mrac.pitch.u_ad", -0.403456),
        ("mrac.roll.e", 0.217802),
        ("mrac.roll.u_ad", 0.728086),
        ("mrac.yaw.e", -0.13515),
        ("mrac.yaw.u_ad", 0.499376),
        ("mrac.z.e", -0.650269),
        ("mrac.z.u_ad", -0.933064),
        ("status.arm", 1.0),
        ("status.flymode", 2.0),
        ("status.sbus_lost", 0.0),
    ]


def main() -> None:
    lines = _sample_frame_a_lines()

    print("=== Frame A internal keys (11 channels) ===")
    for n, v in lines:
        print(f"  {n} -> {vofa_channel_name(n)}")

    # firewater_multiline: two UDP datagrams (payload + blank line packet)
    dat = "".join(f"{vofa_channel_name(n)}: {v:.6f}\n" for n, v in lines).encode("utf-8")
    blank = b"\n"
    print("\n=== firewater_multiline (legacy): TWO UDP packets ===")
    print("Packet 1 (data) UTF-8:\n", dat.decode("utf-8"), end="")
    print("Packet 1 raw bytes (hex):", dat.hex())
    print("Packet 1 length:", len(dat))
    print("Packet 2 (time-axis blank) hex:", blank.hex(), "len:", len(blank))

    # firewater_single_line (default): one line per channel + blank line, ONE UDP packet
    payload_sl = ""
    for n, v in lines:
        payload_sl += f"{vofa_channel_name(n)}: {v:.6f}\n"
    payload_sl += "\n"
    sb = payload_sl.encode("utf-8")
    print("\n=== firewater_single_line: ONE UDP packet ===")
    print("UTF-8:\n", payload_sl, end="")
    print("Raw bytes (hex):", sb.hex())
    print("Length:", len(sb))

    # header_csv: first frame = header line + first data line (two packets first time)
    names = [vofa_channel_name(n) for n, _ in lines]
    hdr = "!" + ",".join(names) + "\n"
    body = ",".join(f"{v:.6f}" for _, v in lines) + "\n"
    print("\n=== firewater_header_csv (first emission: header + data) ===")
    print("Header UTF-8:\n", hdr, end="")
    print("Header hex:", hdr.encode("utf-8").hex())
    print("Data UTF-8:\n", body, end="")
    print("Data hex:", body.encode("utf-8").hex())

    # justfloat (destination: vofa_port_a for Frame A in serial_bridge)
    vals = [v for _, v in lines]
    jf = struct.pack("<" + "f" * len(vals), *vals) + b"\x00\x00\x80\x7f"
    print("\n=== justfloat: ONE UDP packet (vofa_port_a / Frame A) ===")
    print("Float count:", len(vals), "+ 4 tail bytes")
    print("Total length:", len(jf))
    print("Tail (must be 00 00 80 7F):", jf[-4:].hex())
    print("Full hex (first 48 bytes + ...):", jf[:48].hex(), "...", jf[-8:].hex())


if __name__ == "__main__":
    main()
