# Task T4m2: the real 8081 memory leak (fix round 1)

Read `.agent-ops/tasks-src/t4m-memory.md`; it is binding. Step 0: `git merge --no-edit worker/20260924-105313`. Keep the plan and approval bounds from that round, but they are NOT the leak. The live service ran no agent plans overnight and still grew about 190 MB/h. The growth is per received telemetry frame or per sample.

## Do: measure, don't guess
1. Build a soak harness `ground_station/service/tests/soak_ingest.py` (a script, plus a pytest that runs a short version of it).
   - Start the service's ingest path the way `python -m ground_station.service` wires it: wifi_bridge or frame decoder callback → service state → recorder/session → SSE fan-out → co-pilot/agent state. Read `ground_station/service/__main__.py` and follow the real wiring.
   - Replace only the UDP socket with a fake source. The fake emits valid Frame A (0xAA 0xAA 0x01, 68 bytes) and subscribe slot frames (see docs/telemetry-protocol.md and the existing test frame builders) for 2 active slots.
   - Feed 50k, 100k and 200k frames, and attach one SSE client that reads.
2. After each batch take a `tracemalloc` snapshot and print the top 15 `compare_to(..., 'lineno')` lines. The file:line that keeps growing is the root cause. Also print `len()` of every candidate container.
3. Fix each growing container with a bound chosen from its consumers (check what the UI or API reads before picking a limit). Recordings that must keep everything go to disk, not RAM.
4. The pytest asserts that retained memory growth between the 100k and 200k batches is under 5 MB. Record the before-fix and after-fix numbers.

## Acceptance
- Paste the tracemalloc top lines before and after the fix into `.agent-ops/tasks/<your id>.result.md`, with `ROOT CAUSE: file:line - what`.
- The full tree is green: `python -m pytest ground_station .agent-ops/tests -q -p no:cacheprovider -o faulthandler_timeout=120` (paste the last line).
- `git add` AND `git commit` on your branch (a task with staged but uncommitted work is a failure); the message ends with: Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

Rules: no firmware edits, no probe, no flashing, no contact with 127.0.0.1:8081.

## Tools
Use only the tools listed as available to you. There is no `replace_in_files` tool; use `edit` or `bash`.

## Workspace rule (hard)
Work ONLY inside your own worktree (your starting cwd, under .worktrees/<id>). Run `git rev-parse --show-toplevel` first and right before committing; it MUST end in .worktrees/<id>.
