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

Files you may edit: shell/index.html, plugins/approval-queue.js, plugins/activity-timeline.js,
plugins/header-mode-pill.js, plugins/time-series-panel.js, plugins/overview-panel.js (layout only).
Another worker edits other plugins at the same time: touch nothing else.

Fix each item. For each, report file:line changed and how you verified it.

1. Sidebar "hide" button hides the sidebar with no way back. Add a visible re-open
   control, and let the main tab area grow to the freed width (reflow/resize plots).
2. The Approvals panel is injected into every tab. Move it to its own dedicated tab;
   keep a small pending-count badge in the header/sidebar so approvals stay visible.
3. Text overlap: block diagram top-right status text ("no data" / "live") and the
   agent activity log rows. Fix layout (no overlapping at 1280px and at phone width).
4. Trend-on-demand mini plots: add an expand-to-fullscreen toggle and shrink back.

Verification: `python -m pytest ground_station/service -q` and the node harnesses under
`ground_station/service/tests/` must stay green; add/adjust harness tests for items
1, 2, 5. Paste the final pytest summary line and harness result verbatim.
Exit cleanly when done.
