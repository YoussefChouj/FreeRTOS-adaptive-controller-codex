#!/usr/bin/env bash
# run-worker.sh — run one agy task inside its tmux window, with a watchdog.
# Started by spawn-worker.sh:  run-worker.sh <task_id> <timeout_secs>
# Env:    AGY_CMD         headless Antigravity invocation (prompt appended).
#         AGY_STALL_SECS  log STALLED after this long with no output and no
#                         status line (default: 600).
set -uo pipefail

TASK_ID="$1"
TIMEOUT_SECS="$2"
OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_LOG="$OPS_DIR/state.log"
TASK_FILE="$OPS_DIR/tasks/$TASK_ID.md"
RESULT_FILE="$OPS_DIR/tasks/$TASK_ID.result.md"
OUT_FILE="$OPS_DIR/logs/$TASK_ID.out"
AGY_CMD="${AGY_CMD:-agy --dangerously-skip-permissions -p}"
STALL_SECS="${AGY_STALL_SECS:-600}"

log() { echo "$(date -Iseconds) [$TASK_ID] $*" >> "$STATE_LOG"; }

# Peak RSS of this worker's process tree (sampled every 30 s by the watchdog).
# The tmux pane shell leads the session, so its SID covers every descendant
# (GNU timeout makes its own process group, so the PGID would not).
MEM_FILE="$OPS_DIR/logs/$TASK_ID.mem"
SID="$(ps -o sid= $$ | tr -d ' ')"

# Watchdog, every 30 s. Progress = worker output grew or it wrote a status
# line. It also scans each new chunk of output and logs, for the supervisor
# and the Windows monitor:
#   STALLED      no output or status for STALL_SECS (repeats; notes network)
#   NEEDS_INPUT  a tool/permission request was rejected (worker can't ask)
#   NET_DOWN     network/API errors in the output (at most every 5 min)
#   LOOP         the same error/failure set seen 3 times (likely stuck)
#   QUIET        output grows but no status line for 15 min
(
    sig=""
    t0=$(date +%s)
    off=0
    last_status=$(date +%s)
    last_net=0
    declare -A seen
    peak=0
    while sleep 30; do
        now=$(date +%s)
        rss=$(ps -eo sid=,rss= | awk -v s="$SID" '$1==s{t+=$2} END{print int(t/1024)}')
        [ "$rss" -gt "$peak" ] && { peak=$rss; echo "$peak" > "$MEM_FILE"; }
        n_status=$(grep -F "[$TASK_ID]" "$STATE_LOG" | grep -vcE ' (STALLED|NEEDS_INPUT|NET_DOWN|LOOP|QUIET):')
        size=$(stat -c %s "$OUT_FILE" 2>/dev/null || echo 0)
        s="$size:$n_status"
        if [ "$s" != "$sig" ]; then
            [ "${sig#*:}" != "$n_status" ] && last_status=$now
            sig="$s"
            t0=$now
        elif [ $(( now - t0 )) -ge "$STALL_SECS" ]; then
            net=ok
            curl -s -m 8 -o /dev/null https://www.google.com || net=down
            log "STALLED: no output or status for ${STALL_SECS}s (network: $net)"
            t0=$now
        fi
        if [ "$size" -gt "$off" ]; then
            chunk=$(tail -c +"$((off + 1))" "$OUT_FILE" | sed 's/\x1b\[[0-9;]*m//g')
            off=$size
            hit=$(printf '%s\n' "$chunk" | grep -m1 -iE 'auto-rejecting|rejected permission|user rejected' | cut -c1-140)
            [ -n "$hit" ] && log "NEEDS_INPUT: $hit"
            if [ $(( now - last_net )) -ge 300 ]; then
                hit=$(printf '%s\n' "$chunk" | grep -m1 -iE 'ECONNRESET|ETIMEDOUT|ENOTFOUND|EAI_AGAIN|fetch failed|socket hang up|network error|rate.?limit|\b(429|502|503|504)\b' | cut -c1-140)
                [ -n "$hit" ] && { log "NET_DOWN: $hit"; last_net=$now; }
            fi
            errs=$(printf '%s\n' "$chunk" | grep -E '^(FAILED|ERROR) |Error:|error:' | sort -u)
            if [ -n "$errs" ]; then
                h=$(printf '%s' "$errs" | md5sum | cut -c1-12)
                seen[$h]=$(( ${seen[$h]:-0} + 1 ))
                [ "${seen[$h]}" -eq 3 ] && log "LOOP: same failures 3x: $(printf '%s' "$errs" | head -n1 | cut -c1-110)"
            fi
        fi
        if [ $(( now - last_status )) -ge 900 ]; then
            log "QUIET: output growing but no status line for 15 min"
            last_status=$now
        fi
    done
) &
WATCHDOG=$!

# AGY_CMD is split into words on purpose.
RULES_FILE="$OPS_DIR/STANDING-RULES.md"
if [ -f "$RULES_FILE" ]; then
  PROMPT="$(cat "$RULES_FILE")

---

$(cat "$TASK_FILE")"
else
  PROMPT="$(cat "$TASK_FILE")"
fi

run_worker() {
    timeout --kill-after=30 "$TIMEOUT_SECS" $AGY_CMD "$PROMPT" 2>&1 | tee -a "$OUT_FILE"
    rc=${PIPESTATUS[0]}
}
start_ts=$(date +%s)
run_worker
# Transient startup failure (seen 2026-09-21 @043419): the eligibility
# preflight dies with an EOF from the profile-picture fetch, rc=1, within
# seconds and before any work could have happened. Retry once, and ONLY
# this exact signature: auth and quota failures must surface, and a worker
# that ran long enough to have done work must never be blindly re-run.
if [ "$rc" -eq 1 ] && [ $(( $(date +%s) - start_ts )) -le 30 ] \
        && grep -q 'Eligibility check failed: failed to get profile picture' "$OUT_FILE"; then
    log "RETRY: transient eligibility/network failure at startup (rc=1 after $(( $(date +%s) - start_ts ))s); retrying once in 15s"
    sleep 15
    run_worker
fi
kill "$WATCHDOG" 2>/dev/null

[ "$rc" -eq 124 ] && log "FAILED: timed out after ${TIMEOUT_SECS}s"
# Idle death (seen 2026-09-21): a worker that goes idle is terminated by the
# harness at rc=0, which looks exactly like a clean finish. rc=0 with no
# result file (or only an auto-generated stub) is recorded as IDLE_DEATH,
# not success — but real edits may exist (@63 died this way with four file
# edits landed), so check the working tree before re-running the task.
stub_first_line="$(head -n1 "$RESULT_FILE" 2>/dev/null)"
idle_death=0
if [ "$rc" -eq 0 ] && { [ ! -s "$RESULT_FILE" ] || [ "${stub_first_line#Worker wrote no result file}" != "$stub_first_line" ]; }; then
    idle_death=1
    log "IDLE_DEATH: rc=0 but no result file was written; the worker was likely terminated while idle. It may have made real edits — check the working tree and logs/$TASK_ID.out before re-running."
    printf 'IDLE DEATH — auto-generated stub, not a worker result.\nThe worker exited rc=0 without writing a result file, which means it was terminated after going idle, not that it finished. It may still have made real edits: check the working tree (git status, OBJ/ excluded) and .agent-ops/logs/%s.out before re-running this task.\n\nLast output lines:\n\n%s\n' \
        "$TASK_ID" "$(tail -n 10 "$OUT_FILE")" > "$RESULT_FILE"
elif [ ! -s "$RESULT_FILE" ]; then
    printf 'Worker wrote no result file (rc=%s). Last output lines:\n\n%s\n' \
        "$rc" "$(tail -n 10 "$OUT_FILE")" > "$RESULT_FILE"
fi
[ -s "$MEM_FILE" ] && log "MEM: peak worker RSS $(cat "$MEM_FILE") MB (WSL side)"
# Always the last line for this task; waiters key on it. IDLE_DEATH rides on
# the EXIT line (wait-task.sh turns it into its own exit code) so the
# last-line contract is unchanged.
if [ "$idle_death" -eq 1 ]; then
    log "EXIT: rc=0 IDLE_DEATH (no result file; worker likely terminated while idle)"
else
    log "EXIT: rc=$rc"
fi
