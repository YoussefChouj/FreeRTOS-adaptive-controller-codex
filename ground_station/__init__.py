"""FreeRTOS 6-DOF Adaptive Controller — ground station package.

Modules:
  livewatch  — pyOCD-based probe access (read/write memory, registers, flash,
               breakpoints, watchpoints, RTT, SWO, GDB server, named-variable
               reads via DWARF).
  comm       — UDP/serial telemetry transport (wifi_bridge, frame decoders,
               subscribe protocol helpers).
  flashtool  — Keil/UV4 build orchestration + pyOCD flash helpers.
"""
