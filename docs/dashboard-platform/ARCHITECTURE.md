# Architecture

This document describes the high-level architecture of the Adaptive Controller Ground Station Platform. It covers the system overview, data flow, plugin architecture, session management, experiment runtime, and the complete Agent API surface.

## Table of contents

1. [System overview](#system-overview)
2. [Data flow](#data-flow)
3. [Plugin architecture](#plugin-architecture)
4. [Session management](#session-management)
5. [Experiment runtime](#experiment-runtime)
6. [Agent API reference](#agent-api-reference)

---

## System overview

The platform connects an STM32F4/FreeRTOS flight controller (drone) to browser-based dashboards and external agents through a layered architecture:

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Browser (Shell)                              │
│   ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────────┐  │
│   │ Status  │ │  MRAC   │ │  EKF    │ │ Safety  │ │ Telemetry   │  │
│   │ Panel   │ │ Panel   │ │ Panel   │ │ Panel   │ │ Explorer    │  │
│   └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────────┘  │
│                         shellApi (subscribe, registerPanel, ...)      │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTP REST / WebSocket
┌──────────────────────────────▼──────────────────────────────────────┐
│                     GroundStationService                              │
│   ┌──────────────┐  ┌───────────────┐  ┌─────────────────────────┐ │
│   │  ApiServer   │  │ CommandGateway │  │    SessionStore (SQLite) │ │
│   │  (port 8081) │  │ (transaction) │  │    SessionReplay         │ │
│   └──────────────┘  └───────────────┘  └─────────────────────────┘ │
│                              │                                       │
│                    ┌──────────▼──────────┐                           │
│                    │     StateHub        │                           │
│                    │  (in-process pub/sub)│                          │
│                    └─────────────────────┘                           │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ Wi-Fi (MicoAir UDP 14550)
┌──────────────────────────────▼──────────────────────────────────────┐
│                        WifiBridge                                    │
│   ┌──────────────┐  ┌───────────────┐  ┌─────────────────────────┐ │
│   │ MultiStream  │  │ Transaction   │  │   Protocol decoder       │ │
│   │ Decoder      │  │ Ledger        │  │   (0xAA 0xBB / 0xCC 0xDF)│ │
│   └──────────────┘  └───────────────┘  └─────────────────────────┘ │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ UDP
┌──────────────────────────────▼──────────────────────────────────────┐
│                    Drone (STM32F4 / FreeRTOS)                         │
│   ┌──────────────┐  ┌───────────────┐  ┌─────────────────────────┐ │
│   │  Send_Task   │  │ Command       │  │   Subscribe            │ │
│   │  (telemetry) │  │ Service       │  │   Service              │ │
│   └──────────────┘  └───────────────┘  └─────────────────────────┘ │
│   ┌──────────────┐  ┌───────────────┐  ┌─────────────────────────┐ │
│   │  MRAC        │  │ EKF           │  │   Flight FSM            │ │
│   │  Controller  │  │ Estimator     │  │   (ARM/FlyMode)        │ │
│   └──────────────┘  └───────────────┘  └─────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### Component responsibilities

| Component | Language | Responsibility |
|-----------|----------|----------------|
| Drone firmware | C (Keil ARMCC) | Flight control, telemetry emission, command execution |
| WifiBridge | Python | Wi-Fi bridge boundary, protocol parsing, transaction management |
| GroundStationService | Python | State aggregation, session lifecycle, API serving |
| ApiServer | Python (stdlib) | HTTP REST endpoints, static file serving |
| SessionStore | Python + SQLite | Persistent storage of sessions, events, telemetry |
| Shell (index.html) | Vanilla JS | Browser UI, plugin loading, 500ms polling loop |
| Plugins | Vanilla JS | Dashboard panels, visualizations |

---

## Data flow

### Telemetry ingestion

1. **Firmware emission**: `Send_Task` sends telemetry frames over USART3 → MicoAir
2. **Wi-Fi transport**: MicoAir forwards UDP datagrams to ground station
3. **Bridge reception**: `WifiBridge.recv()` receives raw bytes
4. **Protocol parsing**: `_parse_one()` identifies frame type (0x01–0x06 legacy, 0x09–0x0C streams)
5. **Stream decoding**: `MultiStreamDecoder.decode()` unpacks typed streams by slot
6. **State publication**: `GroundStationService.ingest()` stores raw frames and decoded telemetry
7. **StateHub broadcast**: Listeners (shell, plugins) receive updated `ServiceState`

```
Raw UDP → WifiBridge.recv() → _parse_one() → MultiStreamDecoder.decode()
  → GroundStationService.ingest() → StateHub.publish() → Shell poll → Plugin callbacks
```

### Command submission

1. **Plugin/shell**: `shellApi.submitCommand(cmdId, index, value)` fires
2. **HTTP POST**: `POST /commands` → `ApiServer` → `CommandGateway`
3. **Transaction creation**: `CommandGateway.submit()` allocates transaction ID
4. **Bridge send**: `WifiBridge.send_transaction()` encodes `0xCC 0xDF` envelope
5. **Firmware processing**: Command parsed, validated, applied
6. **Result event**: Firmware emits `0x30` (ACK), `0x31` (REJECTED), or `0x32` (APPLIED)
7. **Result polling**: `CommandGateway.poll()` retrieves result, fires callback

```
submitCommand() → POST /commands → CommandGateway → WifiBridge.send_transaction()
  → 0xCC 0xDF envelope → Drone → Result frame → poll_command() → callback
```

### State publication

`ServiceState` is an immutable snapshot published on each ingest cycle:

```python
ServiceState {
    schema_id: str,           # "r1-s1-0x9F32E2EA"
    session_id: str,           # UUID of current session
    connected: bool,            # Bridge health
    samples: int,              # Total telemetry samples this session
    last_update_ns: int,       # Unix timestamp (ns) of last update
    streams: dict[int, StreamState]  # Per-slot telemetry
}

StreamState {
    tag: int,                  # Slot number (0-3)
    sequence: int,             # Frame sequence number (mod 256)
    received: int,             # Frames received since subscription
    dropped: int,              # Frames lost (detected via sequence gaps)
    loss_pct: float,           # drop_pct = dropped / (received + dropped) * 100
    values: dict[str, float],  # Key-value pairs (e.g., "ch0": 0.123)
}
```

---

## Plugin architecture

### Discovery and loading

1. Shell scans `shell/plugins/` directory at startup
2. Each `.js` file is fetched and evaluated
3. Plugin calls `window.__registerPlugin__(name, init, destroy)`
4. `init(shellApi)` is called once per plugin

### Lifecycle states

```
Plugin states:
  disabled → shadow → candidate → active
                     ↓           ↓
                 faulted     faulted

Transitions:
  - disabled/shadow/candidate can become candidate via authority grant
  - candidate → active: AuthorityArbiter grants output authority
  - any → faulted: output failure or timeout
  - faulted → candidate: manual reset
```

### Plugin contract

```javascript
// Plugin must export:
export const name = "My Plugin";

export function init(shellApi) {
  // Register panels, subscribe to state, set up timers
}

export function destroy() {
  // Optional cleanup: clear intervals, remove event listeners
}
```

### State subscription pattern

```javascript
export function init(api) {
  api.registerPanel("My Panel", function(container, api) {
    // Build initial DOM
    container.innerHTML = '<div id="my-value">—</div>';
    
    // Subscribe to state updates
    api.subscribe(function(state) {
      var val = state.streams && state.streams['0'] && state.streams['0'].values.ch0;
      document.getElementById('my-value').textContent = val != null ? val.toFixed(4) : '—';
    });
  });
}
```

See [PLUGIN_DEVELOPER_GUIDE.md](PLUGIN_DEVELOPER_GUIDE.md) for full API reference and examples.

---

## Session management

### Session lifecycle

```
Session states: STARTING → ACTIVE → ENDED

Events emitted:
  - session_started (schema_id, source)
  - telemetry_sample (stream_id, sequence, values)
  - command_submitted (transaction_id, command_id, index, value)
  - command_result (transaction_id, result, reason?)
  - session_ended (reason)
```

### Storage schema (SQLite WAL)

```sql
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  started_ns INTEGER NOT NULL,
  ended_ns INTEGER,
  schema_id TEXT NOT NULL,
  source TEXT,
  metadata TEXT  -- JSON
);

CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT REFERENCES sessions(id),
  time_ns INTEGER NOT NULL,
  type TEXT NOT NULL,
  data TEXT  -- JSON
);

CREATE TABLE telemetry (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT REFERENCES sessions(id),
  time_ns INTEGER NOT NULL,
  stream_id INTEGER NOT NULL,
  sequence INTEGER,
  source_time_ms INTEGER,
  values TEXT  -- JSON
);

CREATE TABLE raw_frames (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT REFERENCES sessions(id),
  time_ns INTEGER NOT NULL,
  direction TEXT,  -- 'rx' or 'tx'
  raw_bytes BLOB
);
```

### Replay

`SessionReplay` iterates `SessionStore` records as `ReplayRecord`:

```python
ReplayRecord {
    time_ns: int
    kind: str  # 'event' | 'telemetry'
    data: dict
}

# Usage:
for record in SessionReplay(store, session_id):
    print(record.time_ns, record.kind, record.data)
```

---

## Experiment runtime

### State machine

```
IDLE → PRECHECK → CONFIGURE → SETTLE → MEASURE → RESTORE → COMPLETE
                                ↓
                              ABORTED ← (safety predicate or operator)
                                ↓
                              RESTORE → ABORTED
```

### Parameter management

1. **Snapshot**: Before parameter change, entire parameter map is snapshotted
2. **Update**: Parameter modified in firmware via command
3. **Measure**: Telemetry collected during measurement window
4. **Restore**: Unconditional restore of original parameter map on completion/abort

### Safety predicates

```python
ExperimentRuntime {
    safety_predicate: (state) -> bool  # Return False to trigger abort
    
    # Default safety predicate:
    # - ARM status must remain disarmed
    # - Battery voltage above 10.5V
    # - Stream loss below 10%
}
```

### Host ↔ Firmware coordination

- **Host owns**: Storage, analysis, timing, safety abort decision
- **Firmware owns**: Parameter application, timing precision, safety enforcement

See [S6 report](reports/S6-experiments.md) and `ground_station/platform/experiments.py` for details.

---

## Agent API reference

### REST endpoints

All endpoints are on `http://localhost:8081` (default). See [COMMAND_SPEC.md](COMMAND_SPEC.md) for command semantics and [TELEMETRY_SPEC.md](TELEMETRY_SPEC.md) for telemetry schema.

The authoritative, machine-readable route list is `GET /api/routes` (route -> description / query params, plus the UI `data-testid` scheme). The tables below are a human summary; see [AGENT_GUIDE.md](AGENT_GUIDE.md) for how agents should drive the service and dashboard.

#### Session endpoints

| Method | Path | Description | Response |
|--------|------|-------------|----------|
| GET | `/sessions` | List all sessions | `[{id, started_ns, ended_ns, schema_id, source}]` |
| GET | `/sessions/<id>` | Session detail | `{id, started_ns, ended_ns, schema_id, source, metadata}` |
| GET | `/sessions/<id>/records` | All records merged by time | `[{time_ns, kind, data}]` |
| POST | `/sessions/<id>/export` | Export to CSV | `{output_path, stream_id?}` body → 200 OK |

#### Experiment endpoints

| Method | Path | Description | Request body | Response |
|--------|------|-------------|--------------|----------|
| GET | `/experiments` | List active runs | — | `[{name, state, tick, samples, events, params}]` |
| GET | `/experiments/<name>` | Experiment detail | — | Full experiment object |
| POST | `/experiments` | Start experiment | `{name, settle_ticks, measure_ticks, parameters}` | `201 {run_id}` |
| POST | `/experiments/<name>/abort` | Abort by name | — | 200 OK |

#### Analysis endpoints

| Method | Path | Description | Response |
|--------|------|-------------|----------|
| GET | `/artifacts` | Indexed result files | `[{path, session_id, schema_id, created, metrics}]` |
| GET | `/analysis/compare` | Compare sessions | Query: `a`, `b`, `stream`, `key` |

### Python analysis modules

#### `ground_station/analysis/session.py`

```python
from ground_station.analysis.session import (
    query_telemetry, telemetry_stats, compare_sessions, export_session_csv
)

# Query telemetry for a session
for sample in query_telemetry(store, session_id, stream_id=0, key='ch0', since_ns=start):
    print(sample['time_ns'], sample['values'])

# Get per-stream statistics
stats = telemetry_stats(store, session_id, stream_id=0)
# Returns: {stream_id: StreamStats(count, rate_hz, loss_events)}

# Compare one key across two sessions
comparison = compare_sessions(store, sid_a, sid_b, stream_id=0, key='ch0')
# Returns: {sid_a: {min, max, mean, std, n}, sid_b: {...}, diff_mean, diff_max}

# Export session to CSV
export_session_csv(store, session_id, '/tmp/session.csv', stream_id=0)
```

#### `ground_station/analysis/runs.py`

```python
from ground_station.analysis.runs import summarize_run, compare_runs, detect_settling

# Summarize an experiment run
summary = summarize_run(run)
# Returns: {name, state, duration_ms, settle_ms, measure_ms, parameter_changes, events}

# Detect settling in a telemetry series
settling_index = detect_settling(samples, key='ch0', window=10, threshold=0.01)
# Returns: first index where rolling std < threshold, or -1 if never settled

# Compare multiple runs
comparisons = compare_runs([run1, run2])
# Returns: [{name, state, duration_ms, parameter_deltas, events}, ...]
```

#### `ground_station/analysis/artifacts.py`

```python
from ground_station.analysis.artifacts import index_artifacts, find_similar

# Index all result files
artifacts = index_artifacts(root='ground_station/results')
# Returns: [{path, session_id, schema_id, created, metrics}]

# Find similar result files
similar = find_similar(root='ground_station/results', threshold=0.95)
# Returns: [(path_a, path_b, score), ...]
```

### Telemetry key naming

Keys in `state.streams[N].values` use schema-defined names:

| Schema prefix | Meaning |
|---------------|---------|
| `ch0` – `ch14` | Raw channel index (see TELEMETRY_SPEC.md) |
| `mrac.*` | MRAC adaptive controller parameters |
| `ekf.*` | EKF estimator states |
| `estimator.*` | Estimator metadata (filter status, covariance) |
| `rtos.*` | RTOS runtime metrics |
| `system.*` | System-level metrics |

---

## File locations

### Key source files

| File | Purpose |
|------|---------|
| `ground_station/service/core.py` | GroundStationService |
| `ground_station/service/api.py` | ApiServer, all REST endpoints |
| `ground_station/service/storage.py` | SessionStore, SQLite schema |
| `ground_station/service/replay.py` | SessionReplay |
| `ground_station/platform/experiments.py` | ExperimentRuntime |
| `ground_station/platform/plugins.py` | AuthorityArbiter, PluginState |
| `ground_station/platform/resources.py` | ResourceMap |
| `ground_station/analysis/session.py` | Telemetry analysis |
| `ground_station/analysis/runs.py` | Experiment analysis |
| `ground_station/analysis/artifacts.py` | Artifact indexing |
| `docs/dashboard-platform/shell/index.html` | Browser shell |
| `docs/dashboard-platform/shell/plugin-api.md` | Plugin API reference |
| `firmware/platform_registry_gen.{h,c}` | Generated registry |
| `firmware/command_protocol.{h,c}` | Command envelope |

### Generated artifacts

| File | Content |
|------|---------|
| `ground_station/generated/platform_registry.json` | 39 descriptors, CRC 0x9F32E2EA |
| `ground_station/generated/telemetry_schema.json` | Typed stream metadata |
| `docs/dashboard-platform/generated/platform-registry.md` | Human-readable registry |

---

## Security considerations

- **Motor bench commands** require explicit bench state, disarm constraints, bounded output, heartbeat, and immediate abort
- **Parameter changes** declare whether live-safe, boundary-safe, disarmed, or reset-required
- **Experiment abort** unconditionally restores baseline configuration
- **Stale telemetry** (>3s without update) triggers an alarm in the shell
- **Command queue** has 15 usable entries; overflow increments `gs_cmd_drop_count` silently
