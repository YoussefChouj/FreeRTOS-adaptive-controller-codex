<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only. Never edit any .c or .h file.
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each file edit run `python -m py_compile <file>`; fix syntax before running tests.
4. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
5. Catch only specific exceptions. Bare `except:` and `except Exception: pass` are forbidden.
6. Keep the public API and every behaviour of round 2; all 32 existing tests must keep passing.
7. Python 3.10+, standard library plus PyYAML.
8. If a command or edit fails twice the same way, change approach; never repeat an identical call.
9. Write the digest `.agent-ops/out/wp2-r3.md` BEFORE printing DONE.
10. Output: no preamble, no summary prose.
11. NEVER put a statement after a colon on the same line (`if x: y` is forbidden). No f-string without a
    `{...}` placeholder. ruff is not installed on your host, so check these two rules by reading the file.
</guardrails>

<context>
FIX round, last one (HEAD = your wp2-r2 commit). The supervisor's gate run on Windows, verbatim:
```
FAIL size: 539/200 lines
PASS scope: 5 files
SKIP clang-tidy: no C changes
FAIL ruff: 3 new findings
  ground_station/flashtool/code_gate.py:69 [E701] Multiple statements on one line (colon)
  ground_station/flashtool/code_gate.py:70 [E701] Multiple statements on one line (colon)
  ground_station/flashtool/code_gate.py:74 [F541] f-string without any placeholders
PASS pytest: 32 passed in 0.59s
GATE FAIL: size,ruff
```
Review finding F4b: in `_overlaps_function`, `text_ahead` starts at the beginning of the matching line,
so a line like `if (x) { AutoflyTask_PathArbitrate(); }` sees the `{` BEFORE the name and is taken as the
definition. The scan must start at the match position (m.end()) of `<name>\s*\(`.
</context>

<allow-list>
ground_station/flashtool/code_gate.py
ground_station/flashtool/tests/test_code_gate.py
.agent-ops/out/wp2-r3.md          (digest)
</allow-list>

<spec>
1. Fix the 3 ruff findings (lines 69, 70, 74 of code_gate.py). The line-74 reason must name the path:
   f"Touched protected path: {pre_path or post_path}".
2. F4b: scan for the first `{` or `;` starting at m.end() on the matching line, then following lines.
3. Shrink code_gate.py without changing behaviour: one private `_log(self, record: dict)` helper that
   stamps "ts" from clock() and appends the JSON line (replace the 4 open/write blocks); one
   `_worse(self, j) -> bool` helper for the `lkg_j * (1 + tolerance_frac)` test used by steps 4 and 8.
</spec>

<tests>
Add one test: file text with `void f(void) { if (x) { AutoflyTask_PathArbitrate(); } }` on line 2 and the
real definition `static void AutoflyTask_PathArbitrate(void)` at line 5, body lines 6-9: a hunk at line 2
PASSES step 1, a hunk at line 7 FAILS step 1.
Run:
  python -m pytest -q -p no:cacheprovider ground_station/flashtool/tests/test_code_gate.py
  grep -nE "^\s*(if|for|while|else|elif)\b[^#]*:\s*\S" ground_station/flashtool/code_gate.py ground_station/flashtool/tests/test_code_gate.py   (expect no output)
</tests>

<digest>
`.agent-ops/out/wp2-r3.md`, at most 20 lines: files changed, each command + its last 3 output lines,
`wc -l` of code_gate.py and test_code_gate.py, deviations, open risks.
</digest>
