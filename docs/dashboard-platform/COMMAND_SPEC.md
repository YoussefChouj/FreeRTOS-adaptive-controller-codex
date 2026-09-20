# Command Specification

This document describes the command protocol, command IDs, index parameter semantics, value ranges, result codes, and safety restrictions for the UAV Ground Station platform.

## Table of contents

1. [Protocol overview](#protocol-overview)
2. [Command envelope](#command-envelope)
3. [Command reference](#command-reference)
4. [Result codes](#result-codes)
5. [Safety restrictions](#safety-restrictions)
6. [Usage examples](#usage-examples)

---

## Protocol overview

Commands use a **transactional protocol** with acknowledgement and result events:

```
Host                          Drone
  │                              │
  │──── 0xCC 0xDF (command) ────►│  ACK (queued)
  │◄─── 0x30 (ACK) ─────────────│
  │                              │
  │        [processing...]       │
  │                              │
  │◄─── 0x31 (REJECTED) ────────│  or
  │◄─── 0x32 (APPLIED) ─────────│
```

### Wire format

```
0xCC 0xDF version(1) flags(1) tx_id(2) cmd_id(1) index(1) value(4) crc8(1)
```

| Field | Size | Description |
|-------|------|-------------|
| Sync | 2 B | `0xCC 0xDF` |
| Version | 1 B | Protocol version (currently `0x01`) |
| Flags | 1 B | Reserved (set to `0`) |
| Transaction ID | 2 B | LE16, nonzero for idempotence |
| Command ID | 1 B | Command identifier (see below) |
| Index | 1 B | Command-specific index |
| Value | 4 B | LE float32 |
| CRC8 | 1 B | XOR checksum |

---

## Command envelope

### Legacy format (deprecated)

```
0xCC 0xDD cmd_id(1) index(1) value(4) crc8(1)
```

The legacy `0xCC 0xDD` format is deprecated; new implementations should use `0xCC 0xDF`.

### Transaction lifecycle

1. **Submission**: Host allocates a nonzero transaction ID and sends the command
2. **Acknowledgement**: Drone immediately replies with `0x30 ACK` when queued
3. **Processing**: Drone validates preconditions and applies the command
4. **Result**: Drone replies with `0x31 REJECTED` or `0x32 APPLIED`
5. **Idempotence**: Replaying a command with the same transaction ID returns `APPLIED/duplicate`

---

## Command reference

### PID Gain (0x01)

Adjust PID controller proportional, integral, or derivative gain.

| Index | Meaning | Value range | Unit |
|-------|---------|------------|------|
| `axis * 3 + 0` | Kp (proportional) | 0–200 | gain |
| `axis * 3 + 1` | Ki (integral) | 0–200 | gain |
| `axis * 3 + 2` | Kd (derivative) | 0–200 | gain |

Where `axis`: 0=pitch, 1=roll, 2=yaw, 3=altitude, 4=latlon, 5=?, 6=?

**Safety**: Requires SDK authority and disarmed state. Parameter is boundary-safe.

**Example**: Set pitch Kp to 1.5:
```javascript
api.submitCommand(0x01, 0, 1.5);  // axis=0, gain_type=0 (Kp)
```

---

### MRAC Gamma (0x02)

Adjust MRAC adaptive layer learning rate (gamma) element.

| Index | Meaning | Value range | Unit |
|-------|---------|------------|------|
| High nibble | Axis (0–3) | — | — |
| Low nibble | Basis index (< MAX_NUM_BASIS) | — | — |

**Safety**: Unsafe for unattended operation. Requires SDK authority.

---

### Mixer and Throttle Limits (0x03)

Configure mixer scaling and throttle limits.

| Index | Meaning | Value range | Unit |
|-------|---------|------------|------|
| 0–3 | Mixer index 0–3 | 0–1 | ratio |
| 4–7 | u_max per axis | 0–1 | ratio |
| 8 | Throttle minimum | 0–1 | ratio |
| 9 | Throttle maximum | 0–1 | ratio |

**Safety**: Unsafe. Requires disarmed state.

---

### Flight Mode / Abort (0x04)

Control flight FSM events and path abort.

| Index | Meaning |
|-------|---------|
| 0 | Abort/stop all paths |
| 1 | Recover SDK authority |

**Safety**: Critical. Changes flight state and control authority.

---

### MRAC What_limit (0x05)

Set MRAC adaptive law output limit.

| Index | Meaning | Value range |
|-------|---------|------------|
| Same as 0x02 | Axis + basis | ≥ 0 |

**Safety**: Unsafe.

---

### Virtual RC Injection (0x06)

Inject virtual RC stick values (SDK override).

| Index | Meaning | Value range |
|-------|---------|------------|
| 0–4 | Stick channel | clamped to [-1, 1] |

**Precondition**: SDK mode only.

**Safety**: Critical. Only allowed when `FlyMode` indicates SDK control.

---

### Bench Mode (0x07)

Enable motor bench mode for testing.

| Index | Meaning | Value range |
|-------|---------|------------|
| 0 | Enable bench mode (0/1) | 0 or 1 |

**Safety**: Unsafe. Requires explicit bench state and disarmed constraints.

---

### MRAC What_tol (0x08)

Set MRAC tolerance threshold.

| Index | Meaning | Value range |
|-------|---------|------------|
| Same as 0x02 | Axis + basis | ≥ 0 |

**Safety**: Unsafe.

---

### Ground Station Safety Limits (0x09)

Set host-side safety limits for rate commands.

| Index | Meaning | Value range | Default |
|-------|---------|------------|---------|
| 0 | Max horizontal speed | 0.05–20 m/s | — |
| 1 | Max vertical speed | 0.05–10 m/s | — |
| 2 | Max pitch angle | 3–60 deg | — |
| 3 | Max roll angle | 3–60 deg | — |

**Safety**: Boundary-safe. Can be adjusted live.

---

### TWC Target and Execute (0x0A)

Two-waypoint command (TWC) navigation.

| Index | Meaning | Notes |
|-------|---------|-------|
| 0–4 | TWC parameters | SDK only |

**Safety**: Unsafe. Requires SDK authority.

---

### Sinusoid Path (0x0B)

Configure and execute sinusoidal path.

| Index | Meaning | Notes |
|-------|---------|-------|
| 0–7 | Frequency, amplitude, phase, start/stop | SDK only |

**Safety**: Unsafe. SDK only.

---

### Circle Path (0x0C)

Configure and execute circular path.

| Index | Meaning | Notes |
|-------|---------|-------|
| 0–6 | Radius, center, start/stop | SDK only |

**Safety**: Unsafe. SDK only.

---

### Abort All (0x0D)

Emergency stop: abort paths, neutral sticks, dangerous stop.

| Index | Meaning |
|-------|---------|
| 0 | Abort (no-op on nonzero) |

**Safety**: Critical. No preconditions checked.

---

### SDK Arm Authority (0x0E)

Request or release SDK control authority.

| Index | Value | Meaning |
|-------|-------|---------|
| 0 | 0 | Release authority (RC regains control) |
| 0 | nonzero | Request authority |

**Verified behavior** (S5 gate):
- `submitCommand(0x0E, 0, 0)` → `ACK → APPLIED` on disarmed drone
- Authority is released; RC regains control

**Safety**: Critical. Controls flight authority.

---

### Runtime Flags / Telemetry Mode (0x0F)

Set runtime flags and telemetry mode.

| Index | Value | Meaning |
|-------|-------|---------|
| 0–12 | Various | MRAC flags |
| 100 | — | Legacy mode |
| 101 | — | Mixed mode |
| 102 | — | Subscribe-only mode |

**Safety**: Unsafe for mode/authority changes.

---

### Reset Optical Flow (0x10)

Reset optical flow and world origin.

| Index | Meaning |
|-------|---------|
| 0 | Reset (no-op on nonzero) |

**Safety**: Unsafe. Affects estimator state.

---

### Figure-8 Path (0x11)

Configure and execute figure-8 path.

| Index | Meaning | Notes |
|-------|---------|-------|
| 0–7 | Parameters, start/stop | SDK only |

**Safety**: Unsafe. SDK only.

---

### Waypoint Spacing (0x12)

Configure waypoint spacing and reset.

| Index | Value | Meaning |
|-------|-------|---------|
| 0 | Spacing (m) | Negative clamped to 0 |

**Safety**: Unsafe.

---

### Reference Model Switch (0x13)

Switch reference model and capture state snapshot.

| Index | Value | Meaning |
|-------|-------|---------|
| 0 | Selector | Rounded/clamped to 0–2 |

**Safety**: Unsafe. Changes controller reference model.

---

### SysID / Geofence (0x14)

System identification excitation and geofence control.

| Index | Meaning |
|-------|---------|
| 0–5 | SysID parameters |
| 6 | Start/abort SysID |
| 7 | Geofence enable/disable |

**Safety**: Unsafe.

---

### Gyro Filter (0x15)

Configure gyro low-pass filter.

| Index | Meaning | Value range |
|-------|---------|------------|
| 0 | Enable | 0 or 1 |
| 1 | Cutoff frequency | Hz |

**Safety**: Unsafe.

---

### Motor Bench Output (0x16)

Set disarmed motor output for bench testing.

| Index | Meaning | Value range |
|-------|---------|------------|
| 0 | Heartbeat | — |
| 1 | Motor index | 0–4 |
| 2 | CCR value | 2000–4000 |

**Safety**: Unsafe. Requires explicit bench state and disarmed constraints.

---

### OF Bias Capture (0x17)

Capture optical flow bias estimate.

| Index | Meaning |
|-------|---------|
| 0 | Capture (no-op on nonzero) |

**Safety**: Unsafe.

---

### EKF Reset (0x18)

Force recalibration/reset of EKF.

| Index | Precondition | Meaning |
|-------|---------------|---------|
| 0 | `GROUND_IDLE` or `DisArmed` only | Reset EKF |

**Safety**: Unsafe. Affects estimator state.

---

### OF Bias Estimator Mode (0x1E)

Configure optical flow bias estimator mode.

| Index | Meaning | Value range |
|-------|---------|------------|
| 0 | Mode | 0=FIXED, 1=EMA, 2=EKF |
| 1 | Freeze | ≥ 0.5 to freeze |

**Safety**: Unsafe.

---

## Result codes

### ACK (0x30)

Command was queued for processing.

```javascript
{
  frame_type: 0x30,
  version: 1,
  tx_id: number,
  command_id: number,
  index: number,
  value: number
}
```

### REJECTED (0x31)

Command was rejected before execution.

```javascript
{
  frame_type: 0x31,
  version: 1,
  tx_id: number,
  command_id: number,
  index: number,
  reason: string,      // e.g., "SAFETY_INTERLOCK", "UNKNOWN_COMMAND"
  detail: string       // Human-readable detail
}
```

**Known rejection reasons:**

| Reason | Meaning |
|--------|---------|
| `UNKNOWN_COMMAND` | Command ID not recognized |
| `SAFETY_INTERLOCK` | Precondition not met (e.g., SDK mode required) |
| `INVALID_INDEX` | Index out of range |
| `INVALID_VALUE` | Value out of range |
| `QUEUE_FULL` | Command queue at capacity |

### APPLIED (0x32)

Command was successfully applied.

```javascript
{
  frame_type: 0x32,
  version: 1,
  tx_id: number,
  command_id: number,
  index: number,
  detail: string       // "applied" or "duplicate"
}
```

**Idempotence**: Replaying a command with the same transaction ID returns `APPLIED/duplicate` without re-executing.

---

## Safety restrictions

### Safety classes

| Class | Description | Examples |
|-------|-------------|----------|
| `critical` | Affects flight state, arming, or safety | 0x04, 0x0D, 0x0E, 0x06 |
| `boundary` | Adjusts operational limits | 0x01, 0x09 |
| `operational` | Runtime configuration | 0x0F, 0x15 |
| `diagnostic` | Observability only | 0x17, 0x18 |

### Preconditions

| Command | Precondition |
|---------|--------------|
| 0x06 (Virtual RC) | SDK mode (`FlyMode` indicates SDK) |
| 0x0A–0x0C, 0x11 | SDK authority granted |
| 0x16 (Motor bench) | Explicit bench state, disarmed |
| 0x18 (EKF reset) | `GROUND_IDLE` or `DisArmed` |

### Emergency stop

Command `0x0D` (Abort All) has **no preconditions**. It immediately:
- Aborts all active paths
- Sets sticks to neutral
- Triggers dangerous stop sequence

---

## Usage examples

### Submit a command via shell API

```javascript
// Release SDK authority (safe, verified on S5)
api.submitCommand(0x0E, 0, 0).then(function(result) {
  console.log('Transaction:', result.transaction_id);
}).catch(function(err) {
  console.error('Failed:', err);
});
```

### Submit via HTTP POST

```bash
curl -X POST http://localhost:8081/commands \
  -H "Content-Type: application/json" \
  -d '{"command_id": 14, "index": 0, "value": 0}'
```

### Poll for result (Python)

```python
from ground_station.service.core import GroundStationService

service = GroundStationService()
tx_id = service.submit_command(0x0E, 0, 0.0)

# Poll for result
import time
for _ in range(20):  # 2 second timeout
    result = service.poll_command_result(tx_id)
    if result:
        print(f"Result: {result.frame_type.name} - {result.detail}")
        break
    time.sleep(0.1)
```

### Error handling

```javascript
api.submitCommand(0x06, 0, 0.5).then(function(result) {
  // Success - check result frame
  console.log('Transaction ID:', result.transaction_id);
}).catch(function(err) {
  // Network error
  console.error('Network error:', err);
});

// Note: Rejection is not an error - it resolves the promise with a result frame
```

---

## See also

- [ARCHITECTURE.md](ARCHITECTURE.md) — System overview and command flow
- [TELEMETRY_SPEC.md](TELEMETRY_SPEC.md) — Telemetry data format
- [S3 report](reports/S3-command-events.md) — Command protocol validation evidence
- [S5 report](reports/S5-authority-plugins.md) — Authority management
