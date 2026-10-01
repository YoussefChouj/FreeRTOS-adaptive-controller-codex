<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only. Never touch `OBJ/`, never run Keil.
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each Python edit run `python -m py_compile <file>`; after each C edit run the gcc command below. Fix before going on.
4. Run every command in the foreground and paste its verbatim output (last 3 lines at least) into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
5. Catch only specific exceptions. Bare `except:` and `except Exception: pass` are forbidden.
6. Python 3.10+, standard library + numpy (+ pandas only through the existing loader). C: C99, no malloc.
7. If a command or edit fails twice the same way, change approach; never repeat an identical call.
8. Write the digest `.agent-ops/out/wp8-r2.md` BEFORE printing DONE.
9. Output: no preamble, no summary prose.
</guardrails>

<context>
This is a FIX round for your own previous work (HEAD = your wp8-r1 commit, task `.agent-ops/tasks-src/wp8-r1.md`
is still the full spec; everything there still applies). The supervisor ran the gate on the Windows laptop. Verbatim:

```
FAIL ruff: 7 new findings
  ground_station/analysis/ekf_of_model.py:52 [E702] Multiple statements on one line (semicolon)
  ground_station/analysis/ekf_of_model.py:53 [E702] Multiple statements on one line (semicolon)
  ground_station/analysis/ekf_of_replay.py:178 [E701] Multiple statements on one line (colon)
  ground_station/analysis/ekf_of_replay.py:181 [E701] Multiple statements on one line (colon)
  ground_station/analysis/ekf_of_replay.py:354 [E741] Ambiguous variable name: `l`
  ground_station/analysis/ekf_of_replay.py:384 [E741] Ambiguous variable name: `l`
  ground_station/analysis/tests/test_ekf_of_model.py:192 [F401] `ground_station.comm.boot_default_layout.BOOT_DEFAULT_VARS` imported but unused
FAIL pytest:
  >           self._handle = _dlopen(self._name, mode)
  E           OSError: [WinError 193] %1 is not a valid Win32 application
  ..\..\..\AppData\Local\Programs\Python\Python311\Lib\ctypes\__init__.py:376: OSError
  FAILED ground_station/analysis/tests/test_ekf_of_model.py::test_golden - OSEr...
  FAILED ground_station/analysis/tests/test_ekf_of_model.py::test_c_defaults - ...
  2 failed, 3 passed in 17.37s
```
WinError 193 = the DLL's architecture does not match the 64-bit Python 3.11 (likely a 32-bit MinGW gcc on PATH).
The test also wrote `libekf_of.so` on Windows, against the r1 spec (`.dll` on Windows).
(The gate also reported a clang-tidy error at TASK/StabilizerTask.c:449 "expected 2, have 4": that is a gate
artefact, the compile database includes the main checkout's old ekf_of.h. Do NOT change line 449 for it.)

Supervisor review of `API/ekf_of.c`:
- Top comment lines 5-10 contradict themselves ("Actually, the struct uses:"). Write one clean state description.
- `EKF_OF_NOISE_ROW` is a do-while setter, not the repo table pattern. Read `docs/firmware-table-pattern.md`
  section "Second reference: API/mrac.c" and `API/mrac.c` (`MRAC_SET`): the assignment form for init functions.
- Predict copies P into 16 locals and then into a second 4x4 array; stray blank lines at 69, 76, 89-90.

The five real flight logs are on the supervisor's laptop only (resolved by your `--logs-dir` default fallback via
the git common dir). The gate's pytest runs there, so a test that uses the real logs runs on the laptop and skips on the VPS.
</context>

<allow-list>
API/ekf_of.c
ground_station/analysis/ekf_of_model.py
ground_station/analysis/ekf_of_replay.py
ground_station/analysis/tests/test_ekf_of_model.py
.agent-ops/out/wp8-r2.md         (digest)
</allow-list>

<spec>
1. Fix all 7 ruff findings above (rename `l` to a descriptive name; one statement per line; drop the unused import).
2. Golden-test library build (`gcc_lib` fixture):
   - Output name `libekf_of.dll` on Windows (`sys.platform == "win32"`), else `libekf_of.so`.
   - Compiler: env `EKF_OF_GCC` if set, else `gcc` on PATH; none -> `pytest.skip("gcc not found")`.
   - If Python is 64-bit (`struct.calcsize("P") == 8`) add `-m64`. If the compile fails, or `ctypes.CDLL` raises
     OSError, `pytest.skip(f"gcc <path> (<gcc -dumpmachine output>) cannot build a library for this Python: <error>")`.
     The skip must not hide a numeric mismatch: only build/load errors skip.
3. `API/ekf_of.c` cleanup (no change to the math or the defaults):
   - One clean top comment: states, axis index table, predict, the two measurements (OF h=[0,1,1,0], ZUPT h=[0,1,0,0]), units.
   - Defaults in the assignment-form table pattern, one row per per-axis state in order p, v, bof, ba, written
     for both axes:
       legend comment (q = random-walk/white-noise density per second, units per state; P0 = initial variance),
       `#define EKF_OF_STATE(i, q_field, q, P0)  e->q_field = (q); e->P[0][(i)*5] = (P0); e->P[1][(i)*5] = (P0)`
       rows `EKF_OF_STATE(0, q_pos, 1e-6f, 1.0f);   /* p    position */` etc., aligned columns, plus
       `e->R_of = ...; e->R_zupt = ...;` as a second aligned two-row group with a legend. Keep the literal values.
       A `Change history (newest first)` comment below: `2026-10-01 WP-8: 8-state model, q_vel->q_acc, ZUPT; values provisional`.
   - Predict: compute F P F' directly from `e->P[axis]` (no 16 scalar copies); remove stray blank lines.
4. New test `test_replay_real_logs` in the test file: resolve the logs dir with the replay's own default resolver;
   `pytest.skip` if it is missing or fewer than 5 of the 5 log patterns match. Run the replay's main entry
   (with the grid, as the CLI does by default) capturing stdout (`capsys`). Assert: 5 per-log tables were printed
   (one per log name), every metric is finite, and the line `DEFAULTS_MATCH yes` is present. The assertion
   message for DEFAULTS_MATCH must contain the full `CHOSEN ...` line, the top-5 combos and the per-log tables
   (so a failing gate run shows the tuning result). Make sure a log that cannot be loaded raises with the log name
   in the message rather than being silently skipped.
   The supervisor expects this test to FAIL on the laptop this round (defaults are provisional); that is correct.
5. Keep the full replay run under 120 s on a laptop (vectorised over combos). Print the elapsed time at the end.
</spec>

<tests>
Run: `PYTHONPATH=. python -m pytest ground_station/analysis/tests/test_ekf_of_model.py ground_station/comm -q`
(on the VPS test_replay_real_logs must SKIP, the golden test must PASS with Linux gcc),
`gcc -std=c99 -Wall -Wextra -Werror -c API/ekf_of.c -o /tmp/ekf_of.o`,
`ruff check ground_station/analysis/ekf_of_model.py ground_station/analysis/ekf_of_replay.py ground_station/analysis/tests/test_ekf_of_model.py`
(if ruff is missing: `python -m pip install ruff` once; if that fails write "ruff: not installed").
</tests>

<digest>
`.agent-ops/out/wp8-r2.md`, at most 25 lines: files changed, each command + its last 3 output lines verbatim,
deviations from this spec, open risks.
</digest>
