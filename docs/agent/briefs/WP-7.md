
# WP-7 brief (CEO -> Manager), 2026-10-01. Workflow B task G16: sim campaign wiring + e2e dry run + RUNBOOK.

Base: `workflow-b` (gate with `--base workflow-b`; the CEO launches this after WP-6 merges). Spec:
`.agent-ops/grill-autonomous-flight-loop.md` summary item 1 (line 254); plan: `docs/workflow-b/build-plan.md` Task 16
(line 243), G12 row ("e2e against `fake_drone`"). Out of scope: hardware execution, flashing, 8081.

Goal: the whole B loop runs against FakeDrone through the real dashboard HTTP API with the default `ApiServer` wiring,
so a dry run needs no hand-built test deps; plus the operator RUNBOOK for the real lab.

Facts the CEO measured on 2026-10-01 (workflow-b @ 191b252):
- `service/api.py` line 2627: if no `campaign_service` is passed, `ApiServer` builds `CampaignService(agent=self.agent)`
  with `deps_factory=None`, so POST `/api/campaign/go` returns 503 `{"error": "deps_factory None"}` (campaign_api.py 35).
- `service/campaign_runner.py` line 40 `RunnerDeps` (client, step, clock, sleep, status, sample, packs, monitor, tuner,
  gate, flash, analyze, wait_for_go, resting_v, arm_allowed, change_request, diff_source, ..., control, apply_params,
  on_flight). `CampaignService.go` replaces wait_for_go/arm_allowed/control/apply_params/on_flight itself.
- `service/tests/test_campaign_api.py` lines 62-115: `FakeClock` and `create_deps(drone, client, clock)` build deps from
  `FakeDrone` (`service/fake_drone.py` line 111) + `WfbClient(drone.send)` (`platform/wfb_commands.py`), but packs, tuner,
  gate and monitor are all `Mock`s, and flash/analyze are Mocks.
- Real components exist: `service/abort_monitor.py` (`AbortMonitor`, `AbortLimits`, `AbortSample`); find the real pack
  registry, tuner and change gate the WP-3 runner was written against (grep the runner's imports and its tests) and use them.
- `GroundStationService(..., source="sim")` is the sim source used by the tests; `service/__main__.py` builds the CLI
  (`_build_argparser`, line 28).

CEO decisions:
- Sim only. When the service source is `sim`, `ApiServer`'s default `CampaignService` gets a FakeDrone deps factory and
  the knobs of the campaign's controller. For any other source keep `deps_factory=None`, and change the 503 error text to
  `"live campaign wiring not built: hardware path needs operator approval"`. No live wiring in this WP.
- `flash` in the sim factory is a stub that returns a fixed fake hash and never imports or calls flashtool / ST-Link.
  `analyze` may be a deterministic stub if the flightlab analyzer cannot read FakeDrone output; say which in the report.

Wanted:
1. `ground_station/service/campaign_deps.py`: `FakeClock` (moved from the test) and `sim_deps_factory(...)` returning a
   zero-arg factory that builds `RunnerDeps` around a new `FakeDrone` + `WfbClient`, with the real AbortMonitor, pack
   registry, tuner and gate (Mocks only where no real class exists; list them in the report). `test_campaign_api.py`
   imports `FakeClock` from there instead of its own copy; its other tests stay unchanged and passing.
2. `api.py`: the sim / non-sim wiring above (smallest change near line 2627); `campaign_api.py`: the new 503 text.
3. `ground_station/service/tests/test_workflow_b_e2e.py`, ephemeral-port `ApiServer` with the DEFAULT wiring (no injected
   campaign_service), campaign YAML written to `tmp_path` (2 packs, 1-2 short experiments), every step over HTTP:
   a. go with an `agent:` source -> 403; go with one box false -> 409;
   b. allow_agent_arm False -> go -> status ends `arm_refused`, no flight armed;
   c. operator sets allow_agent_arm True -> go pack 1 -> `running` -> flights appear -> `waiting_for_go` with
      waiting_pack = pack 2 -> go pack 2 -> `complete`; every flight has a decision and the flight count matches the YAML;
   d. pause, land and abort during a run each reach their documented status (land -> operator_stop, abort -> operator_needed);
   e. a non-sim ApiServer -> go -> 503 with the new text.
   Whole file under 60 s wall time on the laptop; no sleeps longer than needed (poll state with a deadline).
4. `docs/workflow-b/RUNBOOK.md`, operator steps in order: install the phone app (adb install), bench-check yaw sign,
   clamp the phone and start recording, mark the end wall and pad centre, label packs, review the crash/abort thresholds
   (`abort_monitor.py` AbortLimits are PROPOSED, not flight-validated), write a campaign with the flight-campaign skill,
   open the Campaign panel, allow_agent_arm, per-battery Go with the checklist, Pause / Land / Abort, RC ch10 kill,
   what each end status means and what to do next. State plainly that live (non-sim) campaigns are not wired yet.

Worker rules: one statement per line, no `;`-joined statements; no new files outside the gate allow-list (no scratch
scripts at the repo root, nothing under `.agent-ops/served/`); do not reformat code you did not need to touch.

Acceptance (run by you):
- First, on the base: `python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_api.py ground_station/service/tests/test_runner.py ground_station/service/tests/test_agent.py ground_station/service/tests/test_abort_monitor.py ground_station/platform/tests/test_capability_manifest.py` -> note the last line.
- `python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_workflow_b_e2e.py` -> 0 failed, at least 5 tests, note the time
- the first command again -> no new failures vs the base
- if any route or description changed, regenerate `docs/dashboard-platform/capability_manifest.json` (no-drift test)
- `python .agent-ops/gate.py --base workflow-b --max-lines 1000 --allow ground_station/service/campaign_deps.py --allow ground_station/service/api.py --allow ground_station/service/campaign_api.py --allow ground_station/service/tests/test_campaign_api.py --allow ground_station/service/tests/test_workflow_b_e2e.py --allow docs/workflow-b/RUNBOOK.md --allow docs/dashboard-platform/capability_manifest.json --allow ".agent-ops/out/*" --allow "docs/agent/reports/*" --allow ".agent-ops/tasks-src/*"` -> GATE PASS

Scope (worker may edit): the files in the gate line above, its digest `.agent-ops/out/wp7-r<n>.md`
Allow globs: as the gate line.
Worker lane: agy-vps, chain `agy:gemini-3.1-pro-high,agy:gemini-3.8-flash-high`   Max worker rounds: 3
Report to: `docs/agent/reports/WP-7.md`; add one line: any MANAGER.md rule that was unclear or a denied command.

## CEO decision 2026-10-01, after PARTIAL (manager report wp/7 @ 2f9a2cc) -> CTE
CEO review: wiring, 503 text, flash stub, CodeGate (build/ram_check/custody Mocks, ledger in a temp dir, OBJ untouched)
and the read-only PackRegistry are accepted. Fix these in the existing wp/7 worktree, same gate line as above:
1. RUNBOOK.md operator_needed list: replace the two invented strings with the real ones. `"cooldown not reached"`
   (campaign_runner.py ~166) and the reason strings `PackRegistry.next_flight_allowed` returns (battery_model.py),
   quoted exactly; one short "what to do" per reason.
2. test_workflow_b_e2e.py item d is not tested today. `_wait_state` returns at the deadline without failing, and
   pack 1 finishes before "running and flights>0" is ever seen, so land/abort/pause go out after the run (30 s of
   timeouts). Required:
   - `_wait_state` calls `pytest.fail` with the last state on timeout.
   - Every go in the file asserts its HTTP code. Pause/land/abort POSTs send `{"source": "operator"}`.
   - Each of pause, land and abort is sent while status is `running` and a flight is in progress, deterministically:
     hold the sim mid-flight with a `threading.Event`, using monkeypatch on campaign_deps / FakeDrone from the test, so
     ApiServer keeps its default wiring. No sleeps longer than needed.
   - For land and abort, assert the FakeDrone's commands after the POST include traj_stop then land, and never a kill
     or disarm-in-air. If the runner does not do that, STOP and report it as BLOCKED with the evidence: it is a
     safety finding the CEO decides. Do not change campaign_runner.py.
   - The whole file still has at least 5 tests and runs under 60 s. Note the time.
3. Re-run the base 5-file set. Say whether the "Exception occurred during processing of request" stderr line also
   appears on workflow-b @ 01a1e4b (run it there in full, no tail cut).
Leave as open notes, not fixed here: J=None tuner wrapper, sim service bridge=Mock, decision "" when no change pending.
Report to `docs/agent/reports/WP-7-cte.md`.
