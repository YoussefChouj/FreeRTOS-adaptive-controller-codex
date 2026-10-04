"""Comprehensive command pipeline test using WifiBridge directly.

Bypasses the dashboard HTTP layer; uses the same WifiBridge.send_transaction
and poll_transaction_result pair the dashboard uses. Tests all command IDs
sequentially and prints the results.
"""
from __future__ import annotations

import sys
import time

from ground_station.comm.wifi_bridge import WifiBridge


# Each command: (cmd_id, idx, value, description, expected_reason)
COMMANDS = [
    (0x1E, 0, 0.0, "OF bias mode -> FIXED (boot default)",   "APPLIED"),
    (0x1E, 0, 1.0, "OF bias mode -> EMA",                    "APPLIED"),
    (0x1E, 0, 2.0, "OF bias mode -> EKF",                    "APPLIED"),
    (0x1E, 1, 1.0, "EMA freeze ON",                          "APPLIED"),
    (0x1E, 1, 0.0, "EMA freeze OFF",                         "APPLIED"),
    (0x1E, 0, 0.0, "OF bias mode -> FIXED (restore)",        "APPLIED"),

    (0x0F, 100, 0.0, "Telemetry mode -> LEGACY",              "APPLIED"),
    (0x0F, 101, 0.0, "Telemetry mode -> MIXED",               "APPLIED"),
    (0x0F, 102, 0.0, "Telemetry mode -> SUBSCRIBE_ONLY",      "APPLIED"),
    (0x0F, 101, 0.0, "Telemetry mode -> MIXED (restore)",     "APPLIED"),

    (0x10, 0, 0.0, "Reset world origin (OF earth_x/y=0)",     "APPLIED"),

    (0x07, 0, 1.0, "Bench mode enable (prop-wash guard)",    "APPLIED"),
    (0x07, 0, 0.0, "Bench mode disable",                      "APPLIED"),

    (0x09, 0, 5.0, "Max horizontal speed = 5 m/s",           "APPLIED"),
    (0x09, 0, 3.0, "Max horizontal speed = 3 m/s (restore)", "APPLIED"),

    (0x0D, 0, 0.0, "Abort all paths (no-op when no path)",   "APPLIED"),
]


def main():
    bridge = WifiBridge(wifi_host="192.168.4.1", wifi_port=14550)
    bridge.start()
    print("[probe] WifiBridge started", flush=True)
    # Allow time for connect handshake
    time.sleep(0.5)

    failed = 0
    for cmd_id, idx, value, desc, expected_reason in COMMANDS:
        try:
            txid = bridge.send_transaction(cmd_id, idx, value)
        except Exception as exc:
            print(f"[FAIL] {desc}: send_transaction raised {exc}")
            failed += 1
            continue
        # Poll for the result
        deadline = time.monotonic() + 2.0
        result = None
        while time.monotonic() < deadline:
            result = bridge.poll_transaction_result(0.05)
            if result is not None and result.transaction_id == txid:
                break
        if result is None:
            print(f"[TIMEOUT] {desc} (txid={txid}, cmd=0x{cmd_id:02X} idx={idx} val={value})")
            failed += 1
            continue
        ok = "✓" if result.outcome.name in ("APPLIED", "ACK") else "✗"
        print(f"[{ok}] {desc:50s} cmd=0x{cmd_id:02X} idx={idx:3d} val={value:5.1f} -> "
              f"txid={txid:5d} outcome={result.outcome.name:8s} reason={result.reason.name:8s} "
              f"detail={result.detail!r}")
        if result.outcome.name not in ("APPLIED", "ACK"):
            failed += 1
        time.sleep(0.05)  # small gap between commands

    bridge.stop()
    print(f"\n[probe] DONE: {len(COMMANDS) - failed}/{len(COMMANDS)} commands applied successfully")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
