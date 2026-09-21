"""Minimal WifiBridge transaction test - send one command, wait, see result."""
from __future__ import annotations

import sys
import time

from ground_station.comm.wifi_bridge import WifiBridge


def main():
    bridge = WifiBridge(wifi_host="192.168.4.1", wifi_port=14550)
    bridge.start()
    print("[probe] bridge started", flush=True)

    # Send one command
    txid = bridge.send_transaction(0x0F, 101, 0.0)
    print(f"[probe] sent CMD 0x0F idx=101 (MIXED) txid={txid}", flush=True)

    # Poll for up to 5 seconds
    deadline = time.monotonic() + 5.0
    result = None
    polls = 0
    while time.monotonic() < deadline:
        result = bridge.poll_transaction_result(0.1)
        polls += 1
        if result is not None:
            print(f"[probe] got result after {polls} polls: {result}", flush=True)
            break
        if polls % 10 == 0:
            print(f"[probe] still waiting... ({polls} polls so far)", flush=True)

    if result is None:
        print(f"[probe] TIMEOUT after {polls} polls", flush=True)
        # Check if the FC received the command
        bridge.stop()
        return 1

    bridge.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
