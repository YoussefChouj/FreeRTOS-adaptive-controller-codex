<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only. No new files outside it: no scratch
   scripts at the repo root, nothing under `.agent-ops/served/`.
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. One statement per line; never join statements with `;` on one line (JS statement-terminating `;` is fine).
4. After each Python edit run `python -m py_compile <file>`; after each JS edit run `node --check <file>`.
5. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" / "ALL CHECKS PASSED" in the output.
6. Do not reformat code you did not need to touch. Keep every existing function name and signature.
7. No backend change: do not edit anything under `ground_station/service/` except the two new test files.
8. If a command or edit fails twice the same way, change approach; never repeat an identical call.
9. Write the digest `.agent-ops/out/wp5-r1.md` BEFORE printing DONE.
10. Output: no preamble, no summary prose.
</guardrails>

<context>
Build the operator dashboard panel "Campaign" for the autonomous campaign runner, using only existing HTTP routes.
Measured on base `workflow-b` @ 894e681:
- Routes (`ground_station/service/api.py` ~483 and ~527-530; `ground_station/service/campaign_api.py`):
  GET `/api/campaign/state` -> `{status, waiting_pack, campaign_path, reason, flights, control}`. status is one of
  idle | running | waiting_for_go | complete | operator_stop | operator_needed | gate_refused | arm_refused | error.
  `flights` items: `{flight_id, pack_id, experiment, j, abort_level, abort_reason, decision, hover_only, duration_s,
  reflash_hash}`.
  POST `/api/campaign/go` body `{campaign_path, pack_id, checklist, source}` -> 400 no source, 403 `agent:` source,
  409 checklist not all true or busy, 503 no runner deps, else 200 + state.
  POST `/api/campaign/pause`, `/api/campaign/land`, `/api/campaign/abort` body `{source}` -> 200 `{ok: true}`
  or an error body `{error}`.
- GET `/api/agent/control` -> `{mode, allow_agent_arm, ...}`; POST `/api/agent/control`
  `{allow_agent_arm: bool, source: 'operator'}`. Pattern to copy: `docs/dashboard-platform/shell/plugins/approval-queue.js`
  lines 74-87 (window.confirm before a dangerous grant, fetch POST, source 'operator') and `header-mode-pill.js`.
- Plugin lifecycle (see `experiment-panel.js` 428-439, 499-523 and `motor-bench-panel.js` 561, 619, 637):
  `api.registerPanel(name, function (container) {...})` inside an init function; teardown is the third argument of
  `window.__registerPlugin__(name, init, destroy)`; experiment-panel uses setInterval/clearInterval for polling.
- Registration points: `docs/dashboard-platform/shell/index.html` script list (line 686 `'/plugins/experiment-panel.js',`)
  and panel-to-workspace map (line 943 `'Experiment Runtime':    { workspace: 'experiments' },`);
  `ground_station/platform/capability_manifest.py` workspace map (line 460), plugin_files tuples (line 478),
  `keys_read_map` (line 491). Regenerate the JSON with `python ground_station/platform/capability_manifest.py`
  (writes `docs/dashboard-platform/capability_manifest.json`); `--check` reports drift.
- Harness pattern: `ground_station/service/tests/motor_bench_panel_harness.js` (fake DOM + stubbed shell api, prints
  check lines then `ALL CHECKS PASSED`, exit 0) wrapped by `ground_station/service/tests/test_motor_bench_panel.py`.
  `ground_station/service/tests/test_all_panels_audit.py` loads every `shell/plugins/*.js`; the new panel must pass it.
- On the Windows laptop, `subprocess.run(..., text=True)` without an encoding decodes as cp1252 and crashed
  test_all_panels_audit.py with UnicodeDecodeError. Your wrapper must pass `encoding="utf-8"`.
</context>

<allow-list>
docs/dashboard-platform/shell/plugins/campaign-panel.js        (new)
ground_station/service/tests/campaign_panel_harness.js         (new)
ground_station/service/tests/test_campaign_panel.py            (new)
docs/dashboard-platform/shell/index.html
ground_station/platform/capability_manifest.py
docs/dashboard-platform/capability_manifest.json               (regenerated only, never hand-edited)
.agent-ops/out/wp5-r1.md                                       (digest)
</allow-list>

<spec>
1. `campaign-panel.js`, panel name `Campaign`, workspace `experiments`:
   - Text field for the campaign path and one for the pack ID. When status is `waiting_for_go`, show
     "Waiting for pack <waiting_pack>" and prefill the pack ID field with `waiting_pack`.
   - Checklist checkboxes with these ids and labels:
     pack_swapped "Pack swapped and labelled";
     drone_on_pad "Drone at pad centre, nose to the marked end wall";
     powered_in_place "Powered on in place (OF frame aligned)";
     rc_ready "RC transmitter on and in reach (ch10 kill)";
     phone_recording "Phone clamped and recording";
     operator_present "Operator stays in the room".
     All ticks reset after each successful (200) go.
   - Go button: disabled unless path and pack ID are non-empty (trimmed) and every box is ticked. Click -> exactly one
     POST `/api/campaign/go` with JSON body `{campaign_path, pack_id, checklist: {<id>: true, ...all 6}, source: 'operator'}`.
   - Pause / Land / Abort buttons: rendered in every status (idle included), never hidden or disabled, no confirm
     dialog. Each click -> exactly one POST to `/api/campaign/pause|land|abort` with body `{source: 'operator'}`.
     Abort is visually the strongest (e.g. red, larger).
   - `allow_agent_arm` checkbox/toggle showing the current value from GET `/api/agent/control`. Turning it on calls
     `window.confirm(...)` first; false -> no POST. Confirmed on, or turning off (no confirm), -> POST
     `/api/agent/control` `{allow_agent_arm: <bool>, source: 'operator'}`.
   - Status line showing status, reason and control; queue-progress table with one row per flight
     (flight_id, pack_id, experiment, j, decision, abort_level/abort_reason, hover_only).
   - Any non-200 reply shows its `error` text (fallback: HTTP status) in a visible error element in the panel.
   - Poll GET `/api/campaign/state` every 1000 ms while mounted; the destroy function clears the timer so no fetch
     happens after teardown.
   - Never call `api.submitCommand` or `api.gatedCommand`; talk only to the routes above via `fetch`.
2. Register: index.html script list (after experiment-panel.js) and workspace map
   (`'Campaign': { workspace: 'experiments' }`, aligned like neighbours); capability_manifest.py workspace map
   (`"Campaign": {"workspace": "experiments", "gates": []}`), plugin_files tuple
   (`("campaign-panel.js", "Campaign", ["experiments"], [], "<short description>")`), `keys_read_map` entry
   `"campaign-panel.js": []`; then regenerate capability_manifest.json with the script.
</spec>

<tests>
`campaign_panel_harness.js`: fake DOM, fake fetch recording {method, url, body} with scriptable replies, fake timers
(setInterval/clearInterval), fake window.confirm, stub api whose submitCommand/gatedCommand record calls. Prints exactly
one line per check, each starting with the letter tag, then `ALL CHECKS PASSED`, exit 0; any failure -> exit 1:
  a. registers as `Campaign`
  b. go disabled with empty pack ID, and with each one of the 6 boxes unticked in turn
  c. all set -> exactly one POST /api/campaign/go with the exact body
  d. Pause/Land/Abort present in idle, running, waiting_for_go, operator_needed and error states
  e. each of Pause/Land/Abort -> one POST to its own route with source operator
  f. waiting_for_go shows the pack and prefills the pack ID
  g. flights render one row each
  h. 409 and 503 error text shown
  i. allow_agent_arm: confirm false -> no POST; confirm true -> POST {allow_agent_arm: true, source: 'operator'}
  j. after teardown, advancing timers causes no further fetch
  k. zero submitCommand/gatedCommand calls over the whole run
  l. ticks reset after a successful go
`test_campaign_panel.py`: like test_motor_bench_panel.py (skip if no node), `encoding="utf-8"`, asserts returncode 0,
`ALL CHECKS PASSED`, and that each of the 12 tags a. .. l. appears at the start of some output line.
Run and paste last lines:
  node ground_station/service/tests/campaign_panel_harness.js
  python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_panel.py ground_station/platform/tests/test_capability_manifest.py ground_station/service/tests/test_all_panels_audit.py ground_station/service/tests/test_motor_bench_panel.py
  node ground_station/service/tests/node_harness.js   (exit code)
  python ground_station/platform/capability_manifest.py --check
</tests>

<digest>
`.agent-ops/out/wp5-r1.md`, at most 25 lines: files changed, each command + its last 3 output lines,
deviations from this spec (should be none), open risks.
</digest>
