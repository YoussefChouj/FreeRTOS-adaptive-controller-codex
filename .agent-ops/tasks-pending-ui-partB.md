# Task: dashboard UI fixes from operator walkthrough (2026-09-22)

Scope: dashboard front end only — `docs/dashboard-platform/shell/` (shell + `plugins/*.js`)
and, only if a fix needs it, read-only service routes in `ground_station/service/`.
Tier 2. Do NOT touch firmware (`API/ TASK/ BSP/ USER/`), `agent.py` approval logic,
or POST to the live service on 8081. Read `docs/dashboard-platform/AGENT_GUIDE.md` and
`PLUGIN_DEVELOPER_GUIDE.md` first. Use `python -m ground_station.agent_map explain <name>`
before grepping firmware names.


A previous worker ran out of turns on this. Its partial, unverified edits are in
`.worktrees/20260922-165309/` (read-only reference; compare with `diff --strip-trailing-cr`).
Reuse what is correct; edit only the repo-root files.

Files you may edit: plugins/mrac-panel.js, plugins/safety-panel.js, plugins/estimator-panel.js,
plugins/path-panel.js, plugins/status-panel.js, plugins/motor-bench-panel.js, plugins/overview-panel.js
(attitude/battery render only). NEVER edit shell/index.html: another worker owns it.

Fix each item. For each, report file:line changed and how you verified it.

5. Checkboxes on MRAC gamma, width limits, GS safety limits, gyro filters, real-time
   adaptation flags: clicking clears/removes them instead of toggling. Find and fix the
   render/state bug (probably re-render wiping the element when the value is
   "not published"). A control whose value is unknown must stay visible and togglable
   only if the command path allows it; otherwise show it disabled with the reason.
6. Path tab plot renders as black/white slices with blue in the middle. Fix the plot.
7. Attitude view: yaw does not rotate / heading vector does not update. Fix.
8. Battery trend: slope computes but shows "No image". Fix the render.
13. Motor bench panel: firmware only drives bench motors while the FSM is DISARMED plus a
    dead-man heartbeat (TASK/StabilizerTask.c:583-599). Any UI text or flow telling the operator to
    send Arm authorization before bench mode is wrong: remove it and state 'drone must stay
    DISARMED, kill switch (ch9) high'.

Verification: `python -m pytest ground_station/service -q` and the node harnesses under
`ground_station/service/tests/` must stay green; add/adjust harness tests for items
1, 2, 5. Paste the final pytest summary line and harness result verbatim.
Exit cleanly when done.
