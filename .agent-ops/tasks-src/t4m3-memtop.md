# Task T4m3: a memory diagnostics endpoint for the live service

Context (measured by the supervisor): the live 8081 service holds 4029 MB private memory, growing about 12 MB/min, with 8 threads and 270 handles, both flat. The soak harness `ground_station/service/tests/soak_ingest.py` (on this branch) found NO growth in the ingest path. So the leak comes from something only the live process does. The supervisor will restart the service with tracing on and read your endpoint.

Step 0: `git merge --no-edit worker/20260924-114419`.

## Build
1. `GET /api/debug/memory` in ground_station/service/api.py. It is GET only and read-only. Register it in the routes list (`GET /api/routes`).
   - When `tracemalloc.is_tracing()` is true it returns JSON with:
     - `traced_mb`, `peak_mb`;
     - `top`: the top 25 `snapshot.statistics('traceback')` entries, each with size_kb, count and a traceback (up to 6 frames, file:line);
     - `diff_top`: the top 25 lines by growth since the previous call (`compare_to(prev, 'lineno')`; keep the previous snapshot in memory).
   - Always include:
     - `process`: rss/private bytes via psutil if it is installed, else null;
     - `gc_counts`;
     - the top 20 object types by count from `gc.get_objects()` (type name → count).
   - When not tracing it returns `tracing: false` plus the always-on fields.
2. Tracing is enabled only by the env `PYTHONTRACEMALLOC=<frames>` (Python handles it), and not otherwise.
3. Fix soak_ingest.py so that it never writes recordings into the repo's `logs/` directory. Use a temp dir (`tmp_path` in pytest, `tempfile.mkdtemp()` in the script). Its last run left an 800,000-row recording in main's `logs/`.
4. Tests: the endpoint returns 200 both with tracing and without it; `diff_top` appears on the second call. Use a service on an ephemeral port only.

## Acceptance
- The full tree is green: `python -m pytest ground_station .agent-ops/tests -q -p no:cacheprovider -o faulthandler_timeout=120` (paste the last line).
- Write `.agent-ops/tasks/<your id>.result.md`.
- `git add` AND `git commit` on your branch. Staged but uncommitted work is a task failure. The message ends with: Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

Rules: no firmware edits, no probe, no flashing, no contact with 127.0.0.1:8081.

## Tools
Use only the tools listed as available to you. There is no `replace_in_files` tool; use `edit` or `bash`.

## Workspace rule (hard)
Work ONLY inside your own worktree. Run `git rev-parse --show-toplevel` first and right before committing; it MUST end in .worktrees/<id>.
