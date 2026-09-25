#!/bin/bash
# Remote workers on the Hetzner VPS (ssh host oc-agent, user agent). Preferred lane for
# code/investigation tasks: costs no laptop CPU and no Claude tokens beyond brief + digest.
# Server side: ~/bin/oc-run (max 3 concurrent; agy 2 at once; Hetzner and Google 1 each),
# worktree ~/wt/<id> on branch worker/<id> based on base/<id> (= laptop HEAD at spawn).
#   vps-worker.sh spawn <id> <model[,fallback...]> <task.md>   e.g. agy:gemini-3.1-pro-high,agy:gemini-3.8-flash-high,qwen
#   vps-worker.sh wait <id>      block until done; prints status + digest (run in background)
#   vps-worker.sh fetch <id>     fetch branch; print digest + diffstat (read this, not the log)
#   vps-worker.sh status | log <id> [lines] | kill <id> | clean <id>
#   vps-worker.sh stats [days]   per-model history (VPS + local ledgers): runs, OK%, median secs,
#                                QUOTA hits in 5h/window + last quota text. Pick workers from it.
# Model aliases: qwen | free | gem | agy | agy:<model>  (ssh oc-agent '~/.local/bin/agy models')
#                fake:ok|quota|net|auth test the chain without a model call.
# Network drops: every remote step retries (5 tries, 15..240s apart); `wait` survives any
# outage and ends with LOST if the run's tmux session is gone without a result.
set -eu
h=oc-agent
ssh_() { ssh -o ConnectTimeout=20 -o ServerAliveInterval=30 -o ServerAliveCountMax=4 "$h" "$@"; }
retry() {  # retry <cmd...>: rerun on failure with backoff; the last failure is fatal
    local i=0 d=15
    until "$@"; do
        i=$((i + 1)); [ $i -ge 5 ] && { echo "vps-worker: gave up after $i tries: $*" >&2; return 1; }
        echo "vps-worker: '$1' failed, retry $i in ${d}s" >&2; sleep $d; d=$((d * 2))
    done
}
digest() { MSYS_NO_PATHCONV=1 git show "vps/$1:.agent-ops/out/$1.md" 2>/dev/null || echo "(no digest written: read 'log $1 60')"; }
case "${1:-}" in
spawn)
    d=$(git status --porcelain -uno | wc -l); [ "$d" -gt 0 ] && echo "note: $d modified tracked file(s) not sent (only HEAD is)" >&2
    ex=$(retry ssh_ "if tmux has -t '$2' 2>/dev/null || [ -e ~/runs/$2.out ]; then echo y; else echo n; fi")
    [ "$ex" = n ] || { echo "id $2 already exists on vps (clean it first)" >&2; exit 1; }
    retry git push -qf vps "HEAD:refs/heads/base/$2" "HEAD:refs/heads/laptop-main"
    retry scp -q -o ConnectTimeout=20 "$4" "$h:tasks/$2.md"
    retry ssh_ "tmux has -t '$2' 2>/dev/null || tmux new -d -s '$2' '~/bin/oc-run $2 $3 ~/tasks/$2.md'"
    echo "spawned $2 on vps (base $(git rev-parse --short HEAD))" ;;
wait)
    # Trust only a sentinel printed remotely, never ssh's exit code: a dropped or killed
    # ssh can exit 0 on Windows. No sentinel = connection lost, so reconnect.
    while :; do
        o=$(ssh_ "while [ ! -e ~/runs/$2.rc ]; do tmux has -t '$2' 2>/dev/null || { sleep 5; [ -e ~/runs/$2.rc ] || { echo @@LOST; exit; }; }; sleep 20; done; echo @@READY; tail -1 ~/runs/$2.out" 2>/dev/null || true)
        case "$o" in
            *@@READY*) echo "${o#*@@READY}" | sed '/^$/d'; break ;;
            *@@LOST*) echo "LOST: tmux session $2 ended without a result; read 'log $2 60'"; exit 3 ;;
        esac
        echo "vps-worker: connection lost, reconnecting in 30s" >&2; sleep 30
    done
    retry git fetch -q vps "worker/$2:refs/remotes/vps/$2" && digest "$2" ;;
status)
    ssh_ 'tmux ls 2>/dev/null; for f in ~/runs/*.out; do [ -e "$f" ] && echo "$(basename $f .out): $(grep -E "^(STARTED|DONE|waiting|FALLBACK|RETRY)" $f | tail -1)"; done; free -h | sed -n 2p' ;;
log)   ssh_ "tail -n ${3:-40} ~/runs/$2.out | tr -d '\007'" ;;
fetch) retry git fetch -q vps "worker/$2:refs/remotes/vps/$2" && git log --oneline -1 "vps/$2" && digest "$2" && git diff --stat "HEAD...vps/$2" | tail -8 ;;
stats) o=$(dirname "$0"); { ssh_ 'cat ~/runs/ledger.jsonl 2>/dev/null' || echo "vps-worker: vps ledger unreachable" >&2
         cat "$o/logs/ledger.jsonl" 2>/dev/null; } | python "$o/worker_stats.py" --days "${2:-7}" -
       # Live remaining quota: `agy -p /usage` is headless, ~2 s, 0 tokens (q1, verified 2026-09-26).
       # Ark has no key-level usage API (needs account AK/SK), so its only signal is the QUOTA history above.
       echo; echo "agy quota remaining (pool, window, left, resets UTC):"
       echo "[vps]";   ssh_ 'timeout 60 ~/.local/bin/agy -p /usage 2>&1' | sed 's/^/  /' || echo "  unreachable"
       echo "[local]"; wsl -d Ubuntu -e bash -lc 'timeout 60 agy -p /usage 2>&1' | sed 's/^/  /' || echo "  unreachable" ;;
kill)  ssh_ "tmux kill-session -t '$2'" ;;
clean) ssh_ "cd FreeRTOS-adaptive-controller-codex && git worktree remove --force ~/wt/$2; git branch -D worker/$2 base/$2; rm -f ~/runs/$2.* ~/tasks/$2.md"
       git update-ref -d "refs/remotes/vps/$2" 2>/dev/null || true ;;
*) sed -n 2,16p "$0"; exit 2 ;;
esac
