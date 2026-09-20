# Dashboard Command Panel — Implementation Notes

> **Note:** For the canonical command reference (all 30 commands, index semantics, value ranges, safety classes), see [COMMAND_SPEC.md](COMMAND_SPEC.md). For the Shell API used by this panel, see [shell/plugin-api.md](shell/plugin-api.md). This document describes the UI implementation in `shell/plugins/command-panel.js`.

## Overview

The enhanced command panel plugin (`command-panel.js`) provides a comprehensive command interface for the UAV ground station dashboard, replacing the basic command form with:

1. **Command Registry Lookup** — Human-readable command names and valid ranges
2. **Result Feedback** — Color-coded status (ACK, APPLIED, REJECTED, SAFETY_INTERLOCK)
3. **Command History** — Persistent log of last 20 commands in localStorage
4. **Safety Interlock Warnings** — Prominent alerts with link to safety panel
5. **Quick Command Buttons** — One-click presets for common operations

## Files Modified/Created

| File | Change |
|------|--------|
| `shell/plugins/command-panel.js` | **NEW** — Enhanced command panel plugin |
| `shell/index.html` | Modified to load the new plugin |
| `DASHBOARD_COMMANDS.md` | **NEW** — This documentation |

## Features

### 1. Command Name/Range Lookup

The plugin loads a static command registry mapping numeric IDs to human-readable names:

```javascript
COMMAND_REGISTRY = {
  0x00: { name: 'NOP',              min: 0,     max: 0,      unit: '' },
  0x01: { name: 'PID Gain',         min: -10,   max: 10,     unit: 'gain' },
  0x04: { name: 'Flight Mode',      min: 0,     max: 5,      unit: 'mode' },
  0x06: { name: 'Virtual RC',       min: 0,     max: 1,      unit: 'on/off' },
  // ... more commands
}
```

When a command is selected in the dropdown, the valid range is displayed below the form:
```
Valid range: 0 – 5 mode
```

The value input is automatically clamped to the valid range.

### 2. Command Result Feedback

Results are displayed with color-coded status badges:

| Status | Color | Border | Icon |
|--------|-------|--------|------|
| Pending | Amber | Left amber | ⏳ |
| Applied | Green | Left green | ✓ |
| Rejected | Red | Left red | ✗ |
| Safety Interlock | Red (bg) | Left red | ⚠ |

The panel polls `/state` for transaction results for up to 3 seconds, then assumes the command was applied if no response is received.

### 3. Command History Log

Commands are persisted to localStorage under key `gs_cmd_history`:

```javascript
{
  ts: "14:32:05",        // Timestamp
  cmdId: 4,              // Numeric ID
  cmdName: "Flight Mode", // Human-readable name
  index: 0,              // Command index
  value: 1.0,            // Submitted value
  status: "applied",     // Result status
  detail: ""             // Optional rejection reason
}
```

- Maximum 20 entries (FIFO)
- Collapsible panel (visibility persisted to localStorage)
- Clear button to reset history
- Visual indicators: green left border for applied, red for rejected

### 4. Safety Interlock Integration

When a command is rejected with `SAFETY_INTERLOCK` reason:

1. The result box turns red with a warning background
2. A prominent warning box appears below the form:
   ```
   ⚠ SAFETY INTERLOCK ACTIVE
   
   This command was rejected for safety reasons.
   Check arm status and safety limits before retrying.
   
   [Open Safety Panel]
   ```

3. Clicking "Open Safety Panel" reveals the safety/alarms card

### 5. Quick Command Buttons

Preset buttons for common operations:

| Button | Command | Index | Value | Color |
|--------|---------|-------|-------|-------|
| ⚡ Arm Auth | SDK Arm Auth (0x0E) | 0 | 1 | Green |
| ☐ Disarm | SDK Arm Auth (0x0E) | 0 | 0 | Red |
| ❘ Stabilize | Flight Mode (0x04) | 0 | 0 | Default |
| ❘ AltHold | Flight Mode (0x04) | 0 | 1 | Default |
| ❘ PosHold | Flight Mode (0x04) | 0 | 2 | Default |
| ▶ SysID | SysID Chirp (0x20) | 0 | 1 | Amber |
| ■ Abort All | Abort All (0x13) | 0 | 1 | Red |
| ★ MRAC On | MRAC Adapt (0x16) | 0 | 1 | Purple |
| ☆ MRAC Off | MRAC Adapt (0x16) | 0 | 0 | Purple |

## Architecture

### Plugin Registration

```javascript
window.__registerPlugin__('Command Panel', initFn, destroyFn);
```

### State Dependencies

The plugin reads from `/state` to poll for transaction results:

- `state.last_transaction_result` — Last transaction result object
- `state.command_results` — Array of transaction results

### Shell API Usage

```javascript
// Submit a command (returns Promise)
api.submitCommand(cmdId, index, value).then(function(data) {
  // { transaction_id: 0x5001 }
});

// Subscribe to state updates
api.subscribe(function(state) { /* ... */ });

// Get current state
var state = api.getState();
```

## Result Polling Flow

```
1. User submits command
2. POST /commands → returns transaction_id
3. Start polling /state every 200ms (max 15 times = 3s)
4. On result match:
   - Update UI with status (applied/rejected/safety_interlock)
   - Add to history
   - Stop polling
5. On timeout:
   - Assume applied (common for local commands)
   - Update UI and add to history
```

## localStorage Keys

| Key | Purpose |
|-----|---------|
| `gs_cmd_history` | JSON array of last 20 commands |
| `gs_cmd_hist_visible` | History panel visibility (0/1) |

## Adding New Commands

To add a new command to the registry, edit `COMMAND_REGISTRY` in `command-panel.js`:

```javascript
0xNN: { name: 'Your Command', min: X, max: Y, unit: 'unit_name' }
```

To add a quick command button, edit `QUICK_COMMANDS`:

```javascript
{ id: 'unique_id', label: 'Button Text', icon: '&#X;', cmdId: 0xNN, index: N, value: V }
```

## Browser Compatibility

- Uses ES5 syntax for broad compatibility
- localStorage for persistence
- fetch API for polling
- No external dependencies

## Limitations

- Result polling assumes response within 3 seconds
- Command history limited to 20 entries
- No undo/redo functionality
- Safety interlock handling requires firmware support for `SAFETY_INTERLOCK` reason
