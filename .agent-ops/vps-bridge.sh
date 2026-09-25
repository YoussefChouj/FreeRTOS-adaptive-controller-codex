#!/bin/bash
set -euo pipefail
OPS_DIR="${OPS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
STATE_LOG="${STATE_LOG:-$OPS_DIR/state.log}"
LOGS_DIR="${LOGS_DIR:-$OPS_DIR/logs}"
SEEN_FILE="${SEEN_FILE:-$LOGS_DIR/vps-bridge.seen}"
SSH_HOST="${VPS_SSH_HOST:-oc-agent}"
mkdir -p "$LOGS_DIR"
touch "$SEEN_FILE"
REMOTE_SCRIPT='for f in ~/runs/*.out; do [ -f "$f" ] || continue; id=$(basename "$f" .out); echo "=== RUN $id ==="; echo "--- TRANS ---"; grep -E "^(STARTED|FALLBACK|DONE)" "$f" | tail -n 1; echo "--- LOG ---"; tail -n 300 "$f"; echo "=== END ==="; done'

log_state() {
    local id="$1" msg="$2" ts
    ts=$(TZ="${TZ:-Asia/Shanghai}" date -Iseconds)
    echo "$ts [vps-$id] $msg" >> "$STATE_LOG"
}

handle_transition() {
    local id="$1" trans="$2" tab="$(printf '\t')" prev rest model rc st
    [ -z "$trans" ] && return 0
    prev=$(grep -m1 "^${id}${tab}" "$SEEN_FILE" 2>/dev/null | cut -f2- || true)
    [ "$trans" = "$prev" ] && return 0
    if [[ "$trans" =~ ^STARTED ]]; then
        model=""; [[ "$trans" =~ model=([^ ]+) ]] && model="model=${BASH_REMATCH[1]}"
        log_state "$id" "STARTED: vps ${model:-${trans#STARTED }}"
    elif [[ "$trans" =~ ^FALLBACK ]]; then
        rest="${trans#FALLBACK}"; rest="${rest# }"
        log_state "$id" "PROGRESS: fallback${rest:+ $rest}"
    elif [[ "$trans" =~ ^DONE ]]; then
        rc=0; st="OK"
        [[ "$trans" =~ rc=([0-9]+) ]] && rc="${BASH_REMATCH[1]}"
        [[ "$trans" =~ status=([A-Za-z0-9_-]+) ]] && st="${BASH_REMATCH[1]}"
        log_state "$id" "DONE: status=$st"
        case "${st^^}" in QUOTA|AUTH|FAIL|TIMEOUT) log_state "$id" "FAILED: $st" ;; esac
        log_state "$id" "EXIT: rc=$rc"
    fi
    grep -v "^${id}${tab}" "$SEEN_FILE" 2>/dev/null > "$SEEN_FILE.tmp" || true
    printf '%s\t%s\n' "$id" "$trans" >> "$SEEN_FILE.tmp"
    mv -f "$SEEN_FILE.tmp" "$SEEN_FILE"
}

parse_stream() {
    local cur_id="" in_trans=0 in_log=0 trans_line="" line
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        if [[ "$line" =~ ^===\ RUN\ (.*)\ ===$ ]]; then
            cur_id="${BASH_REMATCH[1]}"; cur_id="${cur_id#vps-}"
            trans_line=""; in_trans=0; in_log=0; : > "$LOGS_DIR/vps-$cur_id.out.tmp"
        elif [ -n "$cur_id" ]; then
            if [ "$line" = "--- TRANS ---" ]; then in_trans=1; in_log=0
            elif [ "$line" = "--- LOG ---" ]; then in_trans=0; in_log=1
            elif [ "$line" = "=== END ===" ]; then
                in_trans=0; in_log=0; mv -f "$LOGS_DIR/vps-$cur_id.out.tmp" "$LOGS_DIR/vps-$cur_id.out"
                handle_transition "$cur_id" "$trans_line"; cur_id=""
            elif [ "$in_trans" -eq 1 ]; then trans_line="$line"
            elif [ "$in_log" -eq 1 ]; then printf '%s\n' "$line" >> "$LOGS_DIR/vps-$cur_id.out.tmp"
            fi
        fi
    done
}

run_cycle() {
    local raw
    if raw=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$SSH_HOST" "$REMOTE_SCRIPT" 2>/dev/null); then
        parse_stream <<< "$raw"
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    if [ "${1:-}" = "--once" ] || [ "${2:-}" = "--once" ]; then run_cycle
    else while true; do run_cycle; sleep 30; done; fi
fi
