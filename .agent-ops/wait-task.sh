#!/usr/bin/env bash
# wait-task.sh — block until a task needs the supervisor, without printing.
# Usage: wait-task.sh <task_id> <max_secs>
# Returns at once if the task already finished; otherwise on a NEW DONE,
# FAILED, EXIT, BLOCKED, NEEDS_INPUT, NET_DOWN or LOOP line for it.
# STALLED only means the worker has been quiet; it is not a reason to return.
# Exit codes (what happened, not just "an event was seen"):
#   0 DONE, or EXIT: rc=0 — worker finished successfully
#   2 FAILED, or EXIT: rc=<nonzero> — worker finished but failed
#   3 gave up after max_secs
#   4 attention needed: BLOCKED/NEEDS_INPUT/NET_DOWN/LOOP
#   5 EXIT: rc=0 IDLE_DEATH — no result file (worker likely terminated
#     while idle; it may still have made real edits)
set -uo pipefail

TASK_ID="$1"
MAX_SECS="$2"
STATE_LOG="${STATE_LOG:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/state.log}"

# The worker writes DONE/FAILED minutes before its process EXITs; return on either.
EVENTS='DONE|FAILED|EXIT|BLOCKED|NEEDS_INPUT|NET_DOWN|LOOP'

# Newest terminal event line for this task -> exit code.
# Returns: 0 rc=0 EXIT, 2 nonzero EXIT, 4 attention event, 1 no event yet.
event_rc() {
    local line rc
    line="$(grep -F "[$TASK_ID]" "$STATE_LOG" 2>/dev/null \
        | grep -E " ($EVENTS):" \
        | tail -n1)"
    [ -z "$line" ] && return 1
    case "$line" in
        *"] DONE:"*) return 0 ;;
        *"] FAILED:"*) return 2 ;;
        *"EXIT: rc="*)
            # rc=0 with no result file is an idle death, not a success.
            case "$line" in *IDLE_DEATH*) return 5 ;; esac
            rc="${line##*EXIT: rc=}"
            rc="${rc%%[!0-9]*}"
            if [ "$rc" = "0" ]; then return 0; else return 2; fi ;;
        *) return 4 ;;
    esac
}

# Fast path: only DONE/FAILED/EXIT is terminal. An old STALLED/BLOCKED must not make
# every later wait return instantly - a stalled worker is often still thinking.
event_rc
ec=$?
case "$ec" in 0|2|5) exit "$ec" ;; esac

start=$(wc -l < "$STATE_LOG")
deadline=$(( $(date +%s) + MAX_SECS ))
until tail -n +"$((start + 1))" "$STATE_LOG" | grep -F "[$TASK_ID]" | grep -qE " ($EVENTS):"; do
    [ "$(date +%s)" -ge "$deadline" ] && { echo "wait: gave up after ${MAX_SECS}s"; exit 3; }
    sleep 15
done
event_rc
exit $?
