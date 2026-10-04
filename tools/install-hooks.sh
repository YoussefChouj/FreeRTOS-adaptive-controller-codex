#!/usr/bin/env bash
# Opt-in (WP-38): make tools/check.sh this repository's git pre-commit hook. Nothing installs it for you.
#   bash tools/install-hooks.sh            install (refuses to replace a pre-commit hook it did not write)
#   bash tools/install-hooks.sh --remove   remove the hook this script wrote
# The hook runs the whole gate on the working tree of the commit (minutes, not seconds) and blocks the commit
# on CHECK FAIL. One commit without it: git commit --no-verify. Hooks live in the common git dir, so one
# install covers every worktree; each commit runs the check.sh of its own worktree.
set -eu
cd "$(dirname "$0")/.."
hooks=$(git rev-parse --git-path hooks)
hook="$hooks/pre-commit"
marker="# installed by tools/install-hooks.sh"

if [ "${1:-}" = "--remove" ]; then
    if [ -f "$hook" ] && grep -qF "$marker" "$hook"; then
        rm "$hook"
        echo "removed $hook"
    else
        echo "no pre-commit hook of ours at $hook"
    fi
    exit 0
fi

if [ -e "$hook" ] && ! grep -qF "$marker" "$hook"; then
    echo "a different pre-commit hook exists at $hook: not replacing it" >&2
    exit 1
fi
mkdir -p "$hooks"
cat > "$hook" <<EOF
#!/usr/bin/env bash
$marker
exec bash "\$(git rev-parse --show-toplevel)/tools/check.sh"
EOF
chmod +x "$hook"
echo "installed $hook (runs tools/check.sh)"
