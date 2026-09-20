# S13 — Experiments Runtime & Motor Bench Panels

This document describes the two dashboard panels added for experiment control and motor bench testing.

## Experiment Runtime Panel (`experiment-panel.js`)

### Purpose
Controls the firmware experiment runtime to perform parameter sweep experiments with controlled settling and measurement phases.

### API Endpoints Used

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/experiments` | List active experiment runs |
| `GET` | `/experiments/<name>` | Get experiment detail (ticks, samples, events, params) |
| `POST` | `/experiments` | Start a new experiment |
| `POST` | `/experiments/<name>/abort` | Abort a running experiment |

### Experiment States

| State | Description |
|-------|-------------|
| `idle` | No experiment running |
| `settling` | Waiting for system to settle after parameter change |
| `measuring` | Collecting measurement samples |
| `complete` | Experiment finished successfully |
| `aborted` | Experiment stopped by operator or safety predicate |

### Request Format (Start Experiment)

```json
POST /experiments
{
  "name": "step_response",
  "settle_ticks": 100,
  "measure_ticks": 200,
  "parameters": {
    "safety.gs_max_horizontal_speed_mps": 5.0
  }
}
```

### Response Format (Experiment Detail)

```json
{
  "name": "step_response",
  "state": "measuring",
  "tick": 150,
  "settle_ticks": 100,
  "measure_ticks": 200,
  "samples": [0.12, 0.15, 0.14, ...],
  "events": [
    [0, "started", null],
    [100, "settled", "settling complete"],
    [101, "measuring", null],
    [200, "complete", null]
  ],
  "parameters_before": {
    "safety.gs_max_horizontal_speed_mps": 3.0
  },
  "parameters_after": {
    "safety.gs_max_horizontal_speed_mps": 5.0
  }
}
```

### Panel Features

1. **State Badge**: Shows current experiment state with color coding
2. **Tick Counter**: Displays current tick and sample count
3. **Settling Progress Bar**: SVG bar showing settling phase progress
4. **Measure Progress Bar**: SVG bar showing measurement phase progress
5. **Start/Abort Controls**: Form inputs for experiment parameters
6. **Event Log**: Scrollable log of experiment events with tick timestamps
7. **Parameter Sweep Table**: Shows before/after parameter values
8. **Auto-polling**: Updates every 500ms

### Parameter Fields

| Field | Description | Default |
|-------|-------------|---------|
| `name` | Experiment identifier | `step_response` |
| `settle_ticks` | Settling duration in ticks | `100` |
| `measure_ticks` | Measurement duration in ticks | `200` |
| `param.key` | Parameter registry key to modify | — |
| `param.val` | New parameter value | — |

---

## Motor Bench Panel (`motor-bench-panel.js`)

### Purpose
Provides direct control of motor outputs for bench testing without requiring flight.

### Command ID

| Command ID | Description |
|------------|-------------|
| `22` | Motor Bench — set individual motor throttle |
| `13` | Abort All — emergency stop all motors |

### Motor Command Format

```
submitCommand(cmdId=22, index=motor_num, value=throttle)
```

| Index | Motor | Description |
|-------|-------|-------------|
| `0` | Motor 1 | Front-right (typically) |
| `1` | Motor 2 | Front-left (typically) |
| `2` | Motor 3 | Back-left (typically) |
| `3` | Motor 4 | Back-right (typically) |

### Throttle Range

| Min | Max | Step | Unit |
|-----|-----|------|------|
| `0` | `1000` | `10` | PWM units |

### Safety Interlocks

1. **Bench Mode Indicator**: Always visible amber badge indicating bench testing mode
2. **Safety Warning**: Prominent warning about propeller removal/guarding
3. **Saturation Warnings**: Alerts when any motor reaches 98%+ saturation
4. **Emergency Stop**: Large red button sends Abort All command (ID 13)
5. **Panel Destroy Cleanup**: All motors set to zero when panel is closed

### Panel Features

1. **Bench Mode Badge**: Amber indicator always visible
2. **Motor Sliders**: 4 sliders (0-1000 range, step 10) with real-time value display
3. **Set Buttons**: Individual motor command buttons
4. **Global Controls**: "Send All Motors", "Zero All", "Emergency Stop" buttons
5. **RPM Feedback Display**: Shows motor feedback from telemetry (if available)
6. **Saturation Warnings**: Visual warnings for high motor output
7. **SVG Speed Bars**: Color-coded progress bars per motor

### Feedback Channels

The panel attempts to read RPM feedback from these telemetry keys:

| Key Pattern | Source |
|-------------|--------|
| `motor.rpm_0` – `motor.rpm_3` | Typed stream (slot 9) |
| `motor.motor_rpm_0` – `motor.motor_rpm_3` | Typed stream (slot 9) |
| `rpm.mot0` – `rpm.mot3` | Typed stream (slot 9) |
| `slot0.ch0.20` – `slot0.ch0.23` | Raw channel data |

---

## Integration Notes

### Shell API Usage

Both panels use the standard plugin API:

```javascript
// Register panel
api.registerPanel('Panel Name', function(container) {
  container.innerHTML = buildHTML();
  api.subscribe(onState);  // Subscribe to telemetry
});

// Submit commands
api.submitCommand(cmdId, index, value)
  .then(function(result) { /* success */ })
  .catch(function(err)   { /* failure */ });
```

### Polling Strategy

- **Experiment Panel**: Polls `/experiments` every 500ms for active experiment status
- **Motor Bench Panel**: Uses `api.subscribe()` to receive real-time telemetry updates for RPM feedback

### Theme Compatibility

Both panels use CSS custom properties for theme colors:
- `var(--bg)` — background
- `var(--text)` — primary text
- `var(--muted)` — secondary/muted text
- `var(--border)` — borders
- `var(--accent)` — accent color (blue)
- `var(--green)` — success/positive
- `var(--amber)` — warning
- `var(--red)` — danger/error

### Loading

To load these plugins, place them in `docs/dashboard-platform/shell/plugins/` and restart the ground station service. They will be automatically discovered and registered.
