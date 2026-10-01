Status: DONE for CEO fixes 1 and 2. Fix 3 is PARTIAL: the base run on workflow-b @ 01a1e4b was not done because
  all three ways to get a base tree needed approval (see the last line).
Commits (on wp/7, after the manager's 2f9a2cc):
  42d30f8 - cte(wp7): RUNBOOK real operator_needed reasons; e2e holds sim mid-flight for pause/land/abort
  (this report in the next commit)
Gate: GATE PASS   (size 404/1000, scope 13 files, ruff 5 files clean, pytest 26 passed in 27.34s)
Verification (run by me, this session):
  pytest test_workflow_b_e2e.py --durations=8 -> 7 passed in 8.65s; re-run -> 7 passed in 9.19s
    (slowest call: test_go_e2e_two_packs 0.87s; the old 30 s test_pause_land_abort timeouts are gone)
  base 5-file set on wp/7 (run 1) -> 82 passed in 47.38s, no "Exception occurred" line
  base 5-file set on wp/7 (run 2) -> 82 passed in 49.81s, ONE line
    "Exception occurred during processing of request from ('127.0.0.1', 51031)", after about test 49 of 82
  capability_manifest.json: not regenerated, no route/description changed (no-drift test passed in both runs)
Worker rounds: CTE, effort medium (1 pass, no workers)

Fix 1 - RUNBOOK operator_needed list (docs/workflow-b/RUNBOOK.md):
  Replaced "cooldown_min_s not met" / "battery not resting" with the real strings, one "what to do" each:
  "cooldown not reached" (campaign_runner.py:166) and every PackRegistry.next_flight_allowed reason
  (battery_model.py:316-341): "REST: cooldown <s>s < required <min_rest_s>s", "SOC: predicted post-flight SoC <p>% <
  required <gate>% (measured resting SoC <s>%, expected drop <d>%)", "INPUT: unknown pack '<pack_id>'",
  "INPUT: non-finite resting_v: <v>", "INPUT: non-finite cooldown_s: <v>". The f-string values are shown as <...>.
  The other runner reasons (operator abort, param write refused, landing timeout[; revert flash pending],
  abort level 3 or consecutive) now have one short action each too.

Fix 2 - test_workflow_b_e2e.py (7 tests):
  - _wait_state calls pytest.fail with the last state on timeout.
  - Every go asserts its HTTP code (403, 409, 200s; 503 for non-sim). Every /api/agent/control POST asserts 200.
    Pause/land/abort POSTs send {"source": "operator"}.
  - Hold: a `hold` fixture monkeypatches campaign_deps.FakeDrone with a FakeDrone subclass, so ApiServer keeps its
    default wiring (no injected campaign_service). The subclass logs every frame it gets (parse_command:
    command_id, index, value, airborne) and, on its first step with traj_state EXECUTING, sets an in_flight Event and
    waits on a release Event (20 s safety timeout; the fixture teardown always releases).
  - test_operator_control_mid_flight[pause|land|abort]: go -> wait in_flight -> assert drone prim_state TRAJ and
    armed, HTTP status "running" -> mark the drone log -> POST cmd -> release -> final state:
      pause -> operator_stop / "operator pause", flight finished its trajectory (abort_reason "", no traj_stop), land sent
      land  -> operator_stop / "operator land",  abort_reason "operator land"
      abort -> operator_needed / "operator abort", abort_reason "operator abort"
    For land and abort the commands after the POST include TRAJ STOP and then PRIM LAND. For all three: no CMD_KILL,
    no disarm (ARM idx 0, value < 0.5) while airborne, drone ends prim_state IDLE.
    SAFETY RESULT: the runner does traj_stop then land on operator land/abort, with no kill and no disarm in air.
    No BLOCKED stop was needed. campaign_runner.py not touched.
  - test_go_e2e_two_packs uses the same hold, so "running with 0 flights" is seen every time before the
    waiting_for_go (P4000-2, 1 flight) -> go -> complete (2 flights = YAML count) path.
  - Found while doing this: POST /api/agent/control with tier0_access "full" also sets allow_agent_arm True
    (agent.py:742-746, "Full access includes arming"). The test helper sends tier0_access only when it allows arming,
    otherwise the arm_refused test (b) would fly. The worker's version of test b did not send tier0_access.

Fix 3 - "Exception occurred during processing of request" stderr line:
  Seen on wp/7 in 1 of 2 full runs (run 2 above), so it is intermittent. It came out after about test 49 of 82. By
  position (13 test_campaign_api + 17 test_runner + test_agent next) that is inside test_agent.py. This is INFERRED
  from the progress dots, not measured per test. WP-7 does not change test_agent.py. For those tests, WP-7's only
  effect is that a sim ApiServer now builds sim_deps_factory() and sim_knobs() at init.
  Whether the line also appears on workflow-b @ 01a1e4b: NOT MEASURED. The base run was not done because each of these
  needed approval: `git worktree add --detach <scratchpad> 01a1e4b`,
  `git checkout 01a1e4b -- <3 files>`, `git archive -o <scratchpad>/base.tar 01a1e4b ...`.

Open notes (left as the CEO asked, not fixed here):
- campaign_deps.py wraps tuner.record/propose to map J=None -> math.inf (runner/tuner contract gap).
- e2e sim_service still injects bridge=Mock() into GroundStationService (a sim service has no bridge).
- FlightRecord.decision is "" when the gate has no pending change; the test asserts the key is present.
- sample() sets ref_m=drone.position, so the position-error abort cannot fire in sim.
- RUNBOOK line "operator_stop ... or denied the wait_for_go prompt" was not re-checked against the runner (out of
  this fix's scope).
MANAGER.md / CTE rules: denied commands this run (approval needed): git worktree add, git checkout <commit> -- <files>,
  git archive. Rule unclear: fix 3 asks for a run on the base, but the CTE has no allowed way to get a base tree.
