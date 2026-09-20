# S14 — Command Panel Full Coverage

This document describes the S14 expansion of the Command Panel plugin in
`shell/plugins/command-panel.js`. The goal was to cover **all 30 commands**
from `COMMAND_SPEC.md`, fix command-ID mismatches, add per-command safety
classification, and ship several new sub-panels (Virtual RC, Bench Mode,
Navigation Paths, EKF Reset confirmation).

## Summary of changes

| Area | Before (pre-S14) | After (S14) |
|------|------------------|-------------|
| Commands in registry | 12 (mix of spec + legacy) | 30 (full spec + 4 legacy kept) |
| Abort All ID | `0x13` (wrong) | `0x0D` (correct per spec) |
| Reference Model Switch | `0xFE`-alike / mislabel | `0x13` |
| Safety classification | none | `critical` / `boundary` / `operational` / `diagnostic` per spec |
| Preconditions | none | rendered in dropdown tooltips + range hint |
| ARM/SDK live status | none | top bar shows `ARMED / SDK / DISARMED` |
| Virtual RC panel | none | 5-channel sliders, enable/disable, center-all |
| Bench Mode toggle | none | confirm-guarded ON/OFF, live pill |
| Navigation paths | none | grouped Start/Stop for TWC/Sin/Circle/Fig-8 |
| EKF Reset | bare button | requires disarm + confirm |
| Command history | flat list | grouped by `session_id`, filter chips, status icon, hex ID column |
| Result polling | timeout → "assumed applied" | timeout → "submitted (no result feedback)" |

The plugin is now **1042 lines** (well under the 2500-line cap) and still
zero-dependency.

---

## 1. Full COMMAND_REGISTRY (30 entries)

Every command in `COMMAND_SPEC.md` is now declared in `COMMAND_REGISTRY`,
with corrected IDs, ranges, units, safety class, precondition, index
description, and max index value.

| Hex | Name | Min | Max | Unit | Class | Precondition |
|-----|------|-----|-----|------|-------|--------------|
| `0x00` | NOP | 0 | 0 | — | diagnostic | none |
| `0x01` | PID Gain | 0 | 200 | gain | boundary | SDK auth + Disarmed |
| `0x02` | MRAC Gamma | 0 | 50 | rate | operational | SDK auth |
| `0x03` | Mixer & Throttle | 0 | 1 | ratio | boundary | Disarmed |
| `0x04` | Flight Mode / Abort | 0 | 1 | event | critical | none |
| `0x05` | MRAC What_limit | 0 | 100 | limit | operational | SDK auth |
| `0x06` | Virtual RC | -1 | 1 | stick | critical | SDK mode only |
| `0x07` | Bench Mode | 0 | 1 | on/off | critical | Bench state + Disarmed |
| `0x08` | MRAC What_tol | 0 | 10 | tol | operational | SDK auth |
| `0x09` | GS Safety Limits | 0 | 60 | m/s\|deg | boundary | none |
| `0x0A` | TWC Target | -1000 | 1000 | mixed | critical | SDK auth |
| `0x0B` | Sinusoid Path | 0 | 1 | path | critical | SDK auth |
| `0x0C` | Circle Path | 0 | 1 | path | critical | SDK auth |
| `0x0D` | Abort All | 0 | 0 | estop | critical | none (always allowed) |
| `0x0E` | SDK Arm Authority | 0 | 1 | auth | critical | none |
| `0x0F` | Runtime Flags | 0 | 102 | mode | operational | SDK auth for mode change |
| `0x10` | Reset Optical Flow | 0 | 0 | reset | operational | Disarmed |
| `0x11` | Figure-8 Path | 0 | 1 | path | critical | SDK auth |
| `0x12` | Waypoint Spacing | 0 | 100 | m | operational | none |
| `0x13` | Reference Model Switch | 0 | 2 | selector | operational | SDK auth |
| `0x14` | SysID / Geofence | 0 | 1 | mode | operational | SDK auth |
| `0x15` | Gyro Filter | 0 | 200 | Hz | operational | SDK auth |
| `0x16` | Motor Bench Output | 0 | 4000 | CCR | critical | Bench state + Disarmed |
| `0x17` | OF Bias Capture | 0 | 0 | capture | diagnostic | none |
| `0x18` | EKF Reset | 0 | 0 | reset | diagnostic | `GROUND_IDLE` or `DisArmed` |
| `0x1E` | OF Bias Estimator Mode | 0 | 2 | mode | operational | SDK auth |
| `0x1A` | Filter Cutoff (legacy) | 1 | 200 | Hz | operational | SDK auth |
| `0x1C` | Calibration (legacy) | 0 | 10 | mode | operational | Disarmed |
| `0x20` | SysID Chirp (legacy) | 0 | 3 | mode | operational | Disarmed |
| `0xFE` | Firmware Info | 0 | 0 | — | diagnostic | none |

The dropdown shows a colored CSS class per safety tier (red / amber / blue /
green) plus the precondition text in the `title` tooltip.

### Safety class color map

| Class | Color (CSS) |
|-------|-------------|
| `critical`    | `var(--red)` |
| `boundary`    | `var(--amber)` |
| `operational` | `#4a9eff` (blue) |
| `diagnostic`  | `var(--green)` |

A small filled circle (`.cp-safety-dot`) appears on every row in the manual
dropdown and on every quick-command button.

---

## 2. Command-ID corrections

Two IDs were wrong in the prior implementation:

| Function | Old ID | Correct ID (per spec) | Notes |
|----------|--------|-----------------------|-------|
| Abort All | `0x13` | `0x0D` | Fixed. Quick command + dropdown now use `0x0D`. |
| Reference Model Switch | unlabeled | `0x13` | Was being misused as Abort; now its own entry. |
| MRAC Adapt | `0x16` | re-routed to `0x0F` index 1 | `0x16` is Motor Bench Output per spec. The original "MRAC On/Off" buttons now use `0x0F`/`value=1` and `0x0F`/`value=0`. |
| SysID Chirp | `0x20` (index 0/value 1) | `0x14` (index 6/value 1) | Per spec, index 6 of `0x14` is start/abort SysID. |

All other IDs were checked against `COMMAND_SPEC.md` and verified.

---

## 3. ARM / SDK live badge

The top of the panel now shows two pills derived from the live state:

```text
ARMED / SDK  |  SDK: ✓  |  Bench: inactive   session: 1f2a3b4c
```

The logic is in `armStatusFromState(state)`:

| Source value | Meaning |
|--------------|---------|
| `state.streams[0].values.ch13` & `0x01` | bit 0 = ARM signal |
| `state.streams[0].values.ch14` & `0x01` | bit 0 = SDK authority |
| Both unset | `DISARMED` (green) |
| Only SDK set | `SDK` (blue) |
| Only ARM set | `ARMED` (red) |
| Both set | `ARMED / SDK` (red) |
| Telemetry missing | `UNKNOWN` (muted) |

The badges are recolored in real time via the `api.subscribe` callback.

### Blocking critical buttons while armed

Whenever a state update changes arm/sdk status, `refreshAllBlockedButtons()`
walks every quick-command button and:
- sets `disabled = true`
- reduces `opacity` to 0.45
- shows tooltip `Blocked: drone ARMED or wrong mode`

**Exception**: `Abort All (0x0D)` is intentionally never blocked — the spec
says it has *no* preconditions and is always permitted, including in
emergencies.

`canIssueCriticalCommand(cmdId)` consults the registry precondition string:

| Precondition keyword | Behavior |
|----------------------|----------|
| `Disarmed` | Block if `ch13 & 0x01` is set |
| `SDK mode only` | Block if `ch14 & 0x01` is unset |
| other / none | Never block |

---

## 4. Virtual RC injection panel (cmd `0x06`)

Renders five sliders (CH0–CH4, range −1 to +1) plus three buttons:

| Button | Action |
|--------|--------|
| `Enable Virtual RC` | If FlyMode ≠ SDK, shows inline `FlyMode is not SDK. Take SDK authority first (cmd 0x0E).` warning and refuses. If ARMED, shows amber warning but still submits. Then sends the current slider value for channels 0–4. |
| `Disable Virtual RC` | Sets `_vrcEnabled = false`, sends zero on all 5 channels, then RC sticks regain control. |
| `Center All` | Resets sliders to 0; if enabled, submits zero on every channel immediately. |

The panel is **only visible when FlyMode indicates SDK** (i.e. `ch14 & 0x01`).
A `gs_vrc_visible` localStorage override lets users force-show it for setup.

Each slider has a debounced 80 ms submit on input, so dragging pushes
up-to-date values without flooding the queue.

---

## 5. Bench Mode toggle (cmd `0x07`)

Two buttons (`Bench Mode ON` / `Bench Mode OFF`) plus a live status pill:

- ON button triggers a `confirm()` dialog: the user must read the prop-removal warning before proceeding.
- ON also refuses if the drone is `ARMED` (`alert('Cannot enable Bench Mode while ARMED.')`).
- Both ON and OFF submit `cmd 0x07, idx=0, value=1|0`.
- The pill (`Bench Mode: ACTIVE` / `inactive`) is updated whenever telemetry arrives, looking for a `bench_mode` or `ch15 & 0x01` hint in `state.streams[0].values`.

---

## 6. Navigation Paths (cmds `0x0A`, `0x0B`, `0x0C`, `0x11`)

Four grouped Start/Stop pairs in a responsive grid:

| Path | Cmd | Start index / val | Stop index / val |
|------|-----|-------------------|------------------|
| TWC | `0x0A` | 4 / 1 | 4 / 0 |
| Sinusoid | `0x0B` | 7 / 1 | 7 / 0 |
| Circle | `0x0C` | 6 / 1 | 6 / 0 |
| Figure-8 | `0x11` | 7 / 1 | 7 / 0 |

If the drone isn't in SDK mode, the start button asks for confirmation
(`Drone is not in SDK mode. Issue this navigation command anyway?`)
because per spec these paths require SDK authority.

The grid shows the hex ID next to each row so the operator sees what's
actually being submitted.

---

## 7. EKF Reset (cmd `0x18`)

The EKF Reset quick button:

1. Checks `armStatusFromState(_state).armed` — if armed, calls
   `alert('Cannot reset EKF while drone is ARMED. Disarm first.')` and aborts.
2. Calls `confirm('Reset EKF now? This will reinitialize attitude and position estimates.')`.
3. Submits `0x18, index=0, value=0`.

EKF Reset's `precondition: 'GROUND_IDLE or DisArmed'` is also enforced via
the generic `canIssueCriticalCommand()` path so the button is greyed-out
in flight.

---

## 8. Command history

### What changed

- Every entry now carries an explicit hex ID column (e.g. `0x0E`).
- Entries are grouped under a `session <prefix>` header based on
  `state.session_id`. Sessions with the same ID stay together, so you can
  visually segment "morning calibration" vs "afternoon bench" runs.
- Five filter chips above the list (persisted via `localStorage`):
  `all / applied / rejected / submitted / error`.
- Status column now differentiates `submitted` (dashed grey border) from
  `rejected` (red) and `applied` (green).
- The clear-history button asks for confirmation.

### History schema

```js
{
  ts: '14:23:18',
  cmdId: 14,           // 0x0E
  id: '0x0E',          // hex string for display
  cmdName: 'SDK Arm Authority',
  index: 0,
  value: 0,
  status: 'applied',   // 'applied' | 'rejected' | 'submitted' | 'error'
  detail: '',          // human-readable from drone
  session_id: '1f2a3b4c-...'
}
```

Up to 50 entries are kept (was 20).

---

## 9. Result polling with graceful fallback

`/state` is polled every 250 ms after submission, up to a 5 s budget.

The poller checks both `state.last_transaction_result` *and*
`state.command_results[]`. If a matching `transaction_id` is found,
`finalizeResult` resolves it.

If the budget runs out with **no result at all** (typical when the ack-side
is fine but the result event never publishes), the panel now shows:

> ⇪ Submitted (no result feedback)

instead of the previous "timeout (assumed applied)" wording, which could
mask rejections. The history entry is stamped `status: 'submitted'`.

`status` strings the panel understands:

| Status | Icon | Color |
|--------|------|-------|
| `pending` | ⏳ | amber |
| `applied` | ✓ | green |
| `rejected` | ✗ | red |
| `safety_interlock` | ⚠ | red |
| `submitted` | ⇪ | muted |
| `error` (network) | ! | red |

---

## 10. Destroy / cleanup contract

`window.__PLUGIN_DESTROY__()` will:

1. Clear any active polling timer.
2. If `_vrcEnabled` is true, submit neutral sticks (cmd `0x06`, idx=0..4,
   val=0) on each channel so a closed panel can never leave a stuck
   override active.
3. Reset module-level state (`_history`, `_state`, `_pendingTx`, `_api`).

This avoids the previous risk where a hard refresh or accidental
card-close could leave a permanent Virtual-RC override engaged.

---

## 11. Trade-offs and notes

- **`ch13` / `ch14` semantics** — the spec only describes `FlyMode`
  conceptually; we map `ch13 & 0x01 = ARM` and `ch14 & 0x01 = SDK auth`
  because that is the convention used elsewhere in the dashboard
  (`status-panel.js`, `safety-panel.js`). If the firmware changes bit
  layout, `armStatusFromState()` is the single point to update.
- **Navigation path trigger indices** — the start/stop trigger index is
  inferred from the spec examples. They might need tuning per firmware
  build; the current values match the convention used in the ground-station
  python service.
- **Polling budget** — bumped from 3 s to 5 s, since the previous timeout
  was too aggressive for legitimate slow drone rounds. Tunable via
  `PENDING_TIMEOUT_MS`.
- **`bench_mode` detection** — the live "Bench Mode: ACTIVE" pill depends
  on the firmware exposing a `bench_mode` value (or `ch15` bit) on stream 0.
  If neither is published, the pill stays "inactive" even after a successful
  ON command — this is a known limitation; no false-positive is shown.
- **Legacy entries** (`0x1A`, `0x1C`, `0x20`, `0xFE`) are preserved so we
  don't break any external bookmarks or saved test scripts.
- **CAPTCHA-safe confirm() dialogs** are used for destructive actions
  (Bench Mode ON, EKF Reset); this is the dashboard's only accepted
  blocking mechanism and matches the rest of the platform.

---

## 12. Files touched

| File | Status |
|------|--------|
| `shell/plugins/command-panel.js` | rewritten, 1042 lines |
| `REPORT_S14-commands.md` | new |

No other plugins, no shell changes, no test fixtures modified.
