#!/bin/bash
# Run the MANAGER (account B = the `claude` CLI login; the desktop app keeps account A) headless
# on one work package. Spec: docs/agent/ceo-manager-architecture.md, rules: docs/agent/MANAGER.md.
#   manager.sh run <id> [effort]        brief docs/agent/briefs/WP-<id>.md; worktree ../wt-wp<id> on
#                                       branch wp/<id> from main; prompt = MANAGER.md + brief;
#                                       raw output .agent-ops/wp-<id>.json, then a short summary
#   manager.sh resume <id> <msg-file>   follow-up in the same B session (session_id from the json)
# Start it in the background from the CEO session and wait for its one notification.
# The report lands on the branch: git show wp/<id>:docs/agent/reports/WP-<id>.md
set -eu
root=$(git rev-parse --show-toplevel)
id=$2
wt="$root/../wt-wp$id"
out="$root/.agent-ops/wp-$id.json"
tools=(Read Edit Write Glob Grep
    "Bash(bash .agent-ops/vps-worker.sh:*)" "Bash(timeout 540 bash .agent-ops/vps-worker.sh wait:*)"
    "Bash(python .agent-ops/gate.py:*)" "Bash(python -m pytest:*)"
    "Bash(git status:*)" "Bash(git log:*)" "Bash(git diff:*)" "Bash(git show:*)"
    "Bash(git merge --ff-only:*)" "Bash(git add:*)" "Bash(git commit:*)")
flags=(--model opus --disable-slash-commands --strict-mcp-config --output-format json
    --permission-mode acceptEdits --allowedTools "${tools[@]}")
case "${1:-}" in
run)
    [ -e "$wt" ] || git -C "$root" worktree add -q -b "wp/$id" "$wt" main
    cd "$wt"
    cat "$root/docs/agent/MANAGER.md" "$root/docs/agent/briefs/WP-$id.md" \
        | claude -p --effort "${3:-high}" "${flags[@]}" > "$out" || echo "claude rc=$?" ;;
resume)
    sid=$(python -c "import json,sys; print(json.load(open(sys.argv[1]))['session_id'])" "$out")
    cd "$wt"
    claude -p --resume "$sid" "${flags[@]}" < "$3" > "$out.tmp" || echo "claude rc=$?"
    mv "$out.tmp" "$out" ;;
*) sed -n 2,9p "$0"; exit 1 ;;
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
