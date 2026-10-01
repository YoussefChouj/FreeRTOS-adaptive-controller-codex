<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only. Never edit any .c or .h file.
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each file edit run `python -m py_compile <file>`; fix syntax before running tests.
4. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
5. Catch only specific exceptions. Bare `except:` and `except Exception: pass` are forbidden.
6. Keep the public API of round 1 (CodeGate, GateResult, ProtectedSet, load_protected_set, method names
   and signatures). Do not change any other module.
7. Python 3.10+, standard library plus PyYAML.
8. If a command or edit fails twice the same way, change approach; never repeat an identical call.
9. Write the digest `.agent-ops/out/wp2-r2.md` BEFORE printing DONE.
10. Output: no preamble, no summary prose.
11. Tests must never call Keil, a drone, a serial port, the network or a real C compiler.
12. Never put two statements on one line; readability beats line count.
</guardrails>

<context>
This is a FIX round for your own previous work (HEAD = your wp2-r1 commit). The supervisor ran:
`python .agent-ops/gate.py --base workflow-b --allow ground_station/flashtool/code_gate.py --allow ground_station/flashtool/protected_set.yaml --allow ground_station/flashtool/tests/test_code_gate.py --allow ".agent-ops/out/*"`
Verbatim output:
```
FAIL size: 523/200 lines
PASS scope: 4 files
SKIP clang-tidy: no C changes
FAIL ruff: 3 new findings
  ground_station/flashtool/tests/test_code_gate.py:3 [F401] `json` imported but unused
  ground_station/flashtool/tests/test_code_gate.py:4 [F401] `dataclasses.dataclass` imported but unused
  ground_station/flashtool/tests/test_code_gate.py:238 [E741] Ambiguous variable name: `l`
PASS pytest: 26 passed in 0.69s
GATE FAIL: size,ruff
```
Size counts added+removed lines of code_gate.py + protected_set.yaml + test_code_gate.py (.md excluded).
On Windows, `python -m pytest -q -p no:cacheprovider ground_station/flashtool/tests` -> "128 passed".

Review findings in code_gate.py (safety holes; this gate exists to stop exactly these):
F1. `_parse_diff` only reads `+++ b/<path>`. A deleted file has `+++ /dev/null` and its path only in
    `--- a/<path>`, so deleting USER/main.c is NOT refused (hunks are dropped or attributed to the previous
    file). A rename (`--- a/API/rc_input.c`, `+++ b/API/x.c`) is also not refused.
F2. Region and function checks only look at files_after. A hunk that deletes or edits a
    `PROTECTED BEGIN x` / `PROTECTED END x` marker line removes the region from files_after, so it passes.
    Also, when a touched path is missing from files_after the region/function checks are silently skipped.
F3. Step 5 calls custody.snapshot() AFTER step 3's build() has overwritten OBJ/, so a missing snapshot is
    taken of the NEW, untested build and recorded as last-known-good.
F4. `_overlaps_function` starts at the first line matching `<name>(`, which may be a prototype
    (`static void X(void);`) or a call; brace counting then runs into the wrong body.
</context>

<allow-list>
ground_station/flashtool/code_gate.py
ground_station/flashtool/protected_set.yaml
ground_station/flashtool/tests/test_code_gate.py
.agent-ops/out/wp2-r2.md          (digest)
</allow-list>

<spec>
Fix in code_gate.py:
- F1: each file section yields both its pre path (`--- a/<p>`, or None for /dev/null) and post path
  (`+++ b/<p>`, or None for /dev/null). Step 1 FAILS if either path is in `paths`. Hunks of a deleted file
  belong to its pre path.
- F2: Step 1 FAILS if any added or removed line contains `PROTECTED BEGIN` or `PROTECTED END`.
  Step 1 FAILS (fail closed) if a touched post path ending in .c or .h is not a key of files_after.
- F3: before calling build() in step 3, if not custody.has_snapshot(obj_dir): custody.snapshot(obj_dir).
  Step 5 no longer snapshots: it FAILS if not has_snapshot, else hashes `<cache_dir>/JX_FLY.hex` as now.
- F4: the function body starts at the first line matching `<name>\s*\(` whose text up to the next `{` or
  `;` (possibly on following lines) reaches `{` first; skip prototypes and calls.
- Ruff: fix the 3 findings above.
Reduce size without cramming: parametrize the step-1 test cases with pytest.mark.parametrize, share one
fixture/factory for the CodeGate fakes, drop duplicated helpers and blank-line runs. Keep at least one PASS
and one FAIL test for each step 1-9 (>= 18 tests).
</spec>

<tests>
Add tests (to the parametrized step-1 set where possible):
  - deleting a protected file (`--- a/USER/main.c` / `+++ /dev/null`) -> step 1 FAIL
  - renaming API/rc_input.c to API/x.c -> step 1 FAIL
  - a removed line `/* PROTECTED END rc_kill */` -> step 1 FAIL
  - touched TASK/StabilizerTask.c missing from files_after -> step 1 FAIL
  - prototype `static void AutoflyTask_PathArbitrate(void);` at line 3, body at 10-14: hunk at line 6 PASSES,
    hunk at line 12 FAILS
  - no snapshot before the gate: fake custody records the call order; snapshot is called before build
Run:
  python -m pytest -q -p no:cacheprovider ground_station/flashtool/tests/test_code_gate.py
  ruff check ground_station/flashtool/code_gate.py ground_station/flashtool/tests/test_code_gate.py   (if ruff exists; else write "ruff: not installed")
</tests>

<digest>
`.agent-ops/out/wp2-r2.md`, at most 25 lines: files changed, each command + its last 3 output lines,
`wc -l` of the three files, deviations from this spec, open risks.
</digest>
