"""Just listen for any UDP packets on port 14550 to see what the FC sends back."""
from __future__ import annotations

import socket
import time


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(0.5)
    sock.bind(("0.0.0.0", 14550))
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    print("[probe] listening on 14551", flush=True)

    # Nudge first
    sock.sendto(b"\x00", ("192.168.4.1", 14550))
    print("[probe] sent nudge", flush=True)
    time.sleep(0.1)

    deadline = time.monotonic() + 5
    total_bytes = 0
    n_packets = 0
    while time.monotonic() < deadline:
        try:
            data, addr = sock.recvfrom(4096)
            n_packets += 1
            total_bytes += len(data)
            # Print first 20 bytes hex + length
            print(f"[probe] packet #{n_packets} from {addr} len={len(data)} "
                  f"hex={data[:20].hex()}", flush=True)
            if data[:2] == b"\xAA\xBB" and data[2] in (0x30, 0x31, 0x32):
                print(f"[probe] >>> TRANSACTION RESULT FRAME!", flush=True)
        except socket.timeout:
            pass

    print(f"[probe] DONE: {n_packets} packets, {total_bytes} bytes", flush=True)


if __name__ == "__main__":
    main()
