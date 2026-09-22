# Task: dashboard UI fixes part D, layout (operator walkthrough 2, 2026-09-22)

Scope: `docs/dashboard-platform/shell/` (index.html, shell JS/CSS, plugins/*.js) plus tests
in `ground_station/service/tests/`. Tier 2.
Do NOT edit: firmware (`API/ TASK/ BSP/ USER/`), `ground_station/service/agent.py`,
`bandwidth-panel.js`, `slot-manager-panel.js`, `command-panel.js` (another worker owns them).
Do NOT POST to the live service on 8081. Read `docs/dashboard-platform/AGENT_GUIDE.md` and
`PLUGIN_DEVELOPER_GUIDE.md` first. Keep all existing uncommitted edits; revert nothing.

Items. For each one, report the root cause, file:line changed, and how you verified it:

1. Sidebar: after hiding the sidebar once and pressing the "show sidebar" button, the
   sidebar re-opens EMPTY. Find why its contents are lost (innerHTML cleared? panels
   re-parented? display state?) and fix so hide/show round-trips with all contents.
   Add a harness check: hide, show, assert the same child count/testids.
2. Overview "data flow" block diagram: only "attitude estimates" and "MRAC augmentation"
   blocks show data; the other 5 blocks stay empty. For each block, find which symbols it
   reads. If the symbol exists in telemetry under another name, fix the mapping. If it is
   not published, show "not published: in preset X" (reuse GET /api/preset-for-symbol)
   instead of a blank block. List each block -> symbol -> cause in your result.
3. The Activity section (long list of collapsible entries) must appear ONLY in the
   Approvals tab. Remove it from every other workspace/tab.
4. Move the agent mode pill ("agent unknown") and the agent Stop button out of the header
   into a small fixed strip at the bottom-centre of the viewport, so they never cover the
   tab names. Must not cover panel content at phone width either (add bottom padding).
   Also fix why the pill says "unknown" when /api/agent/control returns a mode.
5. Co-pilot drawer: when the operator sends "hi", the history shows "operator · agent · #1
   hi" then "agent · message · #2124 hi". The operator's own message is rendered a second
   time as an agent message. Find where (SSE `message`/`activity` events carrying
   source=operator) and render each operator message once, labelled operator. Do NOT
   build any reply logic; that is a separate task.

Verification: `python -m pytest ground_station/service -q` and every
`node ground_station/service/tests/*harness*.js` must stay green (one known flake:
test_agent.py test_mode_off_cancels_running_plan_and_returns_423). Paste the final pytest
line and harness results verbatim. Exit cleanly when done.
