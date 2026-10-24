#!/usr/bin/env bash
# bash tools/land.sh <branch> - the one way main moves (the GitHub ruleset refuses anything without a green `check`).
#   1. push <branch> (through .githooks/pre-push and the push guard),
#   2. wait for the `check` workflow (.github/workflows/check.yml) to pass on that exact commit,
#   3. fast-forward origin/main to it with --no-verify, the one sanctioned bypass of the main block.
# Idempotent: re-run it after any failure or interruption and it resumes. A lost connection or a rate limit while
# waiting is "unknown", not "failed": it says why and keeps waiting until LAND_TIMEOUT (default 1800 s). A refused
# token or an unknown repo stops it at once. Each change of CI state prints one line. Run it in the background.
# bash tools/land.sh --status <branch>: print the CI state of <branch>'s commit and exit (read-only: no push, no land).
# GITHUB_TOKEN in the environment lifts the API limit from 60 requests/h per IP to 5000/h; it needs Actions: read.
# Landing is the operator's call (AGENTS.md: the CEO merges after QA); agents build branches, they do not land them.
set -euo pipefail
status_only=""; [ "${1:-}" = --status ] && { status_only=1; shift; }
branch=${1:?usage: bash tools/land.sh [--status] <branch>}
repo=${LAND_REPO:-YoussefChouj/FreeRTOS-adaptive-controller-codex}
timeout=${LAND_TIMEOUT:-1800}
# curl is overridable so tools/test_land_ci.py can answer without the network. It cannot be done with PATH: Git Bash
# prepends its own /mingw64/bin ahead of anything the caller passes, so an injected stub curl never wins.
CURL=${CURL:-curl}
PY=${PY:-python}
# Resolve an interpreter that actually runs, the way tools/check.sh does: the `bash` first on PATH on a Windows box is
# WSL, which has no `python` and does not inherit the Windows environment, so `PY` arrives empty and a bare `python`
# dies with "command not found" in the middle of a landing. Probing costs one call and turns that into a working run.
if ! "$PY" -c 'pass' >/dev/null 2>&1; then
    for cand in python python3 py.exe /mnt/c/Windows/py.exe; do
        if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'pass' >/dev/null 2>&1; then PY="$cand"; break; fi
    done
fi
"$PY" -c 'pass' 2>/dev/null ||
    { echo "land: no usable python interpreter (tried \$PY, python, python3, py.exe); set PY=/path/to/python" >&2; exit 1; }
cd "$(git rev-parse --show-toplevel)"
sha=$(git rev-parse --verify "refs/heads/$branch^{commit}")
auth=(); [ -n "${GITHUB_TOKEN:-}" ] && auth=(-H "Authorization: Bearer $GITHUB_TOKEN")
# Relative paths: Windows Python cannot open the /tmp paths Git Bash's mktemp returns.
tmp=.agent_state/land-$$; mkdir -p .agent_state; trap 'rm -f "$tmp".*' EXIT

ci_state() {  # sets $state (one line for the operator) and $verdict (tools/land_ci.py: 0 parsed, 2 wait, 3 stop)
    local code
    code=$("$CURL" -sS -o "$tmp.body" -D "$tmp.hdr" -w '%{http_code}' "${auth[@]}" \
        "https://api.github.com/repos/$repo/actions/runs?head_sha=$sha&per_page=20" 2>"$tmp.err") || true
    verdict=0; state=$("$PY" tools/land_ci.py "${code:-000}" "$tmp.hdr" "$tmp.body" "$tmp.err") || verdict=$?
}

if [ -n "$status_only" ]; then
    ci_state; echo "land: CI on ${sha:0:12} ($branch): $state"; exit "$verdict"
fi
git fetch -q origin main
if [ "$(git rev-parse origin/main)" = "$sha" ]; then echo "land: origin/main is already ${sha:0:12}"; exit 0; fi
git merge-base --is-ancestor origin/main "$sha" ||
    { echo "land: $branch is not a fast-forward of origin/main; merge origin/main into it, re-check, re-run" >&2; exit 1; }
git push -q origin "refs/heads/$branch:refs/heads/$branch"
echo "land: waiting for CI check on ${sha:0:12} (up to ${timeout} s)"
deadline=$(( $(date +%s) + timeout ))
last=""
while :; do
    ci_state
    case "$verdict:$state" in
        "0:completed success") break ;;
        0:completed*) echo "land: CI check finished '$state' on ${sha:0:12}; main not moved" >&2; exit 1 ;;
        0:* | 2:*) ;;
        *) echo "land: cannot read CI for ${sha:0:12}: ${state:-tools/land_ci.py exited $verdict}; main not moved" >&2
           exit 1 ;;
    esac
    [ "$state" = "$last" ] || echo "land: $(date +%H:%M) CI: $state"
    last=$state
    [ "$(date +%s)" -lt "$deadline" ] || { echo "land: no green CI after ${timeout} s (last: $state); re-run to resume" >&2; exit 1; }
    sleep 60
done
git push --no-verify -q origin "$sha:refs/heads/main"
git fetch -q origin main
[ "$(git rev-parse origin/main)" = "$sha" ] || { echo "land: push sent but origin/main is not ${sha:0:12}; re-run" >&2; exit 1; }
echo "land: origin/main is now ${sha:0:12} ($branch)"
