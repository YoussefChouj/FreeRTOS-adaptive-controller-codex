# Task T4m5b: fix round on branch worker/20260924-171824 (commit 91fd706)

Start your worktree from branch `worker/20260924-171824` (git checkout it inside your worktree, or
`git merge worker/20260924-171824`). Two defects in that commit:

1. **Line endings.** `git diff --stat main worker/20260924-171824` shows api.py with 4583 changed
   lines; the real change is ~60 lines. The file's line endings were converted. Restore the original
   line endings of `ground_station/service/api.py` (check main's with `git show main:ground_station/service/api.py | file -`
   or python `b"\r\n" in data`). Acceptance: `git diff --stat main -- ground_station/service/api.py`
   shows under 100 changed lines. Do the same check for the test file.
2. **filter_traces is the slow part** (measured by the previous worker: ~47 s with 200k allocations).
   Remove the `filter_traces` call. Instead, skip tracemalloc/importlib frames only when building the
   top-25 output list (filter the already-sliced `statistics("lineno")` result, which is cheap).
   Then `TestSpeed::test_speed` (two calls, each < 10 s) must pass. Do not weaken the test's
   thresholds or allocation count. If it still fails, report measured per-step timings
   (take_snapshot, statistics, compare_to) in the result and write BLOCKED.

## Verification (paste verbatim last lines)
- `python -m pytest ground_station/service/tests/test_debug_memory.py -q -p no:cacheprovider`
- Full tree, LAST step, once: `python -m pytest ground_station .agent-ops/tests -q -p no:cacheprovider -o faulthandler_timeout=120`
  Run it with `WIN_TIMEOUT=1500` on win.sh. If a hardware-probe test hangs it, add
  `--deselect <nodeid>` for that single test and name it in the result.

Commit on your branch (message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`).
This task overrides the standing "create no commits" rule: commit on your branch only.
Rules: no firmware edits, no probe, no flashing, no contact with 127.0.0.1:8081.

## Workspace rule (hard)
Work ONLY inside your worktree. `git rev-parse --show-toplevel` must end in .worktrees/<id>.
