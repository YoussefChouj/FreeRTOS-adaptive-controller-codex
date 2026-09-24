# Task T4m4: GET /api/debug/memory hangs on the live service

Measured by the supervisor: the live service was started with `PYTHONTRACEMALLOC=6`. `GET /state` answers in under 1 s, but `GET /api/debug/memory` gives no response within 110 s. The process holds about 130 MB of private memory and still leaks about 4 MB/min.

Find the cause in the endpoint added in commit 96dff1c (ground_station/service/api.py). Suspects:
- `statistics('traceback')` over a traced heap (slow);
- `gc.get_objects()` type counting;
- holding a service lock while doing either;
- `compare_to` against a huge previous snapshot.

## Fix
- Use `snapshot.filter_traces` to drop tracemalloc/importlib frames. The default grouping is `'lineno'` (fast); `?group=traceback` is opt-in.
- Object type counts only with `?objects=1`.
- Take no service lock.
- Keep the previous snapshot for diff_top.
- Add a test with 5 traceback frames and about 200k live allocations (create them in the test) that asserts the endpoint returns within 10 s.

## Acceptance
- Full tree green (paste the last line): `python -m pytest ground_station .agent-ops/tests -q -p no:cacheprovider -o faulthandler_timeout=120`.
- If the route list changed, regenerate `python -m ground_station.platform.capability_manifest`.
- Write `.agent-ops/tasks/<your id>.result.md` with `ROOT CAUSE: file:line - what`.
- `git add` AND `git commit` on your branch; the message ends with: Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

Rules: no firmware edits, no probe, no flashing, no contact with 127.0.0.1:8081. Test on ephemeral ports only.

## Tools
There is no `replace_in_files` tool; use `edit` or `bash`.

## Workspace rule (hard)
Work ONLY inside your worktree. `git rev-parse --show-toplevel` must end in .worktrees/<id>, both before editing and before committing.
