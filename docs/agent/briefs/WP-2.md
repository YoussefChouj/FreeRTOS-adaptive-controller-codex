
# WP-2 brief (CEO -> Manager), 2026-10-01. Workflow B task G8: the code-change gate.

Base: `workflow-b` (gate with `--base workflow-b`). Spec: `.agent-ops/grill-autonomous-flight-loop.md`
Q10b (protected set) and Q10c (nine steps); plan: `docs/workflow-b/build-plan.md` Task 11 + amendment G8.

Goal: `ground_station/flashtool/code_gate.py`, the only path by which the agent may change controller
C code between flights. Pure Python, every side effect injected, so tests need no Keil, drone or SIL.

Facts the CEO measured on 2026-10-01 (workflow-b @ 0bc6a4a):
- `ground_station/flashtool/artifact_custody.py` has `snapshot(obj_dir)`, `commit(obj_dir)`, `restore(obj_dir)`,
  `has_snapshot(obj_dir)`, class `CustodyState`. `build_id.py` and `rebuild_and_flash.py` (build-only mode) exist.
- PROTECTED markers exist: `TASK/StabilizerTask.c` `/* PROTECTED BEGIN wfb_apply */`..`END wfb_apply`,
  `TASK/RemoterTask.c` regions `rc_kill`, `rc_ch5_land`. No protected-set file exists yet.
- `sim/bench/bench.py` has `objective(rows)`; `sim/bench/c_ref/` holds C controllers + `c_api.c` + `test_equiv.py`.

Wanted (class `CodeGate`, deps passed to `__init__`: build, ram_check, sil, custody, ledger path, clock):
1. `check_change(diff_text, justification, files_after) -> GateResult` runs steps 1-5 in order and stops at
   the first failure; `GateResult` names the failing step and the reasons.
   - Step 1 protected: refuse when the unified diff touches a protected path, a hunk's post-image span
     overlaps a `PROTECTED BEGIN x`..`END x` region or the body of a protected function (brace-matched in
     `files_after`), or a changed line names a protected param ID.
   - Step 2 justification: needs a non-empty control-theory argument and a predicted effect naming a
     metric; append it to the JSONL ledger.
   - Step 3 build: injected build returns error count + map path; 0 errors and `ram_check` within the
     injected `ram_limit_bytes`. No default numbers; never call Keil in tests.
   - Step 4 SIL: injected `sil(changed_c_files)` -> stable flag + J; pass only if stable and J is not worse
     than the last-known-good J by more than the injected `tolerance_frac`. The DEFAULT sil fails closed
     ("SIL hook not wired"). Do not wire the real C compile; report in Deviations whether
     `sim/bench/c_ref` could host it.
   - Step 5 custody: a last-known-good snapshot exists (take one if missing) and its hash is recorded.
2. Runner-time hooks: step 6 one code change per flight (a second change before a flight is refused);
   step 7 the first flight after a change must be hover-only (method the runner asks); step 8
   `on_flight_result(aborted, j)` -> revert (custody.restore) on abort or worse J, else keep (custody.commit);
   step 9 `record_flight(flight_id, fw_hash)` appends to the ledger.
3. `ground_station/flashtool/protected_set.yaml` (Q10b): paths, marker regions, functions
   (`AutoflyTask_PathArbitrate` ...), param IDs. Locate each Q10b item with Grep; list an item you cannot
   locate under `unresolved:` and in the report. Never edit C files.
Unit suffixes on numeric argument names (`ram_limit_bytes`, `tolerance_frac`). Minimal code, reuse custody.

Acceptance (run by you):
- First, on the base: `python -m pytest -q -p no:cacheprovider ground_station/flashtool/tests` -> note the last line.
- `python -m pytest -q -p no:cacheprovider ground_station/flashtool/tests/test_code_gate.py` -> 0 failed, a pass
  and a fail case for each of steps 1-9 (at least 18 tests)
- `python -m pytest -q -p no:cacheprovider ground_station/flashtool/tests` -> no new failures vs the base line
- `python .agent-ops/gate.py --base workflow-b --allow ground_station/flashtool/code_gate.py --allow ground_station/flashtool/protected_set.yaml --allow ground_station/flashtool/tests/test_code_gate.py --allow ".agent-ops/out/*"` -> GATE PASS

Scope (worker may edit): `ground_station/flashtool/code_gate.py`, `ground_station/flashtool/protected_set.yaml`,
`ground_station/flashtool/tests/test_code_gate.py`, its digest `.agent-ops/out/wp2-r<n>.md`
Allow globs: `ground_station/flashtool/code_gate.py` `ground_station/flashtool/protected_set.yaml` `ground_station/flashtool/tests/test_code_gate.py` `.agent-ops/out/*`
Worker lane: agy-vps, chain `agy:gemini-3.1-pro-high,agy:gemini-3.8-flash-high`   Max worker rounds: 3
Report to: `docs/agent/reports/WP-2.md`; add one line: any MANAGER.md rule that was unclear or a denied command.
