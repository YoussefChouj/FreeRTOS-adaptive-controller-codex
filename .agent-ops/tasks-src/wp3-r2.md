<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only, EXCEPT: delete the two stray
   files `run_test_debug.py` and `sim_debug.py` at the repo root (`git rm` them).
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each file edit run `python -m py_compile <file>`; fix syntax before running tests.
4. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
   ALWAYS run pytest as `timeout 120 python -m pytest ...` so a hang cannot eat your time budget.
5. Catch only specific exceptions. Bare `except:` and `except Exception: pass` are forbidden.
6. Do not change any existing module. Reuse the modules listed in <context>; do not reimplement them.
7. Python 3.10+, standard library + modules already in the repo only.
8. If a command or edit fails twice the same way, change approach; never repeat an identical call.
9. Write the digest `.agent-ops/out/wp3-r2.md` BEFORE printing DONE. Do not create debug scripts
   anywhere; debug inside the test file or with `python -c`.
10. Output: no preamble, no summary prose. Minimal code: campaign_runner.py <= 350 lines,
    test_runner.py <= 350 lines (total diff must stay under 800 lines).
11. SAFETY: the runner must NEVER call `client.kill()` anywhere. No serial port, no network, no Keil.
</guardrails>

<context>
This is a FIX round for your own previous work (HEAD = your wp3-r1 commit, which was KILLED by the time
limit before you finished). It contains campaign_runner.py (214 lines), test_runner.py (236 lines, 9 tests)
and two stray files run_test_debug.py, sim_debug.py that are outside the allow-list (the gate will FAIL on them).
Measured by the manager on the Windows laptop:
- `timeout 120 python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_runner.py`
  -> output `.` then `Exit code 124` (test_files_after_from_diff passes, test_a_e2e_complete HANGS).
- `... -k "not e2e and not cooldown"` -> `.` then `Exit code 124` (test_b_level_1_abort HANGS).
- Cause in your code: `run_loop_until(cond)` (campaign_runner.py ~line 151) is `while not cond():` with no
  bound, used for "until prim_state == 2" (HOVER) and "until traj_state == 4" (DONE). When the fake never
  reaches that state the loop never ends. Also find out WHY the fake does not reach HOVER/DONE in tests A/B
  (arm rejected? takeoff before armed? clock not advanced by step? read fake_drone.py _handle_arm,
  _handle_prim, step) and fix the runner or the test wiring.
- The pytest collection warns: "cannot collect test class 'TestClock' because it has a __init__
  constructor" -> rename it (e.g. FakeClock).
Base API facts (unchanged): see the spec. Baseline other suites:
`python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_fake_drone.py
ground_station/service/tests/test_abort_monitor.py ground_station/service/tests/test_campaign_schema.py
ground_station/flashtool/tests/test_code_gate.py` -> `172 passed in 8.41s`.
APIs: FakeDrone `.send/.step(dt)/.status()` (keys prim_state, traj_state, safety_trip, hb_age, ...),
PrimState IDLE=0 CLIMB=1 HOVER=2 TRAJ=3 RETURN=4 SETTLE=5 DESCEND=6; TrajState EMPTY=0 LOADING=1 READY=2
EXECUTING=3 DONE=4. AbortMonitor begin_flight/step(AbortSample)->AbortDecision(level, reason)/end_flight/
consecutive_aborts. CodeGate check_change(diff_text, justification, files_after)->GateResult(ok, step, reasons),
next_flight_must_hover(), on_flight_result(aborted, j)->"keep"|"revert", record_flight(flight_id, fw_hash).
PackRegistry.next_flight_allowed(pack_id, resting_v, cooldown_s)->(bool, str). Tuner propose(history)/
record(params, J, valid). trajectory_pipeline.generate(shape, params, profile); trajectory_upload.upload(points, client).
</context>

<allow-list>
ground_station/service/campaign_runner.py
ground_station/service/tests/test_runner.py
.agent-ops/out/wp3-r2.md                         (digest)
run_test_debug.py, sim_debug.py                  (DELETE only)
</allow-list>

<spec>
Keep the round-1 spec (RunnerDeps, default_diff_source, files_after_from_diff, FlightRecord, CampaignReport,
run_campaign order 1-10, never kill). Required fixes:
F1. Every wait loop in the runner is bounded by a TICK COUNT: max_ticks = ceil(flight_timeout_s / dt_s),
    counted per loop, so it terminates even if clock() never advances. Applies to: until HOVER, until traj
    DONE, hover hold, landing until IDLE, cooldown waits.
F2. In flight (until HOVER, trajectory, hover hold) a timeout is an abort: level 1, reason "timeout: <phase>"
    -> traj_stop() then land(), same as any level-1 abort. Landing timeout -> status "operator_needed",
    reason "landing timeout", return (no further arm).
F3. Delete run_test_debug.py and sim_debug.py.
F4. Rename TestClock to FakeClock.
F5. Each test asserts what it claims (see tests). Test A must actually reach HOVER and traj DONE on FakeDrone
    for every flight (assert J recorded per flight and status "complete").
</spec>

<tests>
Keep tests A-H from round 1 + test_files_after_from_diff:
  A. e2e: 3+ flights complete on FakeDrone, J recorded for each, status "complete", tuner.record per flight.
  B. level-1 abort: client sends traj_stop then land; kill() spied and never called.
  C. level-2: aborted flight -> gate.on_flight_result called with aborted=True.
  D. level-3: two consecutive aborts -> status "operator_needed", no further arm.
  E. cooldown: fake clock shows the second takeoff happens >= first flight duration after landing.
  F. arm_allowed False (default) -> status "arm_refused", no arm frame sent.
  G. code change + gate next_flight_must_hover True -> flight hover_only, no traj_start.
  H. gate.check_change receives exactly diff_source()'s string and files read from disk.
Add: I. a world that never reaches HOVER -> runner returns (no hang), flight abort_level 1, reason starts
  "timeout", land() sent, kill() never sent.
Run and paste: `timeout 120 python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_runner.py`
(must finish in well under 120 s, 0 failed) and the baseline command (must still show 172 passed).
</tests>

<digest>
`.agent-ops/out/wp3-r2.md`, at most 25 lines: files changed + line counts, why tests A/B hung (root cause),
each command + its last 3 output lines, deviations from this spec, open risks.
</digest>
