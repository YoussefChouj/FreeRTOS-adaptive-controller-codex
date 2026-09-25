#!/bin/bash
# test_vps_bridge.sh - Integration test for vps-bridge.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BRIDGE_SH="$REPO_ROOT/.agent-ops/vps-bridge.sh"

if [ ! -x "$BRIDGE_SH" ]; then
    echo "ERROR: $BRIDGE_SH not executable or not found" >&2
    exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

FAKE_BIN="$TMP_DIR/bin"
MOCK_OPS="$TMP_DIR/agent-ops"
MOCK_LOGS="$MOCK_OPS/logs"
MOCK_STATE="$MOCK_OPS/state.log"
MOCK_SEEN="$MOCK_LOGS/vps-bridge.seen"
MOCK_SSH_DATA="$TMP_DIR/canned_ssh.txt"

mkdir -p "$FAKE_BIN" "$MOCK_LOGS"

# Create fake ssh executable
printf '%s\n' \
'#!/bin/bash' \
'if [ -n "${FAKE_SSH_FAIL:-}" ]; then exit 255; fi' \
'if [ -f "${FAKE_SSH_DATA_FILE:-}" ]; then cat "$FAKE_SSH_DATA_FILE"; else echo "No fake data file set" >&2; exit 1; fi' \
> "$FAKE_BIN/ssh"
chmod +x "$FAKE_BIN/ssh"

export PATH="$FAKE_BIN:$PATH"
export OPS_DIR="$MOCK_OPS"
export STATE_LOG="$MOCK_STATE"
export LOGS_DIR="$MOCK_LOGS"
export SEEN_FILE="$MOCK_SEEN"
export FAKE_SSH_DATA_FILE="$MOCK_SSH_DATA"

fail_count=0
pass_count=0

assert_equals() {
    local expected="$1" actual="$2" desc="$3"
    if [ "$expected" = "$actual" ]; then
        echo "PASS: $desc"
        pass_count=$((pass_count + 1))
    else
        echo "FAIL: $desc" >&2
        echo "  Expected: $expected" >&2
        echo "  Actual:   $actual" >&2
        fail_count=$((fail_count + 1))
    fi
}

assert_grep() {
    local pattern="$1" file="$2" desc="$3"
    if grep -qE "$pattern" "$file" 2>/dev/null; then
        echo "PASS: $desc"
        pass_count=$((pass_count + 1))
    else
        echo "FAIL: $desc (pattern '$pattern' not found in $file)" >&2
        fail_count=$((fail_count + 1))
    fi
}

assert_not_grep() {
    local pattern="$1" file="$2" desc="$3"
    if ! grep -qE "$pattern" "$file" 2>/dev/null; then
        echo "PASS: $desc"
        pass_count=$((pass_count + 1))
    else
        echo "FAIL: $desc (pattern '$pattern' unexpectedly found in $file)" >&2
        fail_count=$((fail_count + 1))
    fi
}

echo "=== Test 1: Worker STARTED transition ==="
printf '%s\n' \
"=== RUN task-alpha ===" \
"--- TRANS ---" \
"STARTED 2026-09-25T15:00:00+00:00 model=gemini-3.1-pro-high" \
"--- LOG ---" \
"Line 1: init alpha" \
"Line 2: setup alpha" \
"=== END ===" > "$MOCK_SSH_DATA"

"$BRIDGE_SH" --once

assert_grep '\[vps-task-alpha\] STARTED: vps model=gemini-3.1-pro-high' "$MOCK_STATE" "state.log contains STARTED line"
assert_grep '[+-][0-9][0-9]:[0-9][0-9] \[vps-task-alpha\]' "$MOCK_STATE" "Timestamp ends with a UTC offset"
assert_grep 'Line 1: init alpha' "$MOCK_LOGS/vps-task-alpha.out" "Mirrored log contains Line 1"
assert_grep 'Line 2: setup alpha' "$MOCK_LOGS/vps-task-alpha.out" "Mirrored log contains Line 2"
assert_grep '^task-alpha	STARTED' "$MOCK_SEEN" "Seen file contains STARTED transition"

echo "=== Test 2: Idempotency (no duplicate state.log entries) ==="
"$BRIDGE_SH" --once
lines_count=$(grep -c '\[vps-task-alpha\]' "$MOCK_STATE")
assert_equals "1" "$lines_count" "No duplicate entries in state.log when transition unchanged"

echo "=== Test 3: Worker FALLBACK transition ==="
printf '%s\n' \
"=== RUN task-alpha ===" \
"--- TRANS ---" \
"FALLBACK after 120s on gemini-3.1-pro-high" \
"--- LOG ---" \
"Line 1: init alpha" \
"Line 2: setup alpha" \
"Line 3: fallback to qwen" \
"=== END ===" > "$MOCK_SSH_DATA"

"$BRIDGE_SH" --once
assert_grep '\[vps-task-alpha\] PROGRESS: fallback after 120s on gemini-3.1-pro-high' "$MOCK_STATE" "state.log contains PROGRESS fallback line"
assert_grep 'Line 3: fallback to qwen' "$MOCK_LOGS/vps-task-alpha.out" "Mirrored log has Line 3"

echo "=== Test 4: Worker DONE status=OK rc=0 ==="
printf '%s\n' \
"=== RUN task-alpha ===" \
"--- TRANS ---" \
"DONE rc=0 status=OK 2026-09-25T15:05:00+00:00" \
"--- LOG ---" \
"Line 4: finished successfully" \
"=== END ===" > "$MOCK_SSH_DATA"

"$BRIDGE_SH" --once
assert_grep '\[vps-task-alpha\] DONE: status=OK' "$MOCK_STATE" "state.log contains DONE: status=OK"
assert_grep '\[vps-task-alpha\] EXIT: rc=0' "$MOCK_STATE" "state.log contains EXIT: rc=0"
assert_not_grep '\[vps-task-alpha\] FAILED' "$MOCK_STATE" "state.log does NOT contain FAILED for status=OK"

echo "=== Test 5: Worker failure transitions (TIMEOUT, QUOTA, FAIL, AUTH) ==="
printf '%s\n' \
"=== RUN task-to ===" \
"--- TRANS ---" \
"DONE rc=124 status=TIMEOUT 2026-09-25T15:10:00+00:00" \
"--- LOG ---" \
"timeout log" \
"=== END ===" \
"=== RUN task-q ===" \
"--- TRANS ---" \
"DONE rc=1 status=QUOTA 2026-09-25T15:10:00+00:00" \
"--- LOG ---" \
"quota log" \
"=== END ===" \
"=== RUN task-f ===" \
"--- TRANS ---" \
"DONE rc=1 status=FAIL 2026-09-25T15:10:00+00:00" \
"--- LOG ---" \
"fail log" \
"=== END ===" \
"=== RUN task-a ===" \
"--- TRANS ---" \
"DONE rc=2 status=AUTH 2026-09-25T15:10:00+00:00" \
"--- LOG ---" \
"auth log" \
"=== END ===" > "$MOCK_SSH_DATA"

"$BRIDGE_SH" --once
assert_grep '\[vps-task-to\] DONE: status=TIMEOUT' "$MOCK_STATE" "TIMEOUT: DONE line"
assert_grep '\[vps-task-to\] FAILED: TIMEOUT' "$MOCK_STATE" "TIMEOUT: FAILED line before EXIT"
assert_grep '\[vps-task-to\] EXIT: rc=124' "$MOCK_STATE" "TIMEOUT: EXIT line"

assert_grep '\[vps-task-q\] DONE: status=QUOTA' "$MOCK_STATE" "QUOTA: DONE line"
assert_grep '\[vps-task-q\] FAILED: QUOTA' "$MOCK_STATE" "QUOTA: FAILED line before EXIT"
assert_grep '\[vps-task-q\] EXIT: rc=1' "$MOCK_STATE" "QUOTA: EXIT line"

assert_grep '\[vps-task-f\] DONE: status=FAIL' "$MOCK_STATE" "FAIL: DONE line"
assert_grep '\[vps-task-f\] FAILED: FAIL' "$MOCK_STATE" "FAIL: FAILED line before EXIT"
assert_grep '\[vps-task-f\] EXIT: rc=1' "$MOCK_STATE" "FAIL: EXIT line"

assert_grep '\[vps-task-a\] DONE: status=AUTH' "$MOCK_STATE" "AUTH: DONE line"
assert_grep '\[vps-task-a\] FAILED: AUTH' "$MOCK_STATE" "AUTH: FAILED line before EXIT"
assert_grep '\[vps-task-a\] EXIT: rc=2' "$MOCK_STATE" "AUTH: EXIT line"

echo "=== Test 6: SSH failure skips cycle silently ==="
state_before=$(cat "$MOCK_STATE")
FAKE_SSH_FAIL=1 "$BRIDGE_SH" --once
state_after=$(cat "$MOCK_STATE")
assert_equals "$state_before" "$state_after" "State log unchanged on ssh failure"

echo "=== Test 7: Pure function parsing (offline direct call) ==="
printf '%s\n' \
"=== RUN pure-task ===" \
"--- TRANS ---" \
"STARTED model=qwen" \
"--- LOG ---" \
"pure log output" \
"=== END ===" | (
    source "$BRIDGE_SH"
    parse_stream
)
assert_grep '\[vps-pure-task\] STARTED: vps model=qwen' "$MOCK_STATE" "Pure function parsing logs STARTED"
assert_grep 'pure log output' "$MOCK_LOGS/vps-pure-task.out" "Pure function parsing mirrors log"

echo ""
echo "=== Test Summary: $pass_count passed, $fail_count failed ==="
if [ "$fail_count" -gt 0 ]; then
    exit 1
fi
exit 0
