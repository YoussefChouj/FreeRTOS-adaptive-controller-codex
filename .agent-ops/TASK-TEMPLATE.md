# Task: <one imperative line — what the worker must accomplish>

Copy this file, fill every section, save it under `.agent_state/task-<name>.md`,
and spawn with `-File`. Sections marked FIXED are boilerplate: keep them as they
are. The rest is the thinking you owe the worker.

A task that cannot state its acceptance command is not ready to be spawned.

One topic per task (one panel, one module, one bug). Split anything larger into
several tasks: each starts with a clean context, which costs far fewer tokens than
one worker carrying the earlier topics' files into the later ones.

## Goal

Two to four sentences. What must be TRUE when this is done — not what the
worker should do. State the one constraint that matters most explicitly; if
the default path is in daily use and must not change, say so here.

## Files in scope

- `path/to/file` — what changes in it and why

Name every file. A worker that has to guess which file to edit will edit the
wrong one.

## Files explicitly out of scope

Everything else. In particular: all firmware (`API/ TASK/ BSP/ USER/
Global_file/`), `ground_station/`, `OBJ/`, `.claude_state.md`,
`~/.claude/settings.json`, and `.agent-ops/run-worker.sh`.

Create no commits. Do not run `git add`.                          <!-- FIXED -->

## CRITICAL — run every command in the FOREGROUND     <!-- FIXED -->

Workers in this project have died having done **nothing** because they
backgrounded a command and went idle waiting for it. The harness terminates an
idle worker, and it exits `rc=0` with an auto-generated stub result file, so the
failure looks exactly like success.

**Do not background any command.** Run pytest, grep, builds and everything else
in the foreground and wait for the real output before continuing. Never write
"I will wait for X to complete" and then idle — **an idle worker is a dead
worker here.** If a command is slow, run it anyway and let it block; that is
correct behaviour, not a problem to work around.

Corollaries:
- Never pipe a `wait`.
- Budget the full test suite as your **last** step, not your first. The baseline
  is measured for you in the spec; you do not need to re-measure it to start.

## Things that will bite you                                      <!-- FIXED -->

- About 200 files are already dirty. Never run a bare `git status`; always
  filter to a path.
- `.agent-ops/` is untracked and `.claude/` is gitignored, so `git diff` shows
  nothing for either. Verify edits with `grep` / `sed -n` on the files.
- `agent-ops.ps1` is Windows PowerShell 5.1: no `&&`, no `||`, no ternary,
  no `??`. Match the existing style; do not restructure the script.
- Keep paths POSIX inside bash. The project is at `/mnt/c/...`.
- Do not introduce new dependencies. `jq` is not guaranteed.
- Windows tools (Windows Python, pyOCD, UV4) only work through
  `.agent-ops/win.sh "<cmd>"`. Flash/reset/halt/poke are refused with exit 126
  — those belong to the supervisor.
- **Never edit anything under `.worktrees/`.** Roughly ten stale copies of the
  repo's panel and source files live there. Editing one is a silent no-op that
  looks exactly like success. Always work from the repo-root path.
- `| tail -N` and `| head -N` do not exist in PowerShell 5.1. Inside
  `win.sh "<cmd>"` you are running a Windows command — use
  `Select-Object -Last N` / `-First N`, or do the filtering on the bash side.
- Verify that your work landed by **grepping the file's content**, never by its
  mtime. `.agent-ops/state.log` timestamps are UTC+8 and Windows `ls` is local
  time, so mtime comparisons across the two will mislead you.
- Every `file:line` this spec cites came from the supervisor's own grep and may
  be stale. **Re-read each site before editing it**, and say plainly in your
  result if a cited line, symbol or diagnosis turned out to be wrong. A spec
  claim you disprove is a valuable result, not a failure to do the work.
- Write your result to `.agent-ops/tasks/<your-id>.result.md`. A task that ends
  without that file is recorded as a failure regardless of what you did.
- Never use a heredoc to write a file. It corrupts on this pipeline (BEL
  injection). Use `python - <<` with an explicit write, or `sed`.

## How to verify — these steps are the definition of done

1. <cheapest static check: `bash -n`, a parse check, a compile>
2. <the real behavioural test, run against the REAL artifact — not a copy
   and not a stub>
3. <the failure path, not just the happy path>

Report the verbatim command and the verbatim output of each step. If a step
cannot be run honestly, say so and leave it untested rather than faking a pass.

## Acceptance command

One command the SUPERVISOR runs to check this work without reading the
worker's report:

```
<command>
```

Expected: <exact output, or exit code>

## Result file                                                    <!-- FIXED -->

Write to the result file: what changed in each file (a sentence each), the
verbatim output of every verification step, anything in this spec that turned
out to be wrong, open items, and the required `SUBSTITUTIONS:` line.
