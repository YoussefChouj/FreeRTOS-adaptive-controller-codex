#!/bin/bash
# Remote opencode workers on the Hetzner VPS (ssh host oc-agent, user agent).
# Server side: ~/bin/oc-run (max 3 concurrent; Hetzner and Google each serialized, agy too), runs in
# ~/wt/<id> on branch worker/<id>, log ~/runs/<id>.out.
#   vps-worker.sh spawn <id> <qwen|free|gem|agy|agy:<model>|provider/model> <task.md>
#   vps-worker.sh status | log <id> [lines] | fetch <id> | kill <id> | clean <id>
set -eu
h=oc-agent
case "$1" in
spawn)
    git push -qf vps HEAD:refs/heads/laptop-main
    scp -q "$4" "$h:tasks/$2.md"
    ssh "$h" "tmux new -d -s '$2' '~/bin/oc-run $2 $3 ~/tasks/$2.md'" && echo "spawned $2 on vps" ;;
status)
    ssh "$h" 'tmux ls 2>/dev/null; for f in ~/runs/*.out; do [ -e "$f" ] && echo "$(basename $f .out): $(grep -E "^(STARTED|DONE|waiting)" $f | tail -1)"; done; free -h | sed -n 2,3p' ;;
log)   ssh "$h" "tail -n ${3:-40} ~/runs/$2.out" ;;
fetch) git fetch -q vps "worker/$2:refs/remotes/vps/$2" && git log --oneline -1 "vps/$2" && git diff --stat HEAD..."vps/$2" | tail -5 ;;
kill)  ssh "$h" "tmux kill-session -t '$2'" ;;
clean) ssh "$h" "cd FreeRTOS-adaptive-controller-codex && git worktree remove --force ~/wt/$2; git branch -D worker/$2; rm -f ~/runs/$2.* ~/tasks/$2.md" ;;
*) sed -n 2,7p "$0"; exit 2 ;;
esac
