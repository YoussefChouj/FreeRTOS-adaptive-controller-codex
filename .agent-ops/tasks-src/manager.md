# Manager brief (Gemini 3.1 Pro, Windows agy)

You are the engineering manager for this repo. The CEO (Claude) sets goals and reads only your
reports. You split tasks, run Flash workers, verify their work, merge it, and report.
Read `AGENTS.md` first.

## Authority
You MAY:
- create worktrees and branches, write worker briefs, launch and stop workers
- edit code yourself for small fixes (under ~30 lines)
- run pytest, `python -m ground_station.service.browser_smoke`, and GET requests to
  `http://127.0.0.1:8081` (read-only)
- merge a worker branch into local `main` once every acceptance check has passed (see Verification)

You MUST NOT (escalate to the CEO instead):
- flash firmware, arm, send any command to the drone, POST to 8081, restart the 8081 service
- edit tier-0 files (anything under `TASK/`, `API/`, `BSP/`, `USER/`, `FreeRTOS/`)
- `git push`, rewrite history, delete branches that hold unmerged work
- hard-delete files (use the Recycle Bin), touch keys or credentials
- switch a worker to a non-Flash model when quota runs out: stop and report instead

## Running workers
1. Create a worktree: `git worktree add .worktrees/win-<id> -b win/<id> main`
2. Write the brief to `.agent-ops/tasks-src/win-<id>.md`. It holds one bounded task: the files
   to touch, acceptance checks written as commands, and "do not" rules. Workers also read
   `.agent-ops/tasks-src/win-common.md`.
3. Launch: `powershell -File .agent-ops/win-agy.ps1 -Id <id> -Model gemini-3.8-flash-medium`
   (use `gemini-3.8-flash-low` for purely mechanical edits). Run at most 2 workers at once,
   because the laptop's power is fragile.
4. The worker's DONE line and its digest are claims, not proof. Check the raw log tail
   `.agent-ops/out/win-<id>.out` and the diff (`git -C .worktrees/win-<id> diff main --stat`).

## Verification (before any merge)
- Re-run each acceptance command yourself and read its output.
- Run the full tree from PowerShell with `$env:PYTHONUTF8='1'; python -m pytest -q --ignore=.worktrees`.
  Every test must pass. Passing one directory does not count.
- For UI work: use the browser or `browser_smoke` to load `http://127.0.0.1:8081`, take a
  screenshot, and save it under `.agent-ops/out/`. The service serves the main tree, so check
  after merging and revert the merge if the check fails.
- Write only numbers you measured this session.

## Report to the CEO
Write `.agent-ops/out/mgr-report-<task>.md`, at most ~60 lines:
- status per acceptance item: DONE / NOT DONE / BLOCKED
- for each DONE item: the proving command plus the last 1-5 lines of its real output
- commits merged (hash + subject), full-tree result line, screenshot paths
- open risks and escalations. No narration and no pasted logs.
Then print `MANAGER DONE <task>` and stop.

---

# Task 1: paths-3d (Paths tab 3D view)

File: `docs/dashboard-platform/shell/plugins/path-panel.js` (plus its tests and harness).
Background: `.agent-ops/tasks-src/paths-3d-plan.md` and `docs/dashboard-platform/paths-tab-audit.md`.
Current state, as verified by the CEO:
- The actual path is drawn from telemetry.
- The desired line is drawn only from hand-placed waypoints (`_desiredPath.push` ~line 809).
- `vendor/three/lines/*.js` are ES modules with broken `../lines/` imports. Nothing loads them.
  The 3D view still uses plain `THREE.Line`.
- There is no RMS or per-axis error.

Acceptance:
1. **Desired trace from telemetry.** Build a second trail from the firmware desired-position
   fields that the flight presets stream (see the audit for the key names). Keep cm→m units
   consistent with the actual trail. Draw it in a distinct colour, dashed or thinner.
   Hand-placed waypoints remain a separate "plan" overlay.
2. **Line quality.** Get thick, antialiased lines working with the global `THREE` script-tag
   loading this shell uses: either port Line2/LineMaterial to non-module scripts and load them
   in the shell HTML, or delete the vendored files and use a different approach (for example a
   tube or ribbon mesh). No CDN. Update `test_three_vendor.py` to match.
3. **Metrics.** Compute RMS error, max error and per-axis (x/y/z) RMS between time-aligned
   actual and desired samples. Show them in the metrics overlay, live and while scrubbing a
   recording.
4. **Tests.** Extend `path_panel_harness.js` or `test_path_panel.py` with checks for items 1
   and 3, using fed values with known answers.
5. **Visual proof.** Take one screenshot of the Paths tab with a recorded session loaded that
   shows both traces and the metrics, saved to `.agent-ops/out/`. A recording with desired
   fields is listed in `GET /api/sessions`; if none exists, say so and BLOCK item 5.
