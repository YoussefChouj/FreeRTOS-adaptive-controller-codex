# Telemetry Testing Harness — Summary

Created: 2026-08-27  
Purpose: Validate subscribe telemetry independently from dashboard complexity

## What it does

The harness captures, replays, and validates the flexible subscribe telemetry system:
- Captures raw UDP frames from FC (any frame type)
- Decodes subscribe frames (0x09-0x0C) with slot/seq/timestamp/values
- Validates frame structure against manifests
- Tests uplink commands independently

## Key findings from first capture

**Captured:** 71 frames over 10s from live FC
- **70 × 0x09** (slot 0 subscribe data): 46 variables, 196 bytes/frame
- **1 × 0x01** (legacy ARM/FlyMode): embedded mid-stream

**Validation result:**
- `_base` manifest expects **12 variables** in slot 0
- FC is sending **46 variables** in slot 0
- **Root cause:** Firmware boot-default sends **entire structs** as 4-byte chunks:
  - `imu_data` (whole `_imu_st` struct)
  - `DroneStatus` (whole `DroneStatusTypeDef` struct)
  - `system_monitor` (whole `SYSTEM_MONITOR` struct)
  - `UA3RxFrameCnt`, `UA3TxFrames` (2 scalars)
- The `_base` manifest lists **12 named fields** from these structs, but firmware sends **all struct members**

**Implication:** Dashboard needs the **0x08 schema frame** to map the 46 float positions to variable names. The firmware's `Subscribe_BootDefault()` sets this up, but the host must request the schema via `0x21` to get the mapping.

## Available manifests

1. `_base` — 12 vars (IMU + status), 10 Hz
2. `mrac-weights` — slot 1 (200 Hz) + slot 2 (50 Hz)
3. `pid-autotune` — PID feedback + gains
4. `sysid-sweep` — system ID sweep data
5. `counters-health` — link counters, CPU health

## Usage

```bash
# Capture 10s of frames
python -m ground_station.comm.tests.test_telemetry_harness capture --duration 10

# Replay and analyze
python -m ground_station.comm.tests.test_telemetry_harness replay --file frames_*.bin

# Test specific frame type
python -m ground_station.comm.tests.test_telemetry_harness test-frame 0x09 --file frames_*.bin

# Validate against manifest
python -m ground_station.comm.tests.test_telemetry_harness validate-manifest --file frames_*.bin --manifest _base

# Test uplink command
python -m ground_station.comm.tests.test_telemetry_harness uplink

# Test subscribe workflow (schema + data)
python -m ground_station.comm.tests.test_subscribe_workflow --manifest _base --slot 0
```

## Next steps

**The subscribe system is working correctly:**
- FC boot-default streams slot 0 with full structs (46 floats)
- Manifests define **which named fields** to extract from those 46 values
- Dashboard should request `0x08` schema to get the mapping
- `wifi_bridge.py` has `_decode_stream_frame()` ready to decode

**Dashboard fix required:**
1. On startup, dashboard (or wifi_bridge) sends `0x21` request for slot 0
2. FC replies with `0x08` schema frame (variable names + positions)
3. Dashboard decodes `0x09` frames using schema
4. Maps `DroneStatus.ARM_Status` → position in 46-value array → display

**To validate the schema system:**
```bash
# Check if wifi_bridge receives and decodes slot 0 frames
python -m ground_station.comm.wifi_bridge --cmd-port 14550 --telem-port 1350

# In another terminal, check if dashboard receives decoded JSON
# (Dashboard expects {"a": {}, "b": {}, ...} on port 1350)
```

**Known constraint:**
- Boot-default sends **whole structs** (firmware optimization — single memcpy per range)
- Manifests select **named fields** from those structs
- This is intentional: `_base` lists 12 useful vars from the 46 transmitted
