# Session Replay & Path Planning Panels — S13 Report

**Date:** September 17, 2026  
**Status:** Implemented

## Overview

Two new plugins have been added to the UAV Ground Station dashboard:

1. **Session Replay Panel** (`replay-panel.js`) — Session management and telemetry playback
2. **Path Planning Panel** (`path-panel.js`) — 2D trajectory visualization with waypoint management

---

## 1. Session Replay Panel

### Features

| Feature | Description |
|---------|-------------|
| Session List | Displays all recorded sessions with ID, start time, schema ID, and source |
| Session Detail | Click a session to view detailed metadata (duration, record count) |
| Record Browser | Load and browse all telemetry records for a session |
| Export | Export session data to CSV via `POST /sessions/<id>/export` |
| Playback | Timeline scrubber with play/pause controls and variable speed (1x–10x) |
| Stream Stats | Per-slot telemetry coverage statistics |
| Filter | Filter records by stream/slot |

### API Endpoints Used

- `GET /sessions` — List all sessions
- `GET /sessions/<id>` — Session detail
- `GET /sessions/<id>/records` — All records for a session
- `POST /sessions/<id>/export` — Export to CSV

### UI Layout

```
┌─────────────────┬────────────────────────────────────────────┐
│ Sessions        │ Stream Stats Table                          │
├─────────────────┼────────────────────────────────────────────┤
│ [Session #1]    │ Filter: [All Slots ▼] [Refresh]            │
│ [Session #2]    ├────────────────────────────────────────────┤
│ [Session #3]    │  ▶  ═══════●════════  [1x][2x][5x][10x]   │
│ ...             │      Scrubber         Index: 45/1200 14:32  │
│                 ├────────────────────────────────────────────┤
│ ────────────────│ Timeline canvas                            │
│ Session Detail  ├────────────────────────────────────────────┤
│ ID: 12          │ Record Display                             │
│ Schema: abc123  │ ┌─────┐ ┌─────┐ ┌─────┐                   │
│ Started: 14:30  │ │slot0│ │slot9│ │ ... │                    │
│ [Export CSV]    │ │ch0:1.23│ │pos_x:4.56│                   │
│ [Load Records]  │ └─────┘ └─────┘ └─────┘                   │
└─────────────────┴────────────────────────────────────────────┘
```

### Key Implementation Details

- **Playback speeds:** 1x, 2x, 5x, 10x records per second
- **Timeline:** Canvas-based mini-visualization of record values
- **Scrubber:** HTML range input mapped to record index percentage
- **Record grouping:** Values grouped by slot for readability
- **Key formatting:** `slot0.ch0.5` → `ch5` for compact display

---

## 2. Path Planning Panel

### Features

| Feature | Description |
|---------|-------------|
| 2D Canvas | Real-time trajectory visualization using Canvas API |
| EKF Position | Reads `ekf.pos_x`, `ekf.pos_y` from live state when available |
| Demo Mode | Synthetic Lissajous-curve trajectory when no EKF data |
| Trail | Last 100 position points displayed as trajectory |
| Markers | Home (H), Target (T), Current position, Waypoints (1–N) |
| Waypoints | Editable X,Y table with add/delete functionality |
| Zoom | Zoom in/out controls (0.2x – 5.0x) |
| Metrics | Total distance, max deviation from planned path |
| Pan | Origin-centered with pan offset support |

### EKF Position Detection

The panel attempts to read position data from multiple key patterns:

```javascript
// Patterns tried in order of preference:
'ekf.pos_x', 'ekf.pos_y'           // Primary EKF keys
'estimator.pos_x', 'estimator.pos_y' // Alternative keys
'pos_x', 'pos_y'                     // Fallback bare names
'state.pos_x', 'state.pos_y'         // State prefix
```

Position is extracted from `stream0.values` or `stream9.values` (typed stream).

### Demo Mode

When no EKF position data is detected:
- Lissajous figure: `x = 5·sin(0.3t) + 2·cos(0.7t)`, `y = 4·cos(0.4t) + 1.5·sin(0.9t)`
- Update rate: 50ms intervals
- Yellow "DEMO MODE" badge shown in corner
- Automatically exits demo mode when real EKF data appears

### UI Layout

```
┌───────────────────────────────────────┬─────────────────────────┐
│                                       │ Path Metrics            │
│     ┌─────────────────────────┐      │ ┌────────┬────────┐     │
│     │      TRAJECTORY CANVAS   │      │ │Distance│ MaxDev │     │
│     │                         │      │ │ 12.34m │  0.56m  │     │
│     │    ●───●───●───●        │      │ └────────┴────────┘     │
│     │   /                 \    │      ├─────────────────────────┤
│     │  H                   T   │      │ Zoom & View             │
│     │                         │      │ [Zoom In] [Zoom Out]     │
│     │        Canvas 400px     │      │ [Set Home] [Set Target]  │
│     └─────────────────────────┘      ├─────────────────────────┤
│     [+][−][↺]               DEMO      │ Waypoints               │
│                                      │ # │ X     │ Y     │ ⌫   │
│                                      │ 1 │ 0.00  │ 0.00  │  ✕  │
│                                      │ 2 │ 5.00  │ 3.00  │  ✕  │
│                                      │ 3 │ 8.00  │ 6.00  │  ✕  │
│                                      │ [+ Add Waypoint]         │
│                                      ├─────────────────────────┤
│                                      │ Legend                   │
│                                      │ ● Current  ● Home       │
│                                      │ ● Target   ● Waypoints  │
│                                      │ ━ Trajectory             │
│                                      └─────────────────────────┘
└───────────────────────────────────────┴─────────────────────────┘
```

### Path Metrics

- **Total Distance:** Sum of Euclidean distances between consecutive trajectory points
- **Max Deviation:** Maximum perpendicular distance from any trajectory point to the nearest segment of the planned waypoint path

### Canvas Rendering

- Grid: 50px spacing (scaled by zoom)
- Origin crosshairs: Dashed lines through center
- Trail: Gradient fade from old (transparent) to recent (opaque blue)
- Planned path: Dashed amber line connecting waypoints
- Markers: Outer ring + inner filled circle + optional label

---

## Integration

### Registering Plugins

Both plugins use the standard `window.__registerPlugin__` pattern:

```javascript
window.__PLUGIN_NAME__ = 'Session Replay';
window.__PLUGIN_INIT__ = function(api) { /* ... */ };
window.__PLUGIN_DESTROY__ = function() { /* cleanup */ };
window.__registerPlugin__('Session Replay', window.__PLUGIN_INIT__, window.__PLUGIN_DESTROY__);
```

### Adding to Shell

Plugins are loaded by the shell from `PLUGIN_FILES` array in `index.html`:

```javascript
const PLUGIN_FILES = [
  '/plugins/status-panel.js',
  '/plugins/mrac-panel.js',
  '/plugins/estimator-panel.js',
  '/plugins/resource-panel.js',
  '/plugins/safety-panel.js',
  '/plugins/telemetry-explorer-panel.js',
  '/plugins/replay-panel.js',      // NEW
  '/plugins/path-panel.js',          // NEW
];
```

### API Dependencies

| Plugin | API Calls | State Subscriptions |
|--------|----------|---------------------|
| replay-panel.js | GET /sessions, GET /sessions/\<id\>, GET /sessions/\<id\>/records, POST /sessions/\<id\>/export | None (static data) |
| path-panel.js | None | Live state (for ekf.pos_x, ekf.pos_y) |

---

## CSS Variables Used

Both plugins respect the shell's dark theme CSS variables:

| Variable | Value | Usage |
|----------|-------|-------|
| `--bg` | `#1a1a2e` | Background |
| `--card` | `#16213e` | Card/panel background |
| `--accent` | `#0f3460` | Buttons, highlights |
| `--text` | `#e8e8e8` | Primary text |
| `--muted` | `#8888aa` | Secondary text, labels |
| `--red` | `#e94560` | Errors, target marker |
| `--green` | `#4ecca3` | Success, home marker |
| `--amber` | `#f5a623` | Warnings, waypoints |
| `--border` | `#2a2a4a` | Borders, dividers |

---

## Future Enhancements

### Session Replay Panel
- [ ] Add record filtering by timestamp range
- [ ] Support multi-select for session comparison
- [ ] Add record search by field value
- [ ] Implement step-forward/backward buttons
- [ ] Show field value delta between consecutive records

### Path Planning Panel
- [ ] Implement drag-and-drop waypoint positioning on canvas
- [ ] Add path smoothing algorithm (Bezier interpolation)
- [ ] Support importing waypoints from CSV
- [ ] Show velocity vectors on trajectory
- [ ] Add obstacle zones visualization
- [ ] Integrate with mission planning firmware commands

---

## File Locations

```
docs/dashboard-platform/shell/
├── index.html              # Shell with updated PLUGIN_FILES
└── plugins/
    ├── replay-panel.js      # NEW: Session replay plugin (583 lines)
    ├── path-panel.js        # NEW: Path planning plugin (651 lines)
    ├── status-panel.js
    ├── mrac-panel.js
    ├── estimator-panel.js
    ├── resource-panel.js
    ├── safety-panel.js
    └── telemetry-explorer-panel.js
```

---

## Testing Checklist

- [ ] Load dashboard — both panels appear in sidebar
- [ ] Session Replay: Click session → detail loads
- [ ] Session Replay: Load records → table populates
- [ ] Session Replay: Playback controls work
- [ ] Session Replay: Export generates CSV
- [ ] Path Panel: Demo mode animates trajectory
- [ ] Path Panel: Zoom in/out scales canvas
- [ ] Path Panel: Add/delete waypoint updates table
- [ ] Path Panel: Edit waypoint XY updates path
- [ ] Path Panel: Set Home/Target buttons work
- [ ] Path Panel: Metrics update as trajectory grows

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| Sep 17, 2026 | AI Assistant | Initial implementation |
