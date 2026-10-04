Status: DONE (one gate exception, see Gate). Do not merge before the lab day ends (brief).
Commits: 25edb63 design system + strip + panels + tests + docs; + this report (on wp/35, base wp/32)
Gate: brief command gives PASS scope (23 files), PASS ruff, SKIP clang-tidy, WARN pytest (none mapped), **FAIL size
  2257/200**. A redesign cannot fit 200 lines: kit 350, harness 401, strip 217, CSS 227, campaign/estimator rewrites. With
  the same command plus `--tests` (11 dashboard test files): PASS pytest 39 passed. Waiving size is the CEO's call.
Verification (laptop, this run; nothing touched 8081, serial, Keil or OBJ/):
- `node ground_station/service/tests/campaign_panel_harness.js` -> a..r, `ALL CHECKS PASSED`
- `node ground_station/service/tests/ui_components_harness.js` (new) -> K1-K13 S1-S7 H1 H2 L1-L3, `ALL CHECKS PASSED`
- 26 dashboard pytest files (panels, shell, workflow-B, preflight, path, overview...) -> `1 failed, 103 passed, 6 skipped`.
  The failure is `test_t5::test_opencode_json_exists` (no opencode.json in this worktree), and it failed on the base before
  any change. No `ground_station/service/*.py` was touched: static serving already maps `/ui/*` (test_ui_components checks MIME).
Worker rounds: CTE, effort xhigh (no manager or workers, per the brief).
Changed panels (the brief's substitute for before/after screenshots):
- NEW flight strip (`plugins/flight-strip.js`, `#flight-strip` above the tabs, outside #app). Shows ARM, MODE, BAT (value +
  age, no invented cutoff), RC, LINK age (ok <1 s, amber <3 s, red), campaign pill + banner, then Pause/Land/Abort and Theme.
- Campaign: everything on tokens/classes, banner by status token, preflight `gs-table` + pills, flights empty state.
  Go shows what is missing ("tick 2 checklist items"). Pause/Land/Abort are disabled with the reason when no run is
  active and stay enabled when the state poll fails. Poll moved to GSUI.poller with backoff. Errors in panel + toast.
- Estimator: inline style soup and the `<style>` hex block replaced by tokens/classes, inline onclick replaced by bound
  handlers. Command failures now show in `#est-error` (they were console only). Fixed a bug: with arm state unknown
  (shell returns null) the mode buttons were disabled with no reason shown; now "Arm state unknown ... fail-closed".
- Approvals: gs classes/tokens, armed confirm button, reason when agent mode is off. Command panel: two-click is now
  `GSUI.confirmClick`. Motor bench: two `alert()` calls replaced by in-panel line + toast.
- index.html: tokens/components/kit linked, inline `:root` removed, reload banner and all hex/rgba moved to tokens. Every
  empty or comment-only catch now goes through `shellWarn` -> GSUI.report. The 8 SSE listeners are now one loop.
Decisions: Abort/Land/Pause stay single click everywhere (campaign harness e: "never delayed"). That deviates from the
  WP-32 D1 hold-to-confirm; two-click is reserved for actions that add risk. P = Pause (R/N/F/T/1-4 are taken by the path panel).
Fixed in tests: all_panels_audit failed on base (preset-picker.js registers nothing); it now accepts self-installing chrome.
  Harnesses preload the kit via `tests/ui_kit_loader.js`.
Not verified: eslint config was not run (eslint not installed, npm needs approval); L1-L3 enforce the same rules without npm.
  `verify_honest_panels.js` (manual, not in pytest) still stops at `command-panel.js:2332 row.closest`. That is untouched code
  and a fake-DOM gap, pre-existing by inspection. No real browser render was checked (headless).
Left (detail in `docs/dashboard-platform/ux-audit-2026-10-04.md`): sidebar Flight Status + status-panel duplicate ids, dead
  `#card-cmd`, Streams/Slot/Bandwidth overlap, tokens for overview/status/safety/path/time-series/telemetry panels, WP-32 D2/D3/D5.
