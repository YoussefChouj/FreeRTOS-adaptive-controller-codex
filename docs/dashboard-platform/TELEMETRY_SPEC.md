# Telemetry Specification

This document describes the telemetry data format, stream/slot mapping, channel index meanings, variable naming conventions, update rates, and loss handling for the UAV Ground Station platform.

## Table of contents

1. [Stream/slot mapping](#streamslot-mapping)
2. [Channel index to physical meaning](#channel-index-to-physical-meaning)
3. [Variable naming conventions](#variable-naming-conventions)
4. [Update rates](#update-rates)
5. [Loss handling](#loss-handling)
6. [Schema reference](#schema-reference)

---

## Stream/slot mapping

The firmware supports up to 4 concurrent telemetry streams via the subscribe mechanism. Each stream slot carries a different telemetry group at a configurable divider.

### Slot assignments

| Slot | Frame type | Telemetry group | Nominal rate | Channels | CRC |
|------|-----------|-----------------|--------------|----------|-----|
| 0 | 0x01 | `legacy_status` | 20 Hz | 8 | CRC8 |
| 1 | 0x02 | `legacy_adaptive` | 80 Hz | variable (basis-dependent) | CRC8 |
| 2 | — | `mrac_weights` | 80 Hz | variable | CRC16 |
| 3 | — | `ekf_all` | 80 Hz | variable | CRC16 |

### Typed stream slots (subscribed via `0xCC 0xDE 0x21`)

| Slot | Stream type | Description | Typical channels |
|------|-------------|-------------|------------------|
| 9 | 0x09 | Dashboard frame A | gyro, accel, mag, baro, altitude, vbat |
| 10 | 0x0A | Inner loops | pitch/roll/yaw rate error and output |
| 11 | 0x0B | MRAC weights | θ₀–θ₅ per axis |
| 12 | 0x0C | EKF states | position, velocity, bias |

### State structure

Each slot in `state.streams` contains:

```javascript
{
  tag: 0,              // Slot number (0-3 for legacy, 9-12 for typed)
  sequence: 42,        // Frame sequence number (mod 256)
  received: 100,       // Frames received since subscription
  dropped: 2,         // Frames detected as missing (sequence gaps)
  loss_pct: 1.96,     // drop / (received + dropped) * 100
  values: {            // Key-value pairs (see channel mapping below)
    ch0: 0.123,
    ch1: -0.456,
    // ...
  }
}
```

---

## Channel index to physical meaning

The following channel mappings are based on **live data observation** from the powered drone (Sep 17 2026, S4 capture).

### Dashboard frame A (slot 0, 0x09)

| Channel | Physical meaning | Unit | Typical range | Notes |
|---------|-----------------|------|---------------|-------|
| ch0 | Gyroscope X | rad/s | ±4.0 | Pitch rate |
| ch1 | Gyroscope Y | rad/s | ±4.0 | Roll rate |
| ch2 | Accelerometer Z | m/s² | ±20 | Body-frame vertical |
| ch3 | Magnetometer X | — | varies | Heading reference |
| ch4 | Magnetometer Y | — | varies | Heading reference |
| ch5 | Magnetometer Z | — | varies | Heading reference |
| ch6 | Barometer temperature | °C | 20–50 | BMP280 |
| ch7 | Barometer pressure | hPa | 950–1050 | Sea level reference |
| ch8 | — | — | — | Reserved |
| ch9 | — | — | — | Reserved |
| ch10 | Altitude | m | 0–100+ | Barometric altitude |
| ch11 | Battery voltage | V | 10.5–12.6 | 3S LiPo |
| ch12 | Current | A | 0–20 | ESC current sense |
| ch13 | Status bits | — | — | ARM/FlyMode flags |
| ch14 | Spare | — | — | Reserved |

### Inner loops (slot 1, 0x0A)

| Channel | Physical meaning | Unit |
|---------|-----------------|------|
| ch0 | Pitch rate error | rad/s |
| ch1 | Roll rate error | rad/s |
| ch2 | Yaw rate error | rad/s |
| ch3 | Pitch PID output | PWM (1000–2000) |
| ch4 | Roll PID output | PWM |
| ch5 | Yaw PID output | PWM |

### MRAC weights (slot 2, 0x0B)

| Channel | Physical meaning | Unit |
|---------|-----------------|------|
| ch0–ch5 | θ₀–θ₅ | — |
| ch6 | u_nom | — |
| ch7 | x_m | — |

### EKF states (slot 3, 0x0C)

| Channel | Physical meaning | Unit |
|---------|-----------------|------|
| ch0–ch2 | Position x, y, z | m |
| ch3–ch5 | Velocity x, y, z | m/s |
| ch6–ch8 | Gyro bias x, y, z | rad/s |
| ch9–ch11 | Accel bias x, y, z | m/s² |

---

## Variable naming conventions

### Schema key names

Keys in `state.streams[N].values` follow these patterns:

| Pattern | Meaning | Example |
|---------|---------|---------|
| `ch0` – `ch14` | Raw channel by index | `ch0: 0.123` |
| `mrac.*` | MRAC controller parameter | `mrac.theta_0: 1.5` |
| `ekf.*` | EKF estimator state | `ekf.pos_x: 10.5` |
| `estimator.*` | Estimator metadata | `estimator.filter_status: 2` |
| `rtos.*` | RTOS runtime metric | `rtos.xTickCount: 12345` |
| `system.*` | System-level metric | `system.heap_free: 8192` |

### Naming rules for plugin authors

1. **Use schema keys exactly as defined** — do not invent new names
2. **Check for null/undefined** before using values
3. **Apply units when displaying** — don't show raw channel indices to users
4. **Use `toFixed()` for display** — avoid floating-point artifacts

```javascript
// ✅ Correct: apply units and check for null
var vbat = state.streams['0'].values.ch11;
if (vbat != null) {
  document.getElementById('vbat').textContent = vbat.toFixed(2) + ' V';
}

// ❌ Wrong: raw channel index, no null check
document.getElementById('vbat').textContent = state.streams['0'].values.ch11;
```

---

## Update rates

### Nominal rates

| Stream | Slot | Nominal rate | Measured rate (Sep 17 2026) |
|--------|------|-------------|---------------------------|
| legacy_status | 0 | 20 Hz | 20.2 Hz |
| legacy_adaptive | 1 | 80 Hz | 80.6 Hz |
| mrac_weights | 2 | 80 Hz | 80.6 Hz |
| ekf_all | 3 | 80 Hz | 80.6 Hz |

### Factors affecting rate

- **Subscription divider**: Each slot can be subscribed with a divider to reduce rate (e.g., divider=4 means 1/4 the base rate)
- **Transport budget**: Aggregate bytes/second is bounded; requesting too many channels may be rejected
- **Firmware scheduler**: Rates are approximate; `Send_Task` runs at 80 Hz nominal

### Rate measurement

```javascript
var lastUpdate = null;
var samples = 0;
var totalDt = 0;

api.subscribe(function(state) {
  var now = Date.now();
  if (lastUpdate !== null) {
    var dt = (now - lastUpdate) / 1000;  // seconds
    totalDt += dt;
    samples++;
    var avgRate = samples / totalDt;
    console.log('Average rate:', avgRate.toFixed(2) + ' Hz');
  }
  lastUpdate = now;
});
```

---

## Loss handling

### Loss detection

Loss is detected via sequence number gaps. The decoder tracks `received` and `dropped` counters per slot:

```javascript
api.subscribe(function(state) {
  Object.keys(state.streams).forEach(function(slot) {
    var s = state.streams[slot];
    console.log('Slot', slot, 
      'received:', s.received, 
      'dropped:', s.dropped, 
      'loss:', s.loss_pct.toFixed(2) + '%');
  });
});
```

### Loss thresholds and alarms

| Threshold | Condition | Alarm level | Action |
|-----------|-----------|-------------|--------|
| < 1% | Normal | None | — |
| 1–5% | Degraded | Amber ⚠️ | Investigate Wi-Fi link |
| > 5% | Critical | Red 🔴 | Check antenna, reduce range |

### Loss recovery

Loss counters reset when:
1. A new subscription is made to the slot
2. The schema is re-applied
3. The service restarts

```javascript
// Manual reset: re-subscribe to slot 0
api.submitCommand(0x21, 0, {
  divider: 1,
  transport: 3,  // USART3
  slot: 0,
  ranges: [{start: 0, end: 14}]  // all channels
});
```

---

## Schema reference

### Schema ID

```
r1-s1-0x9F32E2EA
```

Components:
- `r1` — Registry version 1
- `s1` — Schema version 1
- `0x9F32E2EA` — Registry CRC32

### Registry descriptors

| Kind | Count | Example |
|------|-------|---------|
| variable | 5 | `build_id`, `DroneStatus.ARM_Status`, `DroneStatus.FlyMode` |
| enum | 2 | `telemetry_mode`, `safety_class` |
| parameter | 4 | `gs_max_horizontal_speed_mps`, `gs_max_vertical_speed_mps` |
| command | 7 | `pid_gain`, `flight_mode`, `virtual_rc`, `abort_all` |
| telemetry | 4 | `legacy_status`, `legacy_adaptive`, `extended_attitude`, `typed_stream` |
| event | 2 | `boot`, `command_outcome` |
| plugin | 4 | `pid_controller`, `mrac_adaptive_layer`, `ekf9_estimator`, `optical_flow` |
| task | 7 | SystemMonitor, IMU_DataDeal, IMUSample, Stabilizer, Remoter, Autofly, Send |
| resource | 4 | `usart3_radio`, `uart5_probe_vcp`, `command_queue`, `freertos_heap` |

### Telemetry frame structures

**Frame A (0x01)** — 8 × float32 = 32 bytes + header + CRC

**Frame B (0x02)** — Variable, depends on `basis_count`:
- v3 tail: `16n + 202` bytes
- v13 tail: `16n + 206` bytes
- Where `n` = number of basis functions

**Extended telemetry (0x06)** — CRC16-CCITT/XModem

**Typed stream (0x09–0x0C)** — CRC16-CCITT/XModem, sequence number, source timestamp

---

## See also

- [ARCHITECTURE.md](ARCHITECTURE.md) — System overview and data flow
- [COMMAND_SPEC.md](COMMAND_SPEC.md) — Command reference
- [generated/platform-registry.md](generated/platform-registry.md) — Full registry listing
- [S4 report](reports/S4-telemetry.md) — Telemetry validation evidence
