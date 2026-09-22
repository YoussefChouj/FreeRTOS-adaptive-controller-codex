# Task: dashboard UI fixes part C (operator walkthrough 2026-09-22)

Scope: `docs/dashboard-platform/shell/plugins/*.js` and, only if needed, a read-only
service route in `ground_station/service/` (plus its tests). Tier 2.
Do NOT edit: firmware (`API/ TASK/ BSP/ USER/`, read-only for defaults), `agent.py`,
`index.html`, `command-panel.js` flag-toggle code, `path-panel.js`. Do NOT POST to the
live service on 8081. Read `docs/dashboard-platform/AGENT_GUIDE.md` and
`PLUGIN_DEVELOPER_GUIDE.md` first. Use `python -m ground_station.agent_map explain <name>`
before grepping firmware names. Other files already have uncommitted edits from earlier
workers; keep them, do not revert anything.

Items (for each: file:line changed + how verified):

9. Recorder: on Stop, show the absolute path of the saved log file in the UI. If the
   stop response lacks it, add it to the service response (read-only data, add a test).
10. "Note / goal marker" dropdown: add one line of helper text saying what it actually
    does (read the code; does it name the log, or tag a marker in the recording?).
11. MRAC adaptive weights panel: add sort/rank by convergence (|dW/dt| over the last N s,
    or variance) so drifting weights sort to top; must work for any number of weights.
    Add a toggle between natural order and ranked order.
12. PID gains panel: group inner loop (rate: gyrox = roll, gyroy = pitch) vs outer loop
    (angle) gains, and show the firmware default next to each current value. Defaults
    come from firmware source: cite file:line in a code comment for each default.
14. "Not published" values: where a panel shows "not published", add a hint naming the
    preset in `ground_station/livewatch/multi_slot_presets.yaml` that carries the symbol
    (e.g. PRESET_IMU_PID, PRESET_MRAC_FULL). Compute the mapping from the YAML (served by
    an existing or new read-only GET route), not hardcoded. Do not add a subscribe button.

Verification: `python -m pytest ground_station/service -q` and every
`node ground_station/service/tests/*harness*.js` must stay green; add harness checks
for items 11 and 14. Paste the final pytest summary line and harness results verbatim.
Exit cleanly when done.
