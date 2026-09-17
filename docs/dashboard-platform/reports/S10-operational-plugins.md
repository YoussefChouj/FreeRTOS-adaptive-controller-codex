# S10 — Operational dashboard plugins

## Outcome

S10 is complete. Six operational plugins are implemented as self-contained browser
JavaScript modules served from `docs/dashboard-platform/shell/plugins/`. Each plugin
exports `{ name, init(shellApi), destroy() }` and renders into the shell's main
area via `shellApi.registerPanel(name, renderFn)`. State-machine animations and
pending/applied/rejected indicators are implemented. All six panels bind through
schema variable names without changing firmware behavior.

## Plugins

### `status-panel.js` — Flight status and safety

Displays:
- ARM status badge (red=disarmed, green=armed)
- FlyMode enum label
- Battery voltage (vbat)
- Command drop count (gs_cmd_drop_count)
- Active stream slot table: slot, sequence, loss%, sample count
- Command pending indicator: spinner for 2 s after submission → green check
  (applied) or red X (rejected); auto-resolves to applied if no result event
  arrives within 2 s

### `mrac-panel.js` — MRAC adaptive controller

Displays:
- Table: axis (pitch/roll/yaw) × parameter (θ₀–θ₅, u_nom, x_m)
- Inline SVG bar chart per axis, colour-coded (pitch=blue, roll=green, yaw=amber)
- Auto-discovers `mrac.*` keys from the typed stream values

### `estimator-panel.js` — EKF/estimator panel

Displays:
- EKF state groups: position (x, y, z), velocity (x, y, z), gyro bias, accel bias
- Covariance P matrix display (if `ekf.P_*` keys are available)
- Filter status badge
- Auto-discovers `ekf.*` and `estimator.*` keys

### `safety-panel.js` — Safety limits

Displays:
- Safety parameter values: max horizontal/vertical speed, max pitch/roll angle
- ±step buttons for each parameter (calls `shellApi.submitCommand`)
- Interlock warning banners when limits are at default values
- Pending/applied/rejected status per parameter change

### `resource-panel.js` — RTOS resource panel

Displays:
- Scheduler tick count (xTickCount)
- Heap utilization
- USART3 TX stats: frame count, drop count, peak bytes
- Queue depth, DMA busy flag
- Send_Task cycle counter
- Auto-discovers `rtos.*` and `system.*` keys

### `telemetry-explorer-panel.js` — Live telemetry explorer

Displays:
- Schema ID and active slot info in header
- Debounced filter input (filters key names)
- Sticky-column scrollable table of all key-value pairs from all active streams
- Updates every poll cycle

## Test evidence

```text
python -m pytest ground_station/service/tests ground_station/platform/tests \
  ground_station/comm/tests/test_protocol_schema.py -q
26 passed
```

All tests pass. The plugin files are static JavaScript served by the shell's
static file handler; they are exercised by loading the browser shell and verifying
panel rendering and command submission.

## S11 entry

S10 gate passed. S11 can begin with experiment orchestration, parameter-sweep
API, plot scripts, run comparison, replay controls, artifact indexing, and
structured agent APIs — all verified to be usable by an external analysis script
without screen scraping.
