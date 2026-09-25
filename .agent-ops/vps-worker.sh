#!/bin/bash
# Remote workers on the Hetzner VPS (ssh host oc-agent, user agent). Preferred lane for
# code/investigation tasks: costs no laptop CPU and no Claude tokens beyond brief + digest.
# Server side: ~/bin/oc-run (max 3 concurrent; agy 2 at once; Hetzner and Google 1 each),
# worktree ~/wt/<id> on branch worker/<id> based on base/<id> (= laptop HEAD at spawn).
#   vps-worker.sh spawn <id> <model[,fallback...]> <task.md>   e.g. agy:gemini-3.1-pro-high,agy:gemini-3.8-flash-high,qwen
#   vps-worker.sh wait <id>      block until done; prints status + digest (run in background)
#   vps-worker.sh fetch <id>     fetch branch; print digest + diffstat (read this, not the log)
#   vps-worker.sh status | log <id> [lines] | kill <id> | clean <id>
# Model aliases: qwen | free | gem | agy | agy:<model>  (ssh oc-agent '~/.local/bin/agy models')
set -eu
h=oc-agent
ssh_() { ssh -o ServerAliveInterval=60 -o ServerAliveCountMax=5 "$h" "$@"; }
digest() { MSYS_NO_PATHCONV=1 git show "vps/$1:.agent-ops/out/$1.md" 2>/dev/null || echo "(no digest written: read 'log $1 60')"; }
case "${1:-}" in
spawn)
    d=$(git status --porcelain -uno | wc -l); [ "$d" -gt 0 ] && echo "note: $d modified tracked file(s) not sent (only HEAD is)" >&2
    git push -qf vps "HEAD:refs/heads/base/$2" "HEAD:refs/heads/laptop-main"
    scp -q "$4" "$h:tasks/$2.md"
    ssh_ "tmux new -d -s '$2' '~/bin/oc-run $2 $3 ~/tasks/$2.md'" && echo "spawned $2 on vps (base $(git rev-parse --short HEAD))" ;;
wait)
    ssh_ "while [ ! -e ~/runs/$2.rc ]; do sleep 20; done; tail -1 ~/runs/$2.out"
    git fetch -q vps "worker/$2:refs/remotes/vps/$2" && digest "$2" ;;
status)
    ssh_ 'tmux ls 2>/dev/null; for f in ~/runs/*.out; do [ -e "$f" ] && echo "$(basename $f .out): $(grep -E "^(STARTED|DONE|waiting|FALLBACK)" $f | tail -1)"; done; free -h | sed -n 2p' ;;
log)   ssh_ "tail -n ${3:-40} ~/runs/$2.out | tr -d '\007'" ;;
fetch) git fetch -q vps "worker/$2:refs/remotes/vps/$2" && git log --oneline -1 "vps/$2" && digest "$2" && git diff --stat "HEAD...vps/$2" | tail -8 ;;
kill)  ssh_ "tmux kill-session -t '$2'" ;;
clean) ssh_ "cd FreeRTOS-adaptive-controller-codex && git worktree remove --force ~/wt/$2; git branch -D worker/$2 base/$2; rm -f ~/runs/$2.* ~/tasks/$2.md"
       git update-ref -d "refs/remotes/vps/$2" 2>/dev/null || true ;;
*) sed -n 2,11p "$0"; exit 2 ;;
esac
