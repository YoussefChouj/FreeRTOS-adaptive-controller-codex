# Task T4m5: finish the /api/debug/memory hang fix (salvaged from a stalled worker)

A previous worker fixed `GET /api/debug/memory` in `ground_station/service/api.py` but never wrote a
working test or committed. Its files are saved here (read-only references):
- `.agent-ops/tasks-src/t4m5-api.py.salvage` — full api.py with the fix (diff vs main is ~56 lines,
  all inside the `elif route == "/api/debug/memory":` branch near line 1383).
- `.agent-ops/tasks-src/t4m5-test.py.salvage` — its 305-line test attempt. It spent 3 h on this file.
  Do NOT try to repair it. Write a fresh, short test instead.

## Steps
1. In your worktree, copy the salvage api.py over `ground_station/service/api.py`. Confirm with
   `git diff --stat` that only that file changed (~30+/26-).
2. Add one improvement: after `take_snapshot()`, apply
   `_snap = _snap.filter_traces([tracemalloc.Filter(False, tracemalloc.__file__), tracemalloc.Filter(False, "<frozen importlib._bootstrap>"), tracemalloc.Filter(False, "<unknown>")])`.
   Take no service lock anywhere in this branch.
3. Write `ground_station/service/tests/test_debug_memory.py` from scratch, **at most 80 lines**.
   Do not start an HTTP server. Look at how an existing test in `ground_station/service/tests/` calls
   a GET route of `make_handler` (grep `make_handler` there, copy the smallest existing pattern).
   Tests:
   a. default call: 200, JSON has `tracing`, `top`, `diff_top`; no `top_types` key.
   b. `?objects=1`: `top_types` present.
   c. speed: `tracemalloc.start(5)`, allocate `keep = [bytearray(64) for _ in range(200_000)]`,
      call the endpoint twice, assert each call < 10 s; `tracemalloc.stop()` in a finally.
   If after 3 attempts the handler pattern still does not work, write BLOCKED and stop.
4. Run only that test file, then the full tree once as the LAST step:
   `python -m pytest ground_station .agent-ops/tests -q -p no:cacheprovider -o faulthandler_timeout=120`
   (through win.sh). Paste the last line.
5. `git add` both files and `git commit` on your branch; the message ends with
   `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
6. Result file: `ROOT CAUSE: ground_station/service/api.py:<line> - <what>` (the old code ran
   `gc.get_objects()` counting and `statistics("traceback")` on every call and compared against itself).

Rules: no firmware edits, no probe, no flashing, no contact with 127.0.0.1:8081.
This task overrides the standing "create no commits" rule: commit on your branch only.

## Workspace rule (hard)
Work ONLY inside your worktree. `git rev-parse --show-toplevel` must end in .worktrees/<id>,
both before editing and before committing.
