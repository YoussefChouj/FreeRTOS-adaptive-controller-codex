
# WP-5 brief (CEO -> Manager), 2026-10-01. Workflow B task G14: Campaign dashboard panel + JS harness.

Base: `workflow-b` @ 894e681 (gate with `--base workflow-b`). Spec: `.agent-ops/grill-autonomous-flight-loop.md`
summary item 1 (line 254) and A4 (line 298); plan: `docs/workflow-b/build-plan.md` Task 14 (line 218) + amendment G14
(line 314): per-battery go (pack ID + checklist), Pause / Land / Abort always visible.

Goal: the operator's control surface for the G12 runner, using only the G13 HTTP routes merged in WP-4.
No backend change. Tests are offline (Node harness with fake DOM + fake fetch), never 8081.

Facts the CEO measured on 2026-10-01 (workflow-b @ 894e681):
- Routes (`ground_station/service/api.py` 483, 527-530; `service/campaign_api.py`):
  GET `/api/campaign/state` -> `{status, waiting_pack, campaign_path, reason, flights, control}`; status is one of
  idle | running | waiting_for_go | complete | operator_stop | operator_needed | gate_refused | arm_refused | error.
  `flights` items: `{flight_id, pack_id, experiment, j, abort_level, abort_reason, decision, hover_only, duration_s,
  reflash_hash}`. POST `/api/campaign/go` `{campaign_path, pack_id, checklist, source}` -> 400 no source, 403 `agent:`
  source, 409 checklist not all true or busy, 503 no runner deps (true today outside tests), else 200 + state.
  While a run waits for a pack (`waiting_for_go`), a go with that pack_id releases it. POST `/api/campaign/pause|land|abort`
  `{source}` -> 200 `{ok: true}` or an error body `{error}`.
- `allow_agent_arm`: GET `/api/agent/control` -> `{mode, allow_agent_arm, ...}`; POST `/api/agent/control`
  `{allow_agent_arm: bool, source: 'operator'}`. Pattern to copy: `docs/dashboard-platform/shell/plugins/approval-queue.js`
  lines 74-87 (window.confirm before a dangerous grant, fetch POST, source 'operator') and `header-mode-pill.js`.
- Plugins register with `window.__registerPlugin__(name, function (api) {...})`. A plugin is listed in
  `docs/dashboard-platform/shell/index.html` (script list near line 686, panel-to-workspace map near line 943) and in
  `ground_station/platform/capability_manifest.py` (workspace map ~460, `plugin_files` ~469, `keys_read_map` ~491);
  then regenerate `docs/dashboard-platform/capability_manifest.json` with that script
  (`platform/tests/test_capability_manifest.py::test_capability_manifest_no_drift` checks it).
- Harness pattern: `ground_station/service/tests/motor_bench_panel_harness.js` (fake DOM + stubbed shell api, prints
  `ALL CHECKS PASSED`, exit 0) wrapped by `test_motor_bench_panel.py`; `node_harness.js` mocks fetch for the agent plugins.
  `test_all_panels_audit.py` loads every `shell/plugins/*.js`, so the new panel must pass it too.
- `.agent-ops/served/*` is a served copy: do not edit it.

Wanted:
1. `docs/dashboard-platform/shell/plugins/campaign-panel.js`, panel name `Campaign`, workspace `experiments`:
   - Campaign path text field and pack ID field. When status is `waiting_for_go`, show "Waiting for pack <id>" and prefill
     the pack ID with `waiting_pack`.
   - Checklist (checkboxes) from spec item 1: pack swapped and labelled; drone at pad centre, nose to the marked end wall;
     powered on in place (OF frame aligned); RC transmitter on and in reach (ch10 kill); phone clamped and recording;
     operator stays in the room. Keys are short snake_case ids; all ticks reset after each successful go (per battery).
   - Go button: disabled unless path and pack ID are non-empty and every box is ticked; click -> one POST
     `/api/campaign/go` with `source: 'operator'` and the checklist as `{id: true, ...}`.
   - Pause / Land / Abort: always rendered in every status (idle included), never hidden, no confirm dialog; each click
     -> one POST to its route with `source: 'operator'`. Abort is visually the strongest.
   - `allow_agent_arm` toggle showing the current value from `/api/agent/control`; turning it on needs `window.confirm`;
     confirm false -> no POST.
   - Status line (status, reason, control) and a queue-progress table, one row per flight (flight_id, pack_id,
     experiment, j, decision, abort_level/abort_reason, hover_only).
   - Any non-200 reply shows its `error` text in the panel. Poll `/api/campaign/state` about once a second while mounted;
     stop the timer on teardown (follow the lifecycle used by `approval-queue.js` / `experiment-panel.js`).
   - The panel never sends drone commands (no `submitCommand` / `gatedCommand`); it talks only to the routes above.
2. Register it: `index.html` script list + workspace map; `capability_manifest.py` workspace map + `plugin_files`
   + `keys_read_map` (empty list); regenerate `capability_manifest.json`.
3. `ground_station/service/tests/campaign_panel_harness.js` (fake DOM, fake fetch recording method/url/body, fake
   timers, stub api whose `submitCommand`/`gatedCommand` record calls) and `test_campaign_panel.py` (wrapper like
   `test_motor_bench_panel.py`). One named check line each, printed before `ALL CHECKS PASSED`:
   a. registers as `Campaign`; b. go disabled with empty pack ID, and with any one box unticked (try each box);
   c. all set -> exactly one POST go with the exact body; d. Pause/Land/Abort present in idle, running, waiting_for_go,
   operator_needed and error states; e. each of them -> one POST to its own route with source operator;
   f. waiting_for_go shows the pack and prefills it; g. flights render one row each; h. 409 and 503 error text shown;
   i. allow_agent_arm: confirm false -> no POST, confirm true -> POST `{allow_agent_arm: true, source: 'operator'}`;
   j. after teardown no further fetch; k. zero `submitCommand`/`gatedCommand` calls over the whole run;
   l. ticks reset after a successful go.

Worker rules: one statement per line, no `;`-joined statements; no new files outside the gate allow-list (no scratch
scripts at the repo root, no files under `.agent-ops/served/`); do not reformat code you did not need to touch.

Acceptance (run by you):
- First, on the base: `python -m pytest -q -p no:cacheprovider ground_station/platform/tests/test_capability_manifest.py ground_station/service/tests/test_all_panels_audit.py ground_station/service/tests/test_campaign_api.py ground_station/service/tests/test_motor_bench_panel.py` -> note the last line; and `node ground_station/service/tests/node_harness.js` -> note the exit code.
- `node ground_station/service/tests/campaign_panel_harness.js` -> exit 0, prints `ALL CHECKS PASSED` and the 12 check lines a-l
- `python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_panel.py` -> 0 failed
- the first two commands again -> no new failures vs the base
- `python .agent-ops/gate.py --base workflow-b --max-lines 1000 --allow docs/dashboard-platform/shell/plugins/campaign-panel.js --allow ground_station/service/tests/campaign_panel_harness.js --allow ground_station/service/tests/test_campaign_panel.py --allow docs/dashboard-platform/shell/index.html --allow ground_station/platform/capability_manifest.py --allow docs/dashboard-platform/capability_manifest.json --allow ".agent-ops/out/*" --allow "docs/agent/reports/*" --allow ".agent-ops/tasks-src/*"` -> GATE PASS

Scope (worker may edit): the files in the gate line above, its digest `.agent-ops/out/wp5-r<n>.md`
Allow globs: as the gate line.
Worker lane: agy-vps, chain `agy:gemini-3.1-pro-high,agy:gemini-3.8-flash-high`   Max worker rounds: 3
Report to: `docs/agent/reports/WP-5.md`; add one line: any MANAGER.md rule that was unclear or a denied command.

## CEO decision 2026-10-01, after BLOCKED (scope only)
- Accepted the one-line panel-count change 18->19 in ground_station/platform/tests/test_capability_manifest.py (a new panel must change it).
  CEO rerun on wp/5 @ 191b252: harness 12 checks a-l + ALL CHECKS PASSED rc 0; node_harness ALL GREEN rc 0; scoped pytest 17 passed;
  gate with the extra --allow for that test file -> GATE PASS (732/1000, 14 files). Merged ff into workflow-b.
- Pre-existing, not WP-5: test_all_panels_audit fails on Windows (subprocess.run without encoding=, cp1252 decode) on base too.
