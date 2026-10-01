
# WP-3 brief (CEO -> Manager), 2026-10-01. Workflow B task G12: the campaign runner.

Base: `workflow-b` (gate with `--base workflow-b`). Spec: `.agent-ops/grill-autonomous-flight-loop.md`
Q2 (line 33, operator-gated battery sessions), Q8 (line 137, cooldown), Q12 (lines 238-249, abort levels),
summary items 1 and 4 (line 252 on); plan: `docs/workflow-b/build-plan.md` Task 13 + amendment G12.

Goal: `ground_station/service/campaign_runner.py`, the deterministic workflow-B loop. Every side effect is
injected, so the tests run against `service/fake_drone.py` with no drone, serial port, Keil or 8081.

Facts the CEO measured on 2026-10-01 (workflow-b @ 73bb5ca), all modules already exist; reuse them:
- `service/campaign_schema.py`: `load_campaign(path) -> Campaign` (packs, max_flights, envelope, experiments
  with shape/params/profile/repeats, `abort_limits` property). Example: `service/campaigns/example_circle.yaml`.
- `platform/wfb_commands.py`: `WfbClient(send)` with arm, idle, kill, takeoff, land, traj_* and heartbeat.
  `kill()` sends 0x0D = MOTORS OFF. `service/fake_drone.py`: `FakeDrone().send(frame)`, `.step(dt)`, `.status()`.
- `platform/trajectory_upload.py`: `upload(points, client)`; `service/trajectory_pipeline.py`: `generate(...)`.
- `service/abort_monitor.py`: `AbortMonitor` (`begin_flight`, `step(AbortSample) -> AbortDecision(level, reason)`,
  `end_flight`, `consecutive_aborts`). `analysis/battery_model.py`: `PackRegistry.next_flight_allowed(pack_id,
  resting_v, cooldown_s)`, `record_flight`. `analysis/tuner.py`: `Tuner.propose(history)`, `record(params, J, valid)`.
  `analysis/workflow_b_adapter.py`: `flight_rows(session_dir)`, `score(rows)`.
- `flashtool/code_gate.py`: `CodeGate.check_change(diff_text, justification, files_after)`,
  `next_flight_must_hover()`, `on_flight_result(aborted, j)`, `record_flight(flight_id, fw_hash)`.

Wanted:
1. `run_campaign(yaml_path, deps) -> CampaignReport` (deps = one dataclass of injected callables/objects:
   client, step/clock/sleep, packs, monitor, tuner, gate, flash, analyze, wait_for_go, arm_allowed, diff_source).
   Per flight, in order: operator go for the pack (Q2) -> cooldown: motors-off wait >= the last flight's
   duration and `next_flight_allowed` (Q8) -> optional code change through the gate -> flash (injected) ->
   arm -> takeoff -> upload + start trajectory -> fly while feeding `AbortMonitor` -> land -> analyze J ->
   tuner.record -> gate.on_flight_result + record_flight. Stop at `max_flights`.
2. Abort levels (Q12). CEO decision: level 1 in flight = `traj_stop()` then `land()` (return, settle, land at
   origin). The runner NEVER calls `kill()`; motors-off is the operator's (RC ch10, panel). Level 2 = gate
   revert via `on_flight_result(aborted=True, ...)`. Level 3 = campaign status `operator_needed`, no further arm.
3. Safety gates: refuse to arm unless `arm_allowed()` is True (default False; G13 wires the real switch).
   After a code change, the next flight is hover-only when `gate.next_flight_must_hover()`.
4. Carry from WP-2: the runner produces the diff itself via the injected `diff_source()` (default:
   `git diff <last-known-good commit> -- <changed C files>`) and reads `files_after` from disk. It never takes
   diff text or file contents from the agent; the agent supplies only the justification.
5. `CampaignReport`: per-flight id, pack, J (or None), abort level + reason, kept/reverted, final status.
Tests in `service/tests/test_runner.py`: e2e campaign against FakeDrone (3+ flights, J recorded), level-1 abort
(sends traj_stop + land, never kill), level-2 revert called, level-3 stop after two consecutive aborts,
cooldown enforced via fake clock, arm refused when `arm_allowed` False, hover-only after a code change,
gate receives the runner's diff. Unit suffixes on numeric args (`cooldown_s`, `dt_s`). Minimal code.

Acceptance (run by you):
- First, on the base: `python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_fake_drone.py ground_station/service/tests/test_abort_monitor.py ground_station/service/tests/test_campaign_schema.py ground_station/flashtool/tests/test_code_gate.py` -> note the last line.
- `python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_runner.py` -> 0 failed, at least 8 tests
- the first command again -> no new failures vs the base line
- `python .agent-ops/gate.py --base workflow-b --max-lines 800 --allow ground_station/service/campaign_runner.py --allow ground_station/service/tests/test_runner.py --allow ".agent-ops/out/*" --allow "docs/agent/reports/*" --allow ".agent-ops/tasks-src/*"` -> GATE PASS

Scope (worker may edit): `ground_station/service/campaign_runner.py`, `ground_station/service/tests/test_runner.py`,
its digest `.agent-ops/out/wp3-r<n>.md`
Allow globs: `ground_station/service/campaign_runner.py` `ground_station/service/tests/test_runner.py` `.agent-ops/out/*`
Worker lane: agy-vps, chain `agy:gemini-3.1-pro-high,agy:gemini-3.8-flash-high`   Max worker rounds: 3
Report to: `docs/agent/reports/WP-3.md`; add one line: any MANAGER.md rule that was unclear or a denied command.
