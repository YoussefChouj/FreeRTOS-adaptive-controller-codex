# Boot-Default Schema Analysis

## Problem Statement

Dashboard shows `ARM: ?` because it doesn't know which of the 46 floats in slot 0 is `DroneStatus.ARM_Status`.

## Root Cause

**Firmware boot-default** (`API/subscribe.c:919-1014`) sends 5 ranges:
1. **Range 0:** `imu_data` — entire `_imu_st` struct (all members as 4-byte chunks)
2. **Range 1:** `DroneStatus` — entire `DroneStatusTypeDef` struct
3. **Range 2:** `system_monitor` — entire `SYSTEM_MONITOR` struct
4. **Range 3:** `UA3RxFrameCnt` — 1 scalar
5. **Range 4:** `UA3TxFrames` — 1 scalar

**Total:** 46 floats (44 struct members + 2 scalars)

**The `_base` manifest** lists **12 named fields** from these structs, but the firmware sends **all struct members** for efficiency (single memcpy per range).

## The Schema System Design

1. **Firmware sends 0x08 schema frame** (one-time) when host requests via `0x21`
2. Schema contains: `[(address, size, count, name), ...]` for each range
3. **Host unpacks variable names** from schema
4. Host decodes `0x09` data frames using those names
5. Dashboard maps `"DroneStatus.ARM_Status"` → position → display value

## Why Dashboard Shows `ARM: ?`

Dashboard is **not requesting the schema** (`0x21` request) or **not decoding the schema reply** (`0x08` frame). It's receiving `0x09` data frames but doesn't know which float is which.

## Solution Path

**Option 1 — Request schema on startup (correct):**
1. Dashboard (or wifi_bridge) sends `0x21` request for slot 0 on startup
2. FC replies with `0x08` schema frame
3. wifi_bridge decodes schema → `{"slot0.imu_data.rol": 0, "slot0.imu_data.pit": 1, ...}`
4. Dashboard reads telemetry from wifi_bridge's JSON port (1350)
5. UI shows `ARM_Status` correctly

**Option 2 — Hardcode boot-default layout:**
1. Create a hardcoded decoder for slot 0's 46 floats
2. Positional mapping: `ARM_Status = values[X]` where X is known offset
3. Fragile — breaks if firmware structs change

**Option 3 — Query livewatch for struct layout:**
```bash
# Get exact field positions from ELF
python -m ground_station.livewatch fields _imu_st
python -m ground_station.livewatch fields DroneStatusTypeDef
python -m ground_station.livewatch fields SYSTEM_MONITOR

# Build position map: field_name → index in 46-float array
```

## Testing the Full Pipeline

```bash
# 1. Start wifi_bridge
python -m ground_station.comm.wifi_bridge --cmd-port 14550 --telem-port 1350

# 2. Check if it receives 0x09 frames and decodes them
#    (Should see decoded JSON on stdout)

# 3. Start dashboard
python -m ground_station.gui.dashboard

# 4. Check if dashboard receives decoded telemetry on port 1350
#    (ARM status should update)
```

## What the Testing Harness Proved

✓ FC is streaming slot 0 correctly (46 floats, 196 bytes/frame)  
✓ Subscribe system is working (0x09 frames arriving)  
✓ Frame structure is valid (seq, t_ms, values decode cleanly)  
✗ Schema not exchanged (no 0x08 frame observed)  
✗ Dashboard not decoding (doesn't know variable positions)

## Next Action

**Check if wifi_bridge forwards decoded JSON:**
```bash
# Start wifi_bridge and watch for decoded slot 0 output
python -m ground_station.comm.wifi_bridge --cmd-port 14550 --telem-port 1350 | findstr "slot0"
```

If wifi_bridge **does** decode and forward → dashboard JSON parsing issue  
If wifi_bridge **does not** decode → schema request missing
