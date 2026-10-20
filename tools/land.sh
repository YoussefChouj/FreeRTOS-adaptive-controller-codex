#!/usr/bin/env bash
# bash tools/land.sh <branch> - the one way main moves (the GitHub ruleset refuses anything without a green `check`).
#   1. push <branch> (through .githooks/pre-push and the push guard),
#   2. wait for the `check` workflow (.github/workflows/check.yml) to pass on that exact commit,
#   3. fast-forward origin/main to it with --no-verify, the one sanctioned bypass of the main block.
# Idempotent: re-run it after any failure or interruption and it resumes. A lost connection while waiting is
# "unknown", not "failed": it keeps waiting until LAND_TIMEOUT (default 1800 s). Run it in the background.
# Landing is the operator's call (AGENTS.md: the CEO merges after QA); agents build branches, they do not land them.
set -euo pipefail
branch=${1:?usage: bash tools/land.sh <branch>}
repo=${LAND_REPO:-YoussefChouj/FreeRTOS-adaptive-controller-codex}
timeout=${LAND_TIMEOUT:-1800}
PY=${PY:-python}
cd "$(git rev-parse --show-toplevel)"
sha=$(git rev-parse --verify "refs/heads/$branch^{commit}")
git fetch -q origin main
if [ "$(git rev-parse origin/main)" = "$sha" ]; then echo "land: origin/main is already ${sha:0:12}"; exit 0; fi
git merge-base --is-ancestor origin/main "$sha" ||
    { echo "land: $branch is not a fast-forward of origin/main; merge origin/main into it, re-check, re-run" >&2; exit 1; }
git push -q origin "refs/heads/$branch:refs/heads/$branch"
auth=(); [ -n "${GITHUB_TOKEN:-}" ] && auth=(-H "Authorization: Bearer $GITHUB_TOKEN")
echo "land: waiting for CI check on ${sha:0:12} (up to ${timeout} s)"
deadline=$(( $(date +%s) + timeout ))
while :; do
    state=$(curl -fsS "${auth[@]}" "https://api.github.com/repos/$repo/actions/runs?head_sha=$sha&per_page=20" 2>/dev/null |
        "$PY" -c 'import json, sys
runs = [r for r in json.load(sys.stdin).get("workflow_runs", []) if r.get("path", "").endswith("/check.yml")]
print(f"{runs[0][\"status\"]} {runs[0][\"conclusion\"]}" if runs else "none")' 2>/dev/null) || state="unknown"
    case "$state" in
        "completed success") break ;;
        completed*) echo "land: CI check finished '$state' on ${sha:0:12}; main not moved" >&2; exit 1 ;;
    esac
    [ "$(date +%s)" -lt "$deadline" ] || { echo "land: no green CI after ${timeout} s (last: $state); re-run to resume" >&2; exit 1; }
    sleep 60
done
git push --no-verify -q origin "$sha:refs/heads/main"
git fetch -q origin main
[ "$(git rev-parse origin/main)" = "$sha" ] || { echo "land: push sent but origin/main is not ${sha:0:12}; re-run" >&2; exit 1; }
echo "land: origin/main is now ${sha:0:12} ($branch)"
