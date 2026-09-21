#!/usr/bin/env bash
# spawn-worker.sh — start an Antigravity CLI worker in a detached tmux window.
#
# Usage:  .agent-ops/spawn-worker.sh "task description"
#         .agent-ops/spawn-worker.sh -f path/to/task.md
#         -w / --worktree  run the worker in .worktrees/<task_id>, on branch
#                          worker/<task_id> created from HEAD (left in place
#                          after exit; the supervisor cleans it up).
# Env:    AGY_CMD      headless Antigravity invocation; the prompt is appended
#                      as the final argument
#                      (default: "agy --dangerously-skip-permissions -p").
#         AGY_SESSION  tmux session to host workers (default: agent-ops).
#         AGY_TIMEOUT  kill the worker after this many seconds (default: 7200).
#         ARK_MAX_WORKERS  hard cap for ark workers (AGY_CMD starts with
#                          "claude"; default: 2). Refuses (exit 3) at the cap.
#         ARK_SPAWN_GAP_SECS  minimum gap between ark spawns; the spawn sleeps
#                          out the remainder instead of bursting (default: 10).
set -euo pipefail

usage() { echo "usage: $0 \"task description\" | -f task_file [-w|--worktree]" >&2; exit 2; }

WORKTREE=0
while [ $# -gt 0 ]; do
    case "$1" in
        -w|--worktree) WORKTREE=1; shift ;;
        -f) [ -f "${2:-}" ] || usage
            TASK_TEXT="$(cat "$2")"; shift 2 ;;
        *) TASK_TEXT="$1"; shift ;;
    esac
done
[ -n "${TASK_TEXT:-}" ] || usage

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$OPS_DIR")"
STATE_LOG="$OPS_DIR/state.log"
AGY_CMD="${AGY_CMD:-agy --dangerously-skip-permissions -p}"
SESSION="${AGY_SESSION:-agent-ops}"
TIMEOUT_SECS="${AGY_TIMEOUT:-7200}"
WINDOW="agy-worker"

# Cap concurrent workers (12 GB host, WSL capped at 4 GB; see logs/*.mem).
MAX_WORKERS="${AGY_MAX_WORKERS:-3}"
LIVE_IDS="$(pgrep -af 'run-worker\.sh [0-9]{8}-[0-9]{6}' | grep -oE '[0-9]{8}-[0-9]{6}' | sort -u || true)"
LIVE="$(printf '%s\n' "$LIVE_IDS" | grep -c . || true)"
if [ "$LIVE" -ge "$MAX_WORKERS" ]; then
    echo "spawn-worker: $LIVE workers already running (AGY_MAX_WORKERS=$MAX_WORKERS); wait for one to exit" >&2
    exit 3
fi

TASK_ID="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OPS_DIR/tasks" "$OPS_DIR/logs"

# Worker type reaches us through AGY_CMD (agent-ops.ps1 maps -Worker to the
# headless command): ark is the "claude" binary pointed at the Volcengine
# Agent Plan, agy has its own binary.
WORKER_KIND="agy"
case "${AGY_CMD%% *}" in
    claude) WORKER_KIND="ark" ;;
esac
ARK_GAP_FILE="$OPS_DIR/.ark-last-spawn"
if [ "$WORKER_KIND" = "ark" ]; then
    ARK_MAX_WORKERS="${ARK_MAX_WORKERS:-2}"
    ARK_SPAWN_GAP_SECS="${ARK_SPAWN_GAP_SECS:-10}"
    # Bursts return HTTP 429 "System protection triggered by request burst"
    # and the CLI then retries silently for minutes; sleep out the gap first.
    if [ -f "$ARK_GAP_FILE" ]; then
        last="$(cat "$ARK_GAP_FILE" 2>/dev/null || echo 0)"
        case "$last" in ''|*[!0-9]*) last=0 ;; esac
        remain=$(( last + ARK_SPAWN_GAP_SECS - $(date +%s) ))
        if [ "$remain" -gt 0 ]; then
            echo "spawn-worker: last ark spawn was $(( $(date +%s) - last ))s ago; sleeping ${remain}s before spawn (ARK_SPAWN_GAP_SECS=$ARK_SPAWN_GAP_SECS)" >&2
            sleep "$remain"
        fi
    fi
    # Count only live ark workers; tasks/$id.worker records the kind (below).
    live_ark=0
    for id in $LIVE_IDS; do
        [ "$(cat "$OPS_DIR/tasks/$id.worker" 2>/dev/null)" = "ark" ] && live_ark=$((live_ark + 1))
    done
    if [ "$live_ark" -ge "$ARK_MAX_WORKERS" ]; then
        echo "spawn-worker: $live_ark ark workers already running (ARK_MAX_WORKERS=$ARK_MAX_WORKERS); put extra parallel work on -Worker agy" >&2
        exit 3
    fi
fi

# Rotate a bloated state.log only with zero live workers: waiters tail by line
# offset and the watchdog greps per worker, so rotation mid-run corrupts them.
if [ "$LIVE" -eq 0 ] && [ -f "$STATE_LOG" ] && [ "$(stat -c %s "$STATE_LOG")" -gt 1048576 ]; then
    mv -f "$STATE_LOG" "$STATE_LOG.1"
    : > "$STATE_LOG"
    echo "$(date -Iseconds) [rotate] state.log rotated to state.log.1 at spawn (was over 1 MiB)" >> "$STATE_LOG"
fi
TASK_FILE="$OPS_DIR/tasks/$TASK_ID.md"
RESULT_FILE="$OPS_DIR/tasks/$TASK_ID.result.md"

# Prompt goes to a file so tmux send-keys never has to quote free text.
cat > "$TASK_FILE" <<EOF
$TASK_TEXT

---
Evidence contract (required for every task):
- For each verification step, paste the VERBATIM command and its VERBATIM
  output. No descriptions or summaries in place of either.
- One line must begin exactly "SUBSTITUTIONS:" listing every stub, mock,
  fake binary, shortened dataset or other stand-in used anywhere in
  verification, or "SUBSTITUTIONS: none". Stubs are allowed; an undisclosed
  stand-in is a failed task.
- Any step you could not run gets a "NOT RUN: <reason>" line; never report
  success for a step that did not actually run.

---
Status reporting (required): append one-line status updates to
$STATE_LOG
using the format "<ISO-8601 time> [$TASK_ID] <STATUS>: <message>",
where STATUS is one of STARTED, PROGRESS, BLOCKED, DONE, FAILED.
Append only; never truncate or rewrite that file.
Write a PROGRESS line at least every 5 minutes of work; silence for
10 minutes is reported to the supervisor as a stall.
Write DONE or FAILED as your final line.
If you are stuck (same approach failed 3 times, a tool call was rejected,
or you need a decision), write BLOCKED: <the question> and stop, rather
than guessing or retrying. The supervisor answers by respawning you.

Result file (required): before your final status line, write
$RESULT_FILE
in at most 20 lines: files changed, the verification you ran and its
result, and anything left open. The supervisor reads this, not your log.

Environment: you run in WSL2; the project is on the Windows filesystem.
Windows-only tooling (python -m ground_station.*, pyOCD probe, Keil UV4)
must go through:  $OPS_DIR/win.sh "<command>"
win.sh kills the Windows command after 600 s and caps it at 1.5 GB RAM
(override with WIN_TIMEOUT=<secs> / WIN_MEM_MB=<MB>). Run only the pytest
files your change touches; never the whole ground_station suite.

Budget discipline (you are on a metered quota): grep or glob before reading,
read with line ranges instead of whole files, and pipe long command output
through head or tail. Do not re-read what you have already read, and do not
restate the task or your plan in your output.
Run every command in the FOREGROUND and wait for it. Never start background
tasks or subagents: the worker process exits as soon as you go idle, and any
background task is killed with it, losing your work.

Hardware rules:
- Never flash, reset, halt or poke the drone. The supervisor does that.
  If a task needs a flash, build only, then report DONE with
  "ready to flash" in the message.
- Build command (Keil, from project root):
  $OPS_DIR/win.sh "& 'C:\\Keil_v5\\UV4\\UV4.exe' -b -t JX_FLY -j0 USER\\JX_FLY.uvprojx -o build.log; Get-Content USER\\build.log -Tail 5"
- Follow AGENTS.md conventions (Keil ARMCC V5.06 C, no C99-only constructs).
EOF

# Optional isolated checkout. Created from HEAD and left in place on exit;
# cleanup (git worktree remove / branch delete) is the supervisor's job.
# A failure here is fatal: a worker that thinks it is isolated but is not is
# worse than no spawn at all.
WORKTREE_DIR=""
if [ "$WORKTREE" -eq 1 ]; then
    WT_REL=".worktrees/$TASK_ID"
    WT_PATH="$PROJECT_DIR/$WT_REL"
    WT_BRANCH="worker/$TASK_ID"
    if [ -d "$WT_PATH" ]; then
        # Same task id re-run: reuse only if git already has this exact path
        # registered as a worktree on this exact branch.
        if git -C "$PROJECT_DIR" worktree list --porcelain 2>/dev/null | grep -Fxq "worktree $WT_PATH" \
            && git -C "$PROJECT_DIR" worktree list --porcelain 2>/dev/null | grep -Fxq "branch refs/heads/$WT_BRANCH"; then
            echo "spawn-worker: reusing existing worktree $WT_REL ($WT_BRANCH)" >&2
        else
            echo "spawn-worker: $WT_REL exists but is not a worktree on $WT_BRANCH; clean it up or use a new task id" >&2
            exit 4
        fi
    elif ! git -C "$PROJECT_DIR" worktree add --quiet "$WT_REL" -b "$WT_BRANCH"; then
        echo "spawn-worker: git worktree add $WT_REL -b $WT_BRANCH failed (branch already exists?); not spawning" >&2
        exit 4
    fi
    WORKTREE_DIR="$WT_PATH"
    # The template above is full of main-root absolute paths (.agent-ops/...);
    # without this the agent takes its shell Cwd from those and undoes the
    # isolation it was spawned with.
    cat >> "$TASK_FILE" <<EOF

Worktree mode: this task runs in $WT_PATH (branch $WT_BRANCH). That directory
is your working directory: use it for every relative path in the task, and
never cd into the main checkout. The absolute .agent-ops paths above are only
for status logging and the result file.
EOF
fi

# Claude Code refreshes the git index at startup. In this repo, over the 9p
# /mnt/c mount, that re-reads all 866 tracked files (111 MB of them dirty
# OBJ/) and never returns: measured rc=124 after 301 s with zero bytes on
# stdout and stderr, which reads exactly like a quota death. Give ark a cwd
# that is not a git repo and attach the tree with --add-dir instead: same
# task, 59 s, correct answer. See .claude_state.md "Wave 6", AGENT_GUIDE
# trap 6. Do not "fix" this with core.checkStat/trustctime - measured, no.
TARGET_DIR="${WORKTREE_DIR:-$PROJECT_DIR}"
LAUNCH_DIR="$TARGET_DIR"
if [ "$WORKER_KIND" = "ark" ]; then
    LAUNCH_DIR="${ARK_CWD:-/tmp/arkwork/$TASK_ID}"
    mkdir -p "$LAUNCH_DIR"
    # run-worker.sh word-splits AGY_CMD on purpose, so no quoting here; these
    # paths have no spaces.
    # Compact early: every call resends the whole context, and Ark bills cached
    # input at the full rate. CLAUDE_CODE_AUTO_COMPACT_WINDOW (the variable Ark's
    # Claude Code guide documents) makes Claude Code treat the window as 256k
    # whatever the model's real one is; every Agent Plan model has at least 256k.
    # --max-turns only stops a runaway loop; it does not lower the cost per turn.
    # --strict-mcp-config with no --mcp-config loads no MCP servers, whose tool
    # schemas would otherwise be resent on every call.
    AGY_CMD="env CLAUDE_CODE_AUTO_COMPACT_WINDOW=${ARK_COMPACT_WINDOW:-256000} ${AGY_CMD% -p} --add-dir $TARGET_DIR --max-turns ${ARK_MAX_TURNS:-120} --strict-mcp-config -p"
    cat >> "$TASK_FILE" <<EOF

Token budget (the Ark plan is metered; every tool call resends your whole context):
- Grep before reading; read files with offset/limit. Never cat a whole log, JSON
  dump or large file. Pipe every command's output through head or tail (-20 or less).
- Run only the test files you added or changed, output through tail -5. Do not run
  the full suite unless this task explicitly says so; the supervisor runs it before
  every commit, and when a task does ask for it, run it once.
- You have at most ${ARK_MAX_TURNS:-120} turns. Write the result file before you run out.

Your shell starts in $LAUNCH_DIR, which is outside the repo on purpose: git
run from WSL inside the repo takes minutes and would hang you at startup.
The project is attached at $TARGET_DIR. Use absolute paths under it for
every file you read or write, do not cd into it, and run git (and anything
else needing Windows tooling) through $PROJECT_DIR/.agent-ops/win.sh, which
executes on the Windows side where git is fast.
EOF
fi

echo "$(date -Iseconds) [$TASK_ID] SPAWNED: $(head -n1 "$TASK_FILE" | cut -c1-120)" >> "$STATE_LOG"
if [ -n "$WORKTREE_DIR" ]; then
    echo "$(date -Iseconds) [$TASK_ID] WORKTREE: $WT_REL branch $WT_BRANCH" >> "$STATE_LOG"
fi

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux new-session -d -s "$SESSION" -n shell
    # Status bar: newest state.log line, refreshed every 10 s.
    tmux set -t "$SESSION" status-interval 10
    tmux set -t "$SESSION" status-right-length 120
    tmux set -t "$SESSION" status-right "#(tail -n1 '$STATE_LOG' | cut -c12-19,26-130)"
fi
# Target by window id: several workers may share the "agy-worker" name.
WIN_ID="$(tmux new-window -d -P -F '#{window_id}' -t "$SESSION" -n "$WINDOW" -c "$LAUNCH_DIR")"

# Durable kind marker: the ark cap above identifies live ark workers via it;
# prune's retention sweeps the file with the rest of the task's files.
echo "$WORKER_KIND" > "$OPS_DIR/tasks/$TASK_ID.worker"
# Stamp only once the launch is committed, so a failed spawn can't poison the gap.
[ "$WORKER_KIND" = "ark" ] && date +%s > "$ARK_GAP_FILE"

# run-worker.sh records EXIT itself (plus timeout and stall watchdog), so the
# log stays truthful even if the agent never writes to it.
tmux send-keys -t "$WIN_ID" \
    "AGY_CMD='$AGY_CMD' '$OPS_DIR/run-worker.sh' '$TASK_ID' '$TIMEOUT_SECS'" Enter

echo "spawned $TASK_ID in $SESSION ($WIN_ID) result: $RESULT_FILE"
if [ -n "$WORKTREE_DIR" ]; then
    echo "worktree: $WT_REL on $WT_BRANCH (left in place after exit)"
fi
