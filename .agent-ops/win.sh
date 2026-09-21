#!/usr/bin/env bash
# win.sh — run a command in Windows PowerShell from the project root.
# Use for anything that needs Windows tooling: the debug probe (pyOCD),
# Keil UV4, and the Windows Python env (ground_station.*).
#
# Usage:  .agent-ops/win.sh "python -m ground_station.livewatch verify"
# Exit code is the Windows command's exit code (124 if WIN_TIMEOUT hit).
#
# Commands that write to the drone (flash, reset, halt, poke) are refused
# unless AGENT_OPS_ALLOW_HW=1 — workers build, the supervisor flashes.
set -uo pipefail

if [ $# -lt 1 ]; then
    echo "usage: $0 \"<powershell command>\"" >&2
    exit 2
fi

HW_WRITE='rebuild_and_flash|safe_flash|flash-write|flash-erase|UV4(\.exe)?.* -f|livewatch.* (poke|reset|halt|step|resume|bp|wp|rtt-write|fault-erase)( |$)'
if [ "${AGENT_OPS_ALLOW_HW:-0}" != "1" ] && echo "$*" | grep -qiE "$HW_WRITE"; then
    echo "win.sh: refused — drone-writing command needs the supervisor (AGENT_OPS_ALLOW_HW=1)" >&2
    exit 126
fi

PROJECT_DIR="$(dirname "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")"
cd "$PROJECT_DIR" || exit 1

# Run inside a Windows Job Object (win-run.ps1): the Windows process tree is
# killed after WIN_TIMEOUT seconds and capped at WIN_MEM_MB, because killing
# this script from WSL does not stop what it started on Windows.
CMD_B64="$(printf '$ProgressPreference = "SilentlyContinue"; %s; exit $LASTEXITCODE' "$*" | iconv -t UTF-16LE | base64 -w0)"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w "$PROJECT_DIR/.agent-ops/win-run.ps1")" \
    -EncodedCmd "$CMD_B64" -TimeoutSec "${WIN_TIMEOUT:-600}" -MemMB "${WIN_MEM_MB:-1536}" | tr -d '\r'
exit "${PIPESTATUS[0]}"
