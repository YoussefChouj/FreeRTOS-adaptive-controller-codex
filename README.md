# FreeRTOS 6-DOF Adaptive Controller — clean workspace

This is a clean copy of a UAV flight-controller project, stripped of all
Cursor / Claude agent orchestration. It contains only the firmware code
plus the four operational capabilities needed to work with the running
hardware.

The original full project (with all agents, rules, plans, sessions,
memory, and skill index) lives elsewhere. This copy is independent.

## What is in this project

| Path | What it is |
| --- | --- |
| `API/` `TASK/` `BSP/` `USER/` | STM32F4 firmware sources. Compiled by Keil ARMCC V5.06. `USER/` holds the Keil project (`.uvprojx`, `.uvoptx`). |
| `FreeRTOS/` `Global_file/` `stm32_lib/` | Vendor / FreeRTOS / HAL support headers and sources. |
| `OBJ/` | Build output (313 files). `OBJ/JX_FLY.axf` carries DWARF — needed for symbol-named reads. |
| `ground_station/livewatch/` | pyOCD-based probe access: read/write memory, registers, flash, breakpoints, watchpoints, RTT, SWO, GDB server, named-variable reads via DWARF. |
| `ground_station/comm/` | UDP/serial telemetry transport: `wifi_bridge`, frame decoders, subscribe protocol helpers. |
| `ground_station/flashtool/` | Keil/UV4 build orchestration + pyOCD flash helpers (`rebuild_and_flash.py`, `safe_flash.py`). |
| `docs/telemetry-protocol.md` | Wire-format reference for the subscribe protocol (frame types 0x08 / 0x09..0x0C, slot layout, command set). |
| `docs/glossary.md` | Domain terms. |
| `docs/skills/` | Reference docs for the four core capabilities (capture-multislot, micoair-connect, livewatch, probe). |
| `docs/dashboard-platform/` | Long-lived firmware/dashboard platform specification, ordered session briefs, state, and handoff reports. |

## The four core capabilities

These are the only agent-level workflows in this project. Everything else
was intentionally removed.

### 1. livewatch (probe)
Full pyOCD capability layer for the STM32F4 target. Read or write any
memory, flash, halt/step/resume/reset, breakpoints, watchpoints, RTT,
SWO trace, GDB server — by DWARF name or by address.

Reference: `docs/skills/livewatch.md`

```bash
# Always run first — catches stale ELF before it poisons any read
python -m ground_station.livewatch verify

python -m ground_station.livewatch read s_ekf.x[3] DroneStatus.ARM_Status
python -m ground_station.livewatch watch group:ekf --hz 20 --secs 30
python -m ground_station.livewatch log of_drift --secs 60

python -m ground_station.livewatch halt
python -m ground_station.livewatch peek 0x20000000 --size dword
python -m ground_station.livewatch flash-write OBJ/JX_FLY.axf
```

### 2. probe (subset of livewatch)
The `probes`, `probe-info`, and `ProbeSession` (Python API) commands.
Same module as livewatch.

```bash
python -m ground_station.livewatch probes
python -m ground_station.livewatch probe-info

python -c "from ground_station.livewatch import ProbeSession; \
  with ProbeSession() as p: print(p.info())"
```

### 3. micoair-connect (WiFi UDP)
Establish and validate a working MicoAir WiFi UDP connection to the
flight controller (`192.168.4.1` AP, PC joins as `192.168.4.2`, UDP
14550). Handles the nudge requirement, buffer sizing, and common
misconfiguration traps.

Reference: `docs/skills/micoair-connect.md`

```bash
python -m ground_station.livewatch stream_log --transport usart3 --seconds 30
python -m ground_station.comm.wifi_bridge
```

### 4. capture-multislot (CLI subscribe recorder)
Drive a multi-slot subscribe capture over WiFi from a shell (no GUI).
Mirrors the proven dashboard recorder: pre-clear stale FC subscription
state, subscribe each slot sequentially, decode 0x09 data frames, write
one CSV per slot.

Reference: `docs/skills/capture-multislot.md`

```bash
python -m ground_station.livewatch.capture_preset flight_comprehensive --secs 10
```

Preset definitions: `ground_station/livewatch/multi_slot_presets.yaml`
Manifest definitions: `ground_station/livewatch/manifests.yaml`

## Firmware build

```bash
# Keil uVision CLI build (from USER/ directory)
UV4 -b -t JX_FLY -j0 JX_FLY.uvprojx

# Then flash via flashtool
python -m ground_station.flashtool.rebuild_and_flash --force --yes

# Or one-shot:
python -m ground_station.flashtool.safe_flash
```

Conventions: C for Keil ARMCC V5.06. Declarations at block top, no VLAs,
no C99-only constructs. Prefer file-scope buffers over large locals.
Match surrounding style over personal preference.

## Hardware notes

- **Flashing**: SWD wireless debugger on UART5, WiFi module (MicoAir)
  on USART3. Both can be connected simultaneously.
- **Inner-PID axis mapping**: `gyrox` = roll loop, `gyroy` = pitch loop.
  A label may not match the loop it tunes — confirm at the source
  (`TASK/StabilizerTask.c`).
- **EKF** (`s_ekf`) is shadow mode. Do not wire EKF output into control
  paths without explicit approval.
- **Cold-boot == flash**. A successful firmware flash produces a cold-boot
  init by default. Only insist on physical power-cycle when the symptom
  is known to survive a flash.
- **Flash integrity is the operator's responsibility, not Keil's.** UV4
  silent link noop is a known failure mode (CLI reports `0 Error(s)`
  but does not rewrite the AXF). Verify with
  `python -m ground_station.livewatch verify` (0 mismatches) before
  trusting any DWARF-addressed read.

## What was deliberately removed

This copy has none of the following:

- Cursor / Claude rule files, agent definitions, command files, hooks.
- The dispatcher / planner / implementer / reviewer subagents and
  pipeline orchestration.
- Session archives, agent contracts, agent memory, agent scripts.
- The wiki, the knowledge graph, the CocoIndex database.
- The Streamlit web dashboard, the legacy GUI, the simulation harness.
- Quality lint configs, scratch directories, experiment logs.

If you find you need any of those, they live in the parent project. Do
not re-create them here.

## Repository layout

```
API/                firmware (algorithm/library)
TASK/               firmware (RTOS task entry points)
BSP/                firmware (board support package)
USER/               firmware (Keil project, main, ISR wrappers)
FreeRTOS/           FreeRTOS kernel sources
Global_file/        shared firmware headers / macros
stm32_lib/          STM32 HAL / CMSIS headers and sources
OBJ/                build output (313 files, ~92 MB)
ground_station/
  livewatch/        pyOCD probe, named reads, capture_preset
  comm/             wifi_bridge, frame decoders, subscribe helpers
  flashtool/        rebuild_and_flash, safe_flash, UV4 orchestration
docs/
  telemetry-protocol.md
  glossary.md
  skills/           reference docs for the 4 core capabilities
README.md           this file
```
