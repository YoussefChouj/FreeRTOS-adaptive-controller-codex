#!/usr/bin/env bash
# ship.sh - one-call commit for agents. Save often, ship at ship points.
#
#   bash .agent-ops/ship.sh "msg" <path>...          save: quiet local commit of exactly <path>...
#   bash .agent-ops/ship.sh --push "msg" <path>...   ship: commit, then push in the background
#   bash .agent-ops/ship.sh --push                   ship: push what is already committed
#
# Prints one line. The push log goes to .agent-ops/out/push.log (read it only if a later push fails).
# Pathspec commits only: other sessions share this index, so never add -A / . / -a.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

push=0
if [ "${1:-}" = "--push" ]; then push=1; shift; fi

if [ $# -ge 1 ]; then
  msg=$1; shift
  [ $# -ge 1 ] || { echo "ship: give the paths to commit" >&2; exit 2; }
  if [ -n "${SHIP_TRAILER:-}" ]; then msg=$(printf '%s\n\n%s' "$msg" "$SHIP_TRAILER"); fi
  git add -A -- "$@"    # stage new, changed and deleted files under these paths only
  git commit -q -m "$msg" -- "$@"
  echo "saved $(git log -1 --format='%h %s' | cut -c1-90) ($(git show --format= --name-only HEAD | wc -l) files)"
fi

if [ $push -eq 1 ]; then
  mkdir -p .agent-ops/out
  nohup git push -q origin HEAD > .agent-ops/out/push.log 2>&1 &
  echo "push started (log .agent-ops/out/push.log)"
fi
