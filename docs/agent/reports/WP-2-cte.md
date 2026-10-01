Status: DONE
Commits: 8014918, 4b1f42f, b9832e0 - worker wp2-r1..r3 (agy/gemini-3.1-pro-high), kept as is apart from the parser
         31797af - cte(wp2): walk diff hunks by header counts; refuse malformed hunks   (on wp/2)
Gate: GATE PASS   (--max-lines 600 per CEO decision; PASS size 583/600; PASS scope 10 files; SKIP clang-tidy: no C
      changes; PASS ruff 2 files clean; PASS pytest 37 passed)
Verification (run by me, 2026-10-01):
- base line: not re-measured by me (the denied `git checkout` below ruled out swapping files); the manager measured
  102 passed on workflow-b@0bc6a4a. The current tree has 0 failed, so there are no new failures either way.
- pytest ground_station/flashtool/tests/test_code_gate.py -> 37 passed in 0.58s
- pytest ground_station/flashtool/tests -> 139 passed in 4.25s (manager's base 102 + 37 code-gate tests)
- gate.py --base workflow-b --max-lines 600 --allow (brief globs + reports/research/tasks-src) -> GATE PASS
- Old parser vs the new tests (HEAD source exec'd in memory over the module, worktree untouched):
  3 of the 4 new step-1 cases returned ok=True on the old code, so they are real bypasses that are now closed.
CTE, effort medium
- `_parse_diff` now reads `@@ -a,b +c,d @@`, then consumes exactly b old and d new body lines (' '/'' context
  counts toward both, '-' toward old, '+' toward new, '\ No newline' toward neither). It no longer checks body
  lines for a `--- ` or `+++` prefix. A body line with any other tag, or a hunk longer than its header, raises
  ValueError, and `check_change` turns that into a step 1 refusal "Malformed diff: ...". A hunk cut short at EOF
  is still accepted (many existing tests use such headers, and an EOF cut leaves no lines to hide).
- New step-1 cases in test_code_gate.py:
  a) CEO case: removed `-- x;` (diff `--- x;`) inside a PROTECTED region -> refused "protected region". The old
     parser also refused this one, because the header span alone overlapped, so it stays as a regression guard.
  b) a hunk outside the region removing `-- x;` and adding `++ y;`, then a second hunk inside the region ->
     refused. The old parser read `--- x;`/`+++ y;` as a file header pair, moved post_path to "y;", skipped the
     region check, and PASSED the gate.
  c) removed `-- test_param;` -> refused "param ID". The old parser never put that line in `removed` and PASSED.
  d) a header that undercounts, so the next `@@` falls inside the hunk -> refused "Malformed diff". The old parser
     PASSED it.
Deviations / open questions:
- The fail-closed malformed-hunk check goes past the letter of the CEO fix: about 6 lines. Without it, a header
  that overcounts would swallow the next `@@` hunk and skip its region check (case d).
- Remaining trust assumption: the gate trusts that the diff and files_after describe the real change. Whoever
  controls both can leave hunks out. The runner should make the diff with `git diff` itself and never accept one
  from the agent. PROPOSED for the runner WP. Not enforced here.
- Unchanged from the manager report: step 9 has only a pass test; protected_set.yaml `unresolved:` flash-when-armed
  block; SIL default fails closed, and whether sim/bench/c_ref can host it is not verified.
- Rules: one denied command, `git checkout HEAD -- ground_station/flashtool/code_gate.py` (meant as a temporary
  swap to test the old parser). I did not retry it and used an in-memory exec from a scratchpad script instead.
