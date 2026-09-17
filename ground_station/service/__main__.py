"""Entry point: python -m ground_station.service

Starts the ground-station service (WiFi bridge + telemetry ingestion + command
gateway) and the dashboard HTTP shell in one process.

Usage:
    python -m ground_station.service
    python -m ground_station.service --port 8080
    python -m ground_station.service --wifi-host 192.168.4.1 --port 9000
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

# Ensure repo root is on sys.path so `from ground_station import ...` works
# regardless of where the user invokes from.
_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Ground-station service + dashboard shell.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--port", "-p", type=int, default=8081,
        help="Dashboard HTTP port (default: 8081)",
    )
    p.add_argument(
        "--wifi-host", default="192.168.4.1",
        help="MicoAir WiFi module IP (default: 192.168.4.1)",
    )
    p.add_argument(
        "--wifi-port", type=int, default=14550,
        help="MicoAir command UDP port on the WiFi module (default: 14550)",
    )
    p.add_argument(
        "--no-auto-subscribe", action="store_true",
        help="Skip the automatic boot-default subscribe request on start.",
    )
    return p


def main() -> None:
    parser = _build_argparser()
    args = parser.parse_args()

    # ---- Build the service stack ----
    from ground_station.comm.wifi_bridge import WifiBridge
    from ground_station.platform.shell import start_shell
    from ground_station.service.core import GroundStationService

    bridge = WifiBridge(
        wifi_host=args.wifi_host,
        wifi_port=args.wifi_port,
    )

    service = GroundStationService(bridge=bridge)

    # ---- Start session + bridge ----
    service.start(auto_subscribe=not args.no_auto_subscribe)

    print(
        f"[service] Ground-station service started\n"
        f"  WiFi:      {args.wifi_host}:{args.wifi_port}\n"
        f"  Dashboard: http://localhost:{args.port}\n"
        f"  Session:   {service.session_id}\n"
        f"\n  Press Ctrl+C to stop.\n",
        flush=True,
    )

    # ---- Graceful shutdown ----
    def _stop(signum, _frame):
        print("\n[service] Stopping...", flush=True)
        service.stop()
        bridge.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)

    # ---- Start the HTTP shell (blocks) ----
    # Keep main thread alive so daemon HTTPServer thread stays up.
    # Signal handler above handles Ctrl+C and calls sys.exit(0).
    try:
        api = start_shell(service, port=args.port)
        print(f"[service] Shell bound to http://localhost:{args.port}", flush=True)
        # Block indefinitely — signal handler will exit the process
        # time.sleep keeps the main thread alive so the daemon HTTPServer
        # thread (api.server.serve_forever) keeps running.
        while True:
            time.sleep(86400)
    except Exception as exc:
        print(f"[service] Shell failed: {exc}", flush=True)
        service.stop()
        bridge.stop()
        raise


if __name__ == "__main__":
    main()
