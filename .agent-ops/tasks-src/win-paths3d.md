# Task: paths3d (Phase B & C & E)

## Files to touch
- `docs/dashboard-platform/shell/plugins/path-panel.js`
- `ground_station/service/tests/test_path_panel.py` or JS tests
- `test_three_vendor.py`

## Acceptance checks
1. **Desired trace from telemetry:** Build a second trail from the firmware desired-position fields that the flight presets stream. Keep cm->m units consistent with the actual trail. Draw it in a distinct colour, dashed or thinner. Hand-placed waypoints remain a separate "plan" overlay.
2. **Line quality:** Get thick, antialiased lines working with the global `THREE` script-tag loading this shell uses: either port Line2/LineMaterial to non-module scripts and load them in the shell HTML, or delete the vendored files and use a different approach (like a tube/ribbon mesh). No CDN. Update `test_three_vendor.py` to match.
3. **Metrics:** Compute RMS error, max error and per-axis (x/y/z) RMS between time-aligned actual and desired samples. Show them in the metrics overlay, live and while scrubbing a recording.
4. **Tests:** Extend `path_panel_harness.js` or `test_path_panel.py` with checks for items 1 and 3, using fed values with known answers.

## Do not rules
- Do not use a CDN for three.js files.
- Do not mix units (actual and desired should both be normalized to meters).
- Do not flash or arm the drone.

## Digest
Write digest to `.agent-ops/out/win-paths3d.md`.
