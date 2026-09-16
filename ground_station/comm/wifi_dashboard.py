"""One-command launcher: start the MicoAir WiFi bridge and the dashboard together.

Without this, every WiFi session needed two terminals: one for `wifi_bridge.py`
and one for `dashboard.py`. The bridge had to be running before the dashboard's
auto-detect (UDP ping to 127.0.0.1:1349) would resolve to it.

This script:
  1. Spawns `python -m ground_station.comm.wifi_bridge` as a child process.
  2. Polls 127.0.0.1:1349 with a JSON ping until the bridge answers.
  3. Spawns the dashboard in the foreground (Ctrl+C forwards to both).
  4. Tears down the bridge when the dashboard exits (clean or crash).

Usage
~~~~~
  # Default: dashboard with MicoAir WiFi telemetry
  python -m ground_station.comm.wifi_dashboard

  # Same, but with explicit WiFi host/port (rare; default is 192.168.4.1:14550)
  python -m ground_station.comm.wifi_dashboard --wifi-host 192.168.4.1 --wifi-port 14550

  # Direct-COM mode (no bridge at all — dashboard opens the serial port itself)
  python -m ground_station.comm.wifi_dashboard --source com --com-port COM6 --baud 115200

  # Simulate mode (no bridge; dashboard pings an already-running serial_bridge.py
  # for the simulate lane). Same as launching the dashboard directly.
  python -m ground_station.comm.wifi_dashboard --source sim
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional


# Match the dashboard's defaults; if these drift, the dashboard will still find
# the bridge (it's the bridge's `--cmd-port` that matters, not these labels).
DASHBOARD_CMD_HOST = "127.0.0.1"
DASHBOARD_CMD_PORT = 1349

# How long to wait for the bridge to come up before bailing.
BRIDGE_READY_TIMEOUT_S = 8.0
BRIDGE_READY_POLL_INTERVAL_S = 0.1


def _bridge_alive(host: str, port: int) -> bool:
    """Send the dashboard's UDP ping; return True if a bridge answers pong."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.sendto(b"ping", (host, port))
        data, _ = s.recvfrom(1024)
        s.close()
        return data == b"pong"
    except Exception:
        return False


def _wait_for_bridge_alive(proc: Optional[subprocess.Popen], settle_s: float) -> bool:
    """Return True if the bridge is still running after `settle_s` seconds.

    Used to detect a fast bind failure (orphan bridge holds the port; the
    new one crashes in ~1 s). If the bridge exits during the settle window,
    we bail before the longer ping wait so the operator sees the cause in
    the log and the launcher's error message.
    """
    if proc is None:
        return False
    deadline = time.monotonic() + settle_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        time.sleep(0.05)
    return proc.poll() is None


def _wait_for_bridge(host: str, port: int, timeout_s: float) -> bool:
    """Poll for the bridge until it answers, or timeout. Prints progress."""
    deadline = time.monotonic() + timeout_s
    last_print = 0.0
    while time.monotonic() < deadline:
        if _bridge_alive(host, port):
            print(f"[launcher] bridge is up at {host}:{port}.")
            return True
        now = time.monotonic()
        if now - last_print > 1.0:
            print(f"[launcher] waiting for bridge at {host}:{port} "
                  f"({int(deadline - now)}s left)...")
            last_print = now
        time.sleep(BRIDGE_READY_POLL_INTERVAL_S)
    return False


def _spawn_bridge(python: str, args: List[str], log_path: Path) -> subprocess.Popen:
    """Spawn `python -m ground_station.comm.wifi_bridge <args>`, return Popen.

    stdout/stderr are tee'd to the given log file so the operator can see what
    the bridge is doing after the dashboard takes over the foreground. The log
    is opened in line-buffered mode (`buffering=1`) and force-flushed at open
    so the bridge's startup messages appear immediately even if the bridge
    dies before the read loop gets a chance to drain.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # `buffering=1` is line-buffered; this only works on text mode. Open as
    # text with utf-8 so the bridge's print() statements stream out live.
    log_fh = open(log_path, "ab", buffering=0)
    log_fh.flush()
    cmd = [python, "-u", "-m", "ground_station.comm.wifi_bridge", *args]
    print(f"[launcher] starting bridge: {' '.join(cmd)}")
    print(f"[launcher] bridge output tee'd to {log_path}")
    return subprocess.Popen(
        cmd,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        # New process group on POSIX so Ctrl+C to the launcher doesn't kill
        # the dashboard before we can clean up the bridge. On Windows the
        # CREATE_NEW_PROCESS_GROUP flag below achieves the same effect.
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def _spawn_dashboard(python: str, extra: List[str]) -> subprocess.Popen:
    """Spawn the dashboard in the foreground."""
    cmd = [python, "-m", "ground_station.gui.dashboard", *extra]
    print(f"[launcher] starting dashboard: {' '.join(cmd)}")
    return subprocess.Popen(cmd)


def _terminate_bridge(proc: subprocess.Popen) -> None:
    """Best-effort bridge teardown. Idempotent."""
    if proc is None or proc.poll() is not None:
        return
    print("[launcher] stopping bridge...")
    try:
        proc.terminate()
        proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        print("[launcher] bridge did not exit on SIGTERM, killing...")
        try:
            proc.kill()
            proc.wait(timeout=2.0)
        except Exception:
            pass
    except Exception as ex:
        print(f"[launcher] bridge teardown error: {ex!r}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Launch the dashboard with a WiFi bridge or direct serial.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s                                 # default: WiFi\n"
            "  %(prog)s --source com --com-port COM6    # direct serial\n"
            "  %(prog)s --source sim                    # simulate-mode bridge\n"
        ),
    )
    ap.add_argument(
        "--source", choices=("wifi", "com", "sim"), default="wifi",
        help="Telemetry source. 'wifi' starts the MicoAir bridge; 'com' makes "
             "the dashboard open the serial port directly; 'sim' assumes an "
             "already-running serial_bridge.py (no child process spawned).",
    )
    ap.add_argument("--python", default=sys.executable,
                    help="Python interpreter to use for children (default: this one)")
    ap.add_argument("--wifi-host", default="192.168.4.1",
                    help="MicoAir module IP (default: 192.168.4.1)")
    ap.add_argument("--wifi-port", type=int, default=14550,
                    help="MicoAir module UDP port (default: 14550)")
    ap.add_argument("--cmd-port", type=int, default=DASHBOARD_CMD_PORT,
                    help=f"UDP cmd port (default: {DASHBOARD_CMD_PORT})")
    ap.add_argument("--telem-port", type=int, default=1350,
                    help="UDP telem mirror port (default: 1350)")
    ap.add_argument("--com-port", default=None,
                    help="Serial port for --source com (e.g. COM6). "
                         "If omitted, the dashboard reads its own config.")
    ap.add_argument("--baud", type=int, default=115200,
                    help="Baud rate for --source com (default: 115200)")
    ap.add_argument("--bridge-ready-timeout", type=float,
                    default=BRIDGE_READY_TIMEOUT_S,
                    help=f"How long to wait for the bridge to answer ping "
                         f"(default: {BRIDGE_READY_TIMEOUT_S}s)")
    ap.add_argument("--bridge-log", default=None,
                    help="Path for the bridge's stdout/stderr log "
                         "(default: logs/wifi_bridge.log under repo root)")
    args = ap.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    bridge_log = (
        Path(args.bridge_log) if args.bridge_log
        else repo_root / "logs" / "wifi_bridge.log"
    )

    bridge_proc: Optional[subprocess.Popen] = None
    exit_code = 0
    try:
        if args.source == "wifi":
            bridge_args = [
                "--wifi-host", args.wifi_host,
                "--wifi-port", str(args.wifi_port),
                "--cmd-port", str(args.cmd_port),
                "--telem-port", str(args.telem_port),
                "--no-auto-subscribe",
            ]
            bridge_proc = _spawn_bridge(args.python, bridge_args, bridge_log)
            # Probe the bridge: first wait briefly to catch a fast bind failure
            # (the bridge dies in ~1 s when 1349/14550 are already in use), then
            # poll for the ping. If the bridge is dead, bail out immediately
            # instead of waiting the full 8 s for the ping to time out.
            if not _wait_for_bridge_alive(bridge_proc, settle_s=0.2):
                print(
                    f"[launcher] bridge exited before answering ping. "
                    f"See {bridge_log} for the error message.",
                    file=sys.stderr,
                )
                _terminate_bridge(bridge_proc)
                return 1
            if not _wait_for_bridge(
                DASHBOARD_CMD_HOST, args.cmd_port, args.bridge_ready_timeout
            ):
                print(
                    f"[launcher] bridge did not answer at "
                    f"{DASHBOARD_CMD_HOST}:{args.cmd_port} within "
                    f"{args.bridge_ready_timeout}s. Check the WiFi module and "
                    f"see {bridge_log} for bridge-side errors.",
                    file=sys.stderr,
                )
                _terminate_bridge(bridge_proc)
                return 1
            dashboard_extra: List[str] = []
        elif args.source == "com":
            # No bridge. Dashboard opens the serial port itself.
            dashboard_extra = []
            if args.com_port:
                dashboard_extra += ["--serial-port", args.com_port]
            if args.baud:
                dashboard_extra += ["--baud", str(args.baud)]
        else:  # sim
            # No bridge. Dashboard pings an already-running serial_bridge.py.
            dashboard_extra = []

        dash_proc = _spawn_dashboard(args.python, dashboard_extra)
        try:
            exit_code = dash_proc.wait()
        except KeyboardInterrupt:
            print("\n[launcher] Ctrl+C, stopping dashboard...")
            try:
                dash_proc.terminate()
                dash_proc.wait(timeout=3.0)
            except Exception:
                try:
                    dash_proc.kill()
                except Exception:
                    pass
            exit_code = 130
    finally:
        _terminate_bridge(bridge_proc)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
