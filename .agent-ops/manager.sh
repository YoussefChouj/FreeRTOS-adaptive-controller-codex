#!/bin/bash
# Headless account-B roles (B = the `claude` CLI login; the desktop app keeps account A), one work package
# each. Spec: docs/agent/ceo-manager-architecture.md. Rules: docs/agent/{MANAGER,CTE,RESEARCHER}.md.
#   manager.sh run <id> [effort] [base]        MANAGER: brief docs/agent/briefs/WP-<id>.md; worktree ../wt-wp<id>
#                                              on branch wp/<id> from <base> (default main); dispatches workers
#   manager.sh cte <id> [effort] [base]        CHIEF TECHNICAL ENGINEER, after a manager BLOCKED: writes the code
#                                              itself in the same worktree (default effort xhigh)
#   manager.sh research <id> [effort] [base]   RESEARCHER, after a CTE BLOCKED: web research on the CTE's question
#   manager.sh resume <id> <msg-file> [role]   follow-up in the same B session (role run|cte|research, default run)
#   manager.sh clean <id> [base]               after the CEO merged wp/<id> into <base>: remove worktree + merged branch
# Start it in the background from the CEO session and wait for its one notification.
# Reports land on the branch: git show wp/<id>:docs/agent/reports/WP-<id>[-cte].md, research/WP-<id>.md
# Base context with these flags: 10.7k-12.8k tokens (measured 2026-10-01; 34.6k without --tools/--setting-sources).
# Verified 2026-10-01: both CLAUDE.md files still load; no skills, MCP, web tools, subagents, or user hooks/plugins/allow rules.
set -eu
root=$(git rev-parse --show-toplevel)
mode=${1:-}
id=${2:-}
wt="$root/../wt-wp$id"
role=$mode
[ "$mode" = resume ] && role=${4:-run}
out="$root/.agent-ops/wp-$id.json"
[ "$role" = run ] || out="$root/.agent-ops/wp-$id-$role.json"
tools=(Read Edit Write Glob Grep
    "Bash(bash .agent-ops/vps-worker.sh:*)" "Bash(timeout 540 bash .agent-ops/vps-worker.sh wait:*)"
    "Bash(python .agent-ops/gate.py:*)" "Bash(python -m pytest:*)"
    "Bash(git status:*)" "Bash(git log:*)" "Bash(git diff:*)" "Bash(git show:*)"
    "Bash(git merge --ff-only:*)" "Bash(git add:*)" "Bash(git commit:*)")
tool_set=Bash,Read,Edit,Write,Glob,Grep
[ "$role" = cte ] && tools+=("Bash(python:*)" "Bash(node:*)")
if [ "$role" = research ]; then
    tools=(Read Write Glob Grep WebSearch WebFetch "Bash(git add:*)" "Bash(git commit:*)")
    tool_set=Bash,Read,Write,Glob,Grep,WebSearch,WebFetch
fi
flags=(--model opus --tools "$tool_set" --setting-sources project,local
    --disable-slash-commands --strict-mcp-config --output-format json
    --permission-mode acceptEdits --allowedTools "${tools[@]}")
start() {  # $1 rules file, $2 effort, $3 base; extra prompt files follow
    local rules=$1 effort=$2 base=$3; shift 3
    [ -e "$wt" ] || git -C "$root" worktree add -q -b "wp/$id" "$wt" "$base"
    cd "$wt"
    { echo "Base branch for this package: $base (use it wherever the rules say main)."; echo
      cat "$root/docs/agent/$rules" "$root/docs/agent/briefs/WP-$id.md"
      for f in "$@"; do if [ -f "$f" ]; then echo; echo "----- $f -----"; cat "$f"; fi; done
    } | claude -p --effort "$effort" "${flags[@]}" > "$out" || echo "claude rc=$?"
}
case "$mode" in
run) start MANAGER.md "${3:-high}" "${4:-main}" ;;
cte) start CTE.md "${3:-xhigh}" "${4:-main}" "$wt/docs/agent/reports/WP-$id.md" "$wt/docs/agent/research/WP-$id.md" ;;
research) start RESEARCHER.md "${3:-high}" "${4:-main}" "$wt/docs/agent/reports/WP-$id.md" "$wt/docs/agent/reports/WP-$id-cte.md" ;;
resume)
    sid=$(python -c "import json,sys; print(json.load(open(sys.argv[1]))['session_id'])" "$out")
    cd "$wt"
    claude -p --resume "$sid" "${flags[@]}" < "$3" > "$out.tmp" || echo "claude rc=$?"
    mv "$out.tmp" "$out" ;;
clean)
    [ -e "$wt" ] && git -C "$root" worktree remove "$wt"
    git -C "$root" merge-base --is-ancestor "wp/$id" "${3:-main}" || { echo "wp/$id not merged into ${3:-main}"; exit 1; }
    git -C "$root" branch -D "wp/$id"
    rm -f "$root/.agent-ops/wp-$id.json" "$root/.agent-ops/wp-$id-cte.json" "$root/.agent-ops/wp-$id-research.json"
    exit 0 ;;
*) sed -n 2,13p "$0"; exit 1 ;;
esac
python - "$out" <<'EOF'
import json, sys
try:
    j = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, json.JSONDecodeError) as e:
    sys.exit(f"manager: no json result ({e})")
u = j.get("usage", {})
print(f"error={j.get('is_error')} turns={j.get('num_turns')} secs={j.get('duration_ms', 0) // 1000} "
      f"in={u.get('input_tokens')} cache_write={u.get('cache_creation_input_tokens')} "
      f"cache_read={u.get('cache_read_input_tokens')} out={u.get('output_tokens')}")
print((j.get("result") or "")[-600:])
EOF
