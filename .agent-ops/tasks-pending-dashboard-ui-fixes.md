# Task: dashboard UI fixes from operator walkthrough (2026-09-22)

Scope: dashboard front end only — `docs/dashboard-platform/shell/` (shell + `plugins/*.js`)
and, only if a fix needs it, read-only service routes in `ground_station/service/`.
Tier 2. Do NOT touch firmware (`API/ TASK/ BSP/ USER/`), `agent.py` approval logic,
or POST to the live service on 8081. Read `docs/dashboard-platform/AGENT_GUIDE.md` and
`PLUGIN_DEVELOPER_GUIDE.md` first. Use `python -m ground_station.agent_map explain <name>`
before grepping firmware names.

Fix each item; one commit-free change set. For each, report: file:line changed and how
you verified it.

1. Sidebar "hide" button hides the sidebar with no way back. Add a visible re-open
   control, and let the main tab area grow to the freed width (reflow/resize plots).
2. The Approvals panel is injected into every tab. Move it to its own dedicated tab;
   keep a small pending-count badge in the header/sidebar so approvals stay visible.
3. Text overlap: block diagram top-right status text ("no data" / "live") and the
   agent activity log rows. Fix layout (no overlapping at 1280px and at phone width).
4. Trend-on-demand mini plots: add an expand-to-fullscreen toggle and shrink back.
5. Checkboxes on MRAC gamma, width limits, GS safety limits, gyro filters, real-time
   adaptation flags: clicking clears/removes them instead of toggling. Find and fix the
   render/state bug (probably re-render wiping the element when the value is
   "not published"). A control whose value is unknown must stay visible and togglable
   only if the command path allows it; otherwise show it disabled with the reason.
6. Path tab plot renders as black/white slices with blue in the middle. Fix the plot.
7. Attitude view: yaw does not rotate / heading vector does not update. Fix.
8. Battery trend: slope computes but shows "No image". Fix the render.
9. Recorder: on Stop, show the absolute path of the saved log file.
10. "Note / goal marker" dropdown: add a one-line helper text stating what it does
    (read the code to find out: does it name the log, or tag a marker?).
11. MRAC adaptive weights panel: add sort/rank by convergence (e.g. |dW/dt| over the
    last N s, or variance) so drifting weights sort to top; must scale beyond 6 weights.
12. PID gains panel: group inner loop (rate: gyrox=roll, gyroy=pitch) vs outer loop
    (angle) gains, and show the firmware default next to the current value
    (defaults: read from firmware source, cite file:line).

Do NOT fix (report findings only, one paragraph each, if you notice the cause):
"not published" values (angular rates, safety limits, PID gains, ZRAT weights, bench
indicator), data-flow diagram showing 2/7 blocks, arm/Emergency state issues.

Verification: `python -m pytest ground_station/service -q` and the node harnesses under
`ground_station/service/tests/` must stay green; add/adjust harness tests for items
1, 2, 5. Paste the final pytest summary line and harness result verbatim.
Exit cleanly when done.
