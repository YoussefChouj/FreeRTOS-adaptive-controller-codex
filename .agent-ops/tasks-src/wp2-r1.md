<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only. Never edit any .c or .h file.
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each file edit run `python -m py_compile <file>`; fix syntax before running tests.
4. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
5. Catch only specific exceptions. Bare `except:` and `except Exception: pass` are forbidden.
6. Do not change any existing module. Reuse `ground_station/flashtool/artifact_custody.py`; do not copy it.
7. Python 3.10+, standard library plus PyYAML (`import yaml`, already used in ground_station/service).
8. If a command or edit fails twice the same way, change approach; never repeat an identical call.
9. Write the digest `.agent-ops/out/wp2-r1.md` BEFORE printing DONE.
10. Output: no preamble, no summary prose.
11. Tests must never call Keil, a drone, a serial port, the network or a real C compiler.
</guardrails>

<context>
Workflow B task G8: the code-change gate. It is the only path by which an agent may change controller
C code between flights. Spec: `.agent-ops/grill-autonomous-flight-loop.md` lines 199-223 (Q10b protected
set, Q10c nine steps). Plan: `docs/workflow-b/build-plan.md` Task 11.
Measured facts (2026-10-01, this branch):
- Baseline: `python -m pytest -q -p no:cacheprovider ground_station/flashtool/tests` -> "102 passed".
- `ground_station/flashtool/artifact_custody.py`: `snapshot(obj_dir)`, `commit(obj_dir)`, `restore(obj_dir)`
  return frozen dataclass `CustodyState(snapped, triple_present, cache_path, reasons)`; `has_snapshot(obj_dir)
  -> bool`; `cache_dir(obj_dir)`; `FLASHED_TRIPLE = ("JX_FLY.axf", "JX_FLY.hex", "JX_FLY.map")`.
- Protected markers present in the C tree (format `/* PROTECTED BEGIN <name> */` .. `/* PROTECTED END <name> */`):
  TASK/RemoterTask.c:158-177 `rc_kill`, TASK/RemoterTask.c:179-205 `rc_ch5_land`,
  TASK/StabilizerTask.c:264-281 `wfb_apply`.
- `static void AutoflyTask_PathArbitrate(void)` is defined at TASK/AutoflyTask.c:17.
- Tracked files from the Q10b list: API/rc_input.c, TASK/RemoterTask.c, API/flight_fsm.c, BSP/pwm.c,
  API/bmi088_driver.c, USER/main.c. Safety logic (heartbeat loss, limits) lives in API/wfb_safety.c / .h
  (functions wfb_safety_default_limits, wfb_safety_init, wfb_safety_step).
- No file defines named "param IDs" (grep for PARAM_ID/param_id found nothing). Search for where the
  safety limits (tilt/rate limits, fence, ceiling, low-V thresholds, heartbeat timeout, mixer saturation)
  are named (e.g. struct fields in API/wfb_safety.h, API/rc_input.h); list what you find as param_ids,
  and anything you cannot locate under `unresolved:`.
</context>

<allow-list>
ground_station/flashtool/code_gate.py
ground_station/flashtool/protected_set.yaml
ground_station/flashtool/tests/test_code_gate.py
.agent-ops/out/wp2-r1.md          (digest)
</allow-list>

<spec>
A. `ground_station/flashtool/protected_set.yaml` (Q10b). Keys:
   paths: [repo-relative files fully protected: API/rc_input.c, TASK/RemoterTask.c, API/flight_fsm.c,
           BSP/pwm.c, API/bmi088_driver.c, USER/main.c, API/wfb_safety.c, API/wfb_safety.h, plus any other
           Q10b file you locate with grep]
   regions: [marker names: rc_kill, rc_ch5_land, wfb_apply]
   functions: [AutoflyTask_PathArbitrate, plus any other Q10b function you locate]
   param_ids: [identifiers of the Q10b safety limits you locate]
   unresolved: [each Q10b item you could not locate, as a short string]
   Each Q10b item gets a `# <file>:<line>` comment where you located it.

B. `ground_station/flashtool/code_gate.py`:
   `load_protected_set(path=DEFAULT_PROTECTED_SET) -> ProtectedSet` (dataclass: paths, regions, functions, param_ids).
   `GateResult` dataclass: `ok: bool`, `step: int | None` (failing step 1-9, None when ok), `reasons: list[str]`,
   plus `lkg_hash: str | None` (set by step 5).
   `class CodeGate.__init__(self, *, build, ram_check, sil, custody, ledger_path, clock, obj_dir,
       ram_limit_bytes, tolerance_frac, protected=None, lkg_j=None)`
     - build() -> (error_count: int, map_path: str)
     - ram_check(map_path) -> used RAM in bytes (int)
     - sil(changed_c_files: list[str]) -> (stable: bool, j: float)
     - custody: a module-like object with snapshot/commit/restore/has_snapshot(obj_dir), default
       `artifact_custody`. clock() -> float seconds. No default for ram_limit_bytes / tolerance_frac
       (required keyword args, no numbers baked in).
     - `sil` default = a function that returns failure; step 4 then FAILS with reason
       "SIL hook not wired" (fail closed). Do not compile C.
   `check_change(diff_text, justification, files_after) -> GateResult` runs steps 1-5 in order, stops at
   the first failure. `files_after: dict[str, str]` path -> full post-change text.
     1 protected: parse the unified diff (`+++ b/<path>`, `@@ -a,b +c,d @@`). FAIL if a touched path is in
       `paths`; or a hunk's post-image line span [c, c+d-1] (d=0 -> just line c) overlaps a
       `PROTECTED BEGIN x`..`PROTECTED END x` region in files_after[path] for x in `regions`; or overlaps the
       body of a function in `functions` (from the line with `<name>(`  that is followed by `{` to the
       brace-matched closing `}` in files_after[path]); or any added/removed line (`+`/`-`, not
       `+++`/`---`) contains a param ID as a whole word. Reasons name the path and the hit.
     2 justification: `justification` is a dict with `argument` (control-theory text) and
       `predicted_effect` (text) and `metric` (name). FAIL if any is missing/blank. On PASS append one JSON
       line `{"ts": clock(), "event": "justification", ...}` to ledger_path.
     3 build: errors, map_path = build(); FAIL if errors != 0 or ram_check(map_path) > ram_limit_bytes.
     4 SIL: changed C files = touched paths ending in `.c`; stable, j = sil(files). FAIL if not stable, or
       lkg_j is not None and j > lkg_j * (1 + tolerance_frac) (J lower is better). Remember j as pending J.
     5 custody: if not custody.has_snapshot(obj_dir): custody.snapshot(obj_dir). FAIL if still no snapshot.
       lkg_hash = sha256 hex of `<cache_dir(obj_dir)>/JX_FLY.hex` (FAIL if missing); append
       `{"event": "lkg", "hash": ...}` to the ledger.
     On full PASS the change becomes pending (one per flight).
   Runner hooks:
     6 `check_change` refuses (step 6) a second change while one is pending and no flight was recorded since.
     7 `next_flight_must_hover() -> bool`: True for the first flight after an accepted change, then False.
     8 `on_flight_result(aborted: bool, j: float | None) -> str`: "revert" (custody.restore(obj_dir)) when
       aborted or (lkg_j is not None and j > lkg_j * (1 + tolerance_frac)); else "keep" (custody.commit(obj_dir),
       lkg_j = j). Clears the pending change. Appends the decision to the ledger.
     9 `record_flight(flight_id: str, fw_hash: str)` appends `{"event": "flight", "flight_id", "fw_hash", "ts"}`.
   Keep it minimal; module docstring cites Q10b/Q10c.
</spec>

<tests>
`ground_station/flashtool/tests/test_code_gate.py`, pytest, tmp_path, fakes for every dependency (a fake
custody object recording calls; fake build/ram_check/sil; clock = lambda: 1.0). At least 18 tests: for each
of steps 1-9 one PASS case and one FAIL case. Step 1 FAIL cases must cover separately: protected path,
hunk inside a PROTECTED region, hunk inside AutoflyTask_PathArbitrate body, changed line with a param ID;
PASS case: a hunk in the same file outside the region. Step 4: the default sil fails with "SIL hook not
wired". One test loads the real `protected_set.yaml` and checks it contains rc_kill, rc_ch5_land, wfb_apply,
AutoflyTask_PathArbitrate and TASK/RemoterTask.c.
Run:
  python -m pytest -q -p no:cacheprovider ground_station/flashtool/tests/test_code_gate.py
  python -m pytest -q -p no:cacheprovider ground_station/flashtool/tests
</tests>

<digest>
`.agent-ops/out/wp2-r1.md`, at most 30 lines: files changed, each command + its last 3 output lines,
the protected_set `unresolved:` list, whether `sim/bench/c_ref` (C controllers + c_api.c + test_equiv.py)
could host the real SIL compile (one or two sentences from reading it), deviations, open risks.
</digest>
