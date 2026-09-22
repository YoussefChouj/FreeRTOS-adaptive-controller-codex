# STANDING RULES — apply to every task in this project

These rules are prepended automatically to every worker spec. They are not
optional and they are not task-specific. The task description follows below.

## CRITICAL — run every command in the FOREGROUND

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

## Things that will bite you                                 

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
- **Work only in the checkout your task names.** If your task has a "Worktree
  mode" section, that worktree IS your checkout: edit there and nowhere else.
  Otherwise the repo root is your checkout and `.worktrees/` is off limits —
  it holds stale copies of the same panel and source files, and editing one is
  a silent no-op that looks exactly like success.
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

## Project safety rules — non-negotiable

- The drone is powered on and the **supervisor owns the hardware**. Do not
  flash, reset, halt or poke. Do not contend for the probe or UDP 14550 unless
  your task says you are the exclusive hardware owner for this run.
- The dashboard service may be running on port **8081**. **Never POST to it.**
- `s_ekf` is **shadow mode**. You may make its values visible. You may never
  wire them into a control path, setpoint or motor output.
- Firmware (`API/ TASK/ BSP/ USER/ Global_file/`) and `OBJ/` are READ ONLY
  unless your task explicitly grants write access.
- Never edit `.claude_state.md`, `.agent-ops/run-worker.sh`, or
  `~/.claude/settings.json`.
- Create no commits. Do not run `git add`.

## Code navigation protocol

Applies to every agent: interactive sessions, workers, dashboard agents.
Spec: `docs/dashboard-platform/AGENT_MAP_SPEC.md`. No vector RAG, no LLM-written metadata.

1. Exact name known: Serena `find_symbol` / `find_referencing_symbols` when the `serena` tools
   are loaded (Windows sessions and opencode workers), else `rg`/grep. Never `find`
   over the whole repo: the paths are in the task file or one Serena call away.
   In worktree mode Serena can also edit: prefer `replace_symbol_body` /
   `insert_after_symbol` over text search-and-replace, since they target the
   symbol by name and cannot corrupt an unrelated part of the file. Against the
   main checkout Serena is read-only and edit calls are refused by the server.
2. Firmware symbol, variable or module: `.agent-ops/win.sh "python -m ground_station.agent_map explain <name>"`
   (Windows Python; WSL python lacks pyelftools) before opening files. It covers firmware only.
3. Do not search `stm32_lib/`, `FreeRTOS/` or `OBJ/` unless the task is about vendor code.
4. Check the file's safety tier (spec, step 2) before editing. Tier 0 (flight-critical)
   needs explicit permission in the task.
5. Log navigation failures (could not find X, landed in the wrong file) to
   `.agent_memory/frictions.jsonl`.
6. After firmware sources are added or moved, regenerate clangd's database:
   `python -m ground_station.flashtool.compile_commands`.

## Honesty rules

- If a step cannot be run honestly, **say so and leave it untested rather than
  faking a pass.** A red result reported plainly is worth more than a green one
  the supervisor cannot trust.
- Never render a value you did not trace to a real source. An absent field must
  display as an explicit "not published" state — never `0`, never `—`, never a
  hopeful blank. A plausible zero is worse than an admitted absence.
- No synthetic, demo or placeholder data in shipped code. Synthetic data is
  legitimate **only** inside test harnesses.
- End your result with a `SUBSTITUTIONS:` line naming every stub, mock or
  synthetic value you used. Write "SUBSTITUTIONS: none" if there were none.


## Never background a command. An idle worker is a dead worker.

Run pytest, grep, builds and every other command in the **foreground** and wait
for the real output before continuing. If a command is slow, run it anyway and
let it block — that is correct.

Never emit a sentence of the form "I will wait for X to complete" and then stop
acting. The harness terminates an idle worker within seconds, at `rc=0`, with a
stub result file. It looks like success and is total loss of work.

This has now killed multiple workers. The most recent backgrounded its final
test-suite run, went idle, and was terminated having completed every other part
of its task — all of which nearly went unrecovered.

Budget the full suite as your **last** step, never your first.
