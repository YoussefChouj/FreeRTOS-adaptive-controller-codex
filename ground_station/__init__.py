"""FreeRTOS 6-DOF Adaptive Controller — ground station package.

Modules:
  livewatch  — pyOCD-based probe access (read/write memory, registers, flash,
               breakpoints, watchpoints, RTT, SWO, GDB server, named-variable
               reads via DWARF).
  comm       — UDP/serial telemetry transport (wifi_bridge, frame decoders,
               subscribe protocol helpers).
  flashtool  — Keil/UV4 build orchestration + pyOCD flash helpers.
"""

import os as _os

# The laptop sets HTTP_PROXY to Clash (127.0.0.1:7897) with no NO_PROXY, and urllib/requests then send
# localhost calls through it: 8081 answered 502 to camera_truth, the tracker height poll and fly
# (2026-10-14 lab, nothing flown). Every `python -m ground_station.*` imports this package first.
for _key in ("NO_PROXY", "no_proxy"):
    _have = [h.strip() for h in _os.environ.get(_key, "").split(",") if h.strip()]
    _os.environ[_key] = ",".join(_have + [h for h in ("localhost", "127.0.0.1", "::1") if h not in _have])
del _os, _key, _have
