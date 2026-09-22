> RESUME NOTE (2026-09-23): a previous worker died midway on this task. It left UNVERIFIED partial edits in bandwidth-panel.js, command-panel.js, slot-manager-panel.js, ground_station/comm/wifi_bridge.py, comm/tests/test_wifi_bridge_stream.py and livewatch/manifests.yaml. Read `git diff` for those files first and keep, fix or revert each hunk. Do not touch api.py's POST guard or _drain_body. POSTs must send Content-Type: application/json.

# Task: dashboard part E, publishing, WiFi budget, subscribe UX (2026-09-22)

Scope: `bandwidth-panel.js`, `slot-manager-panel.js`, `command-panel.js`,
`telemetry-explorer-panel.js` in `docs/dashboard-platform/shell/plugins/`; service code in
`ground_station/service/` EXCEPT `agent.py`; `ground_station/comm/` only if a subscribe bug
lives there; `ground_station/livewatch/multi_slot_presets.yaml` (add entries, never remove).
Tests go in the matching `tests/` dirs. Tier 2. Do NOT edit firmware (`API/ TASK/ BSP/ USER/`).
Do NOT POST to the live service on 8081, and do not run anything that sends a subscribe to
the drone. Read `docs/dashboard-platform/AGENT_GUIDE.md`, `docs/telemetry-protocol.md` and
`PLUGIN_DEVELOPER_GUIDE.md` first. Keep other uncommitted edits; only the partial edits named in the RESUME NOTE may be reverted.

Items. For each one, report the root cause, file:line changed, and how you verified it:

1. Subscribing new slots does not work. Trace the full path from the slot UI to the
   service route, the subscribe frame builder and the ack/schema (0x08) handling. Find
   the bug(s) with a failing unit test first (fake transport, no drone), then fix.
   Redesign the slot UI into one clear flow: pick symbols or a preset -> choose rate ->
   see projected bytes/s against the budget -> Apply -> per-slot state
   (pending / acked / live / error with reason). Remove duplicate or dead controls.
2. Live WiFi budget: show the remaining link budget live (used vs capacity in B/s, and
   in %), computed from actual received telemetry bytes/s, not only the plan. State the
   capacity assumption and where it comes from (docs/telemetry-protocol.md or code) in
   a comment. Show it in the header area or the bandwidth panel, updating at least 1 Hz.
3. Command flags show "requested ON · unconfirmed (not published)" because the flag
   state symbols are not in any subscribed slot. Make the flag state observable: find
   the firmware symbols for the flags (read-only), make sure a preset carries them, and
   give the Command panel a one-click "publish flag state" using the fixed subscribe
   path from item 1 (operator-clicked only; never automatic).
4. Many other dashboard values show "not published". Produce a table in your result:
   panel -> symbol -> in which preset (or none). Add the missing symbols to a sensible
   preset in multi_slot_presets.yaml where the struct/size is readable (1/2/4 B), and
   make sure the combined default subscription fits the WiFi budget.

Verification: `python -m pytest ground_station -q` (or service+comm+livewatch test dirs)
and every `node ground_station/service/tests/*harness*.js` must stay green (one known
flake: test_agent.py test_mode_off_cancels_running_plan_and_returns_423). Paste the final
pytest lines and harness results verbatim. Exit cleanly when done.
