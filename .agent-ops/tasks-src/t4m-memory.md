# Task T4m: find and fix unbounded memory growth in the dashboard service

Measured (supervisor, 2026-09-24): the live service `python -m ground_station.service` (port 8081), started 04:08, streaming 2 subscribe slots from the drone:
- working set 2022 MB at 07:16, 2596 MB at about 10:45. Roughly 190 MB/h, linear. The process should be flat at well under 300 MB.

Rules: no firmware edits, no probe, no flashing, no contact with 127.0.0.1:8081 (do not start, stop or query it). Use only services the tests start on ephemeral ports.

## Do
1. Find every container that grows per received frame or per sample and is never trimmed, in:
   - ground_station/service/ (state, sessions/recording, SSE clients and queues, the activity log, the co-pilot, the agent plans history);
   - ground_station/comm/wifi_bridge.py and the frame decoders.
   Look for: lists or dicts appended to per sample, `deque` without `maxlen`, per-client queues without bounds, caches keyed by timestamp, recordings that keep everything in RAM, and SSE subscriber lists that never remove dead clients.
2. For each one, give the file:line, what it holds, and its growth per frame.
3. Reproduce the leak: write a test that feeds N synthetic frames (reuse the existing test fixtures and frame builders) through the same path the live service uses. Measure with `tracemalloc` that retained memory grows with N before the fix and stays bounded after it.
4. Fix it with bounded structures (maxlen / ring buffers / spill to disk for recordings), and choose limits that keep the existing features working (history windows the UI or API use; check the consumers before choosing a limit).

## Acceptance
- The new bounded-memory test passes; paste its numbers (before and after).
- The full tree is green: `python -m pytest ground_station .agent-ops/tests -q -p no:cacheprovider -o faulthandler_timeout=120` (paste the last line).
- Write `.agent-ops/tasks/<your id>.result.md`, listing each root cause as "ROOT CAUSE: file:line - what".
- Commit on your branch; the message ends with: Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## Tools
Use only the tools listed as available to you. There is no `replace_in_files` tool; use `edit` or `bash`.

## Workspace rule (hard)
Work ONLY inside your own worktree (your starting cwd, under .worktrees/<id>). Never edit files in the main checkout. Commit on your branch there.

## First command (hard)
Run `git rev-parse --show-toplevel`. It MUST end in .worktrees/<id>. Repeat that check right before `git commit`. If it prints the main checkout, STOP and cd to the worktree path given at the bottom of this task. Edits or commits in the main checkout are a task failure.
