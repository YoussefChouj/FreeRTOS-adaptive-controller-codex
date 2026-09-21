#!/usr/bin/env bash
# monitor.sh — show the last 15 lines of the worker status log.
STATE_LOG="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/state.log"

if [ ! -f "$STATE_LOG" ]; then
    echo "no state.log yet ($STATE_LOG)"
    exit 0
fi
tail -n 15 "$STATE_LOG"
