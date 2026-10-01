<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only. Create NO new files except the digest.
   Do not create helper/runner scripts anywhere; run pytest directly from the shell.
2. Write complete implementations. No "...", TODO or `pass` stubs.
3. After each Python file edit run `python -m py_compile <file>`.
4. Run every command in the foreground and paste its verbatim output into the digest.
5. One statement per line; no `;`-joined statements, no `if x: y` on one line.
6. Change nothing beyond the items in <spec>.
7. Write the digest `.agent-ops/out/wp7-r3.md` BEFORE printing DONE.
8. Output: no preamble, no summary prose.
</guardrails>

<context>
FINAL FIX round for your own work (HEAD = your wp7-r2 commit). The manager ran the gate on the laptop. Verbatim:
```
PASS size: 344/1000 lines
FAIL scope:
  outside allow-list: run_test.py
  outside allow-list: run_tmp.py
SKIP clang-tidy: no C changes
FAIL ruff: 2 new findings
  ground_station/service/tests/test_workflow_b_e2e.py:197 [E702] Multiple statements on one line (semicolon)
  run_tmp.py:1 [F401] `yaml` imported but unused
PASS pytest: 26 passed in 31.72s
GATE FAIL: scope,ruff
```
`run_test.py` and `run_tmp.py` are scratch scripts your round 2 committed at the repo root. Forbidden.
test_workflow_b_e2e.py line 197 is: `    print("LAND REASON IS", state.get("reason")); assert state.get("status") == "operator_stop"`
</context>

<allow-list>
run_test.py                                       (delete only)
run_tmp.py                                        (delete only)
ground_station/service/tests/test_workflow_b_e2e.py
.agent-ops/out/wp7-r3.md                          (digest)
</allow-list>

<spec>
1. `git rm run_test.py run_tmp.py` (both must be gone from the commit).
2. test_workflow_b_e2e.py line 197: delete the print; keep `    assert state.get("status") == "operator_stop"` on its own line.
3. Nothing else.
</spec>

<tests>
Run, and paste the last 3 lines of each:
  git status --short
  python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_workflow_b_e2e.py
  ruff check ground_station/service/tests/test_workflow_b_e2e.py   (or "ruff: not installed")
</tests>

<digest>
`.agent-ops/out/wp7-r3.md`, at most 15 lines: files changed; each command + its last 3 output lines; deviations (none).
</digest>
