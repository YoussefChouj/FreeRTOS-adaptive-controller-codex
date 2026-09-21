# Handoff: you are taking over the overnight run from Claude Code

Claude Code (the supervisor) stopped responding, most likely because its usage limit was hit.
The watchdog (`.agent-ops/watchdog/watchdog.py`) started you. You work on Windows in this repo.

## Read first (only these, in order)
1. `.agent_state/overnight-queue.md` — the task queue. Take the first item not marked DONE.
2. `docs/dashboard-platform/AUDIT_2026-09-21.md` — the operator's audit (source of truth for "what is wrong").
3. `AGENTS.md` — project rules.
4. `docs/dashboard-platform/AGENT_GUIDE.md` — dashboard service and UI.

## How to work
- One queue item at a time. Before starting an item, append `- [agy HH:MM] START <item>` to
  `.agent_state/agy-progress.md`; when done, append `DONE <item>: <one line: what changed, how verified>`.
  Mark the item DONE in the queue file.
- Commit each finished item on `main` (never commit `OBJ/`); message ends with
  `Co-Authored-By: Antigravity agy <noreply@google.com>`. Scope `git status`/`git add` to paths.
- Tests: `python -m pytest -q ground_station/analysis/tests ground_station/service/tests ground_station/platform/tests ground_station/comm/tests`
  (baseline 437 passed, 37 skipped). Do not regress it.
- Test the dashboard on your own service instance only after stopping any running one; never leave
  two instances fighting for UDP 14550.

## Firmware
- Build: from `USER/`, `UV4 -b -t JX_FLY -j0 JX_FLY.uvprojx -o build.txt`, run with a 10-minute timeout.
  Keil sometimes opens its GUI or hangs: if UV4 is still running after the timeout, kill `UV4.exe`
  and retry once. Only one build/flash at a time.
- Flash (allowed, reviewed changes only, drone disarmed): `python -m ground_station.flashtool.rebuild_and_flash --force --yes`,
  then `python -m ground_station.livewatch verify` must show 0 mismatches.
- Keil ARMCC V5.06 C: declarations at block top, no C99-only constructs.

## Hard limits (never break these)
- Never initialize or spin the motors: no bench-test, no motor-init / RC-gesture emulation, no arm
  commands. All other drone commands are allowed.
- Never read or print credentials, API keys, settings `env`/`headers`, or Claude transcripts.
- Never change proxy/VPN settings (the watchdog owns that).
- EKF active mode may only be selected while disarmed and must fall back to fixed on health failure.

## Stopping
- Before each new queue item, check whether `.agent_state/CLAUDE_BACK` exists. If it does, Claude Code
  is back: finish/revert to a clean state, append `HANDBACK: <summary>` to `.agent_state/agy-progress.md`, and stop.
- When the queue is empty, write the morning report to `.agent_state/MORNING_REPORT.md` and stop.
