#!/usr/bin/env bash
# Shared git hooks, tracked in the repo. Enable with one command:
#
#   bash tools/install-hooks.sh            install (refuses to clobber an existing setup)
#   bash tools/install-hooks.sh --remove   remove this repo's hook wiring
#
# Default: install a tracked git pre-commit and pre-push that use
# tools/check.sh and tools/agent_push_guard.py. Both run cheaply and
# fail the operation when gates fail. The legacy local-mode (which wrote
# to .git/hooks/pre-commit) is preserved by passing --legacy.
#
# Activation is one line per machine:
#   git config core.hooksPath .githooks
# We do NOT run that ourselves. The operator runs it after reviewing
# the hook files (they are plain bash; review is easier than reading
# hooks that fire without warning).
set -eu
cd "$(dirname "$0")/.."

marker="# tracked-hooks (tools/install-hooks.sh)"

case "${1:-}" in
    --remove)
        if grep -qF "$marker" .git/config 2>/dev/null; then
            git config --unset core.hooksPath
            echo "removed hooksPath wiring from .git/config"
        else
            echo "no tracked-hooks wiring in .git/config"
        fi
        exit 0
        ;;
    --legacy)
        legacy=1
        ;;
    "")
        legacy=0
        ;;
    *)
        echo "usage: $0 [--remove|--legacy]" >&2
        exit 2
        ;;
esac

if [ "$legacy" -eq 1 ]; then
    # Same as the previous behaviour: write into the common git hooks dir.
    hooks=$(git rev-parse --git-path hooks)
    mkdir -p "$hooks"
    hook="$hooks/pre-commit"
    if [ -e "$hook" ] && ! grep -qF "$marker" "$hook"; then
        echo "a different pre-commit hook exists at $hook: not replacing it" >&2
        exit 1
    fi
    cat > "$hook" <<EOF
#!/usr/bin/env bash
$marker (legacy mode)
exec bash "\$(git rev-parse --show-toplevel)/tools/check.sh"
EOF
    chmod +x "$hook"
    echo "installed (legacy) $hook (runs tools/check.sh)"
    exit 0
fi

# Tracked-mode: hooks live in .githooks/, versioned with the repo. The
# operator activates them once per machine with:
#   git config core.hooksPath .githooks
# We do NOT touch git config here.
test -d .githooks || { echo ".githooks/ missing in this repo" >&2; exit 1; }

# Make the hooks executable (a fresh clone on Windows may not preserve +x).
chmod +x .githooks/pre-commit .githooks/pre-push

# Stash the activation hint for the operator.
cat <<'NOTE'
Tracked-hooks wiring is ready. To activate on this machine:

    git config core.hooksPath .githooks

After that, every commit runs .githooks/pre-commit (the lightweight
subset of tools/check.sh) and every push runs .githooks/pre-push (the
allowed-paths guard from tools/agent_push_guard.py).

To deactivate: `bash tools/install-hooks.sh --remove` (or
`git config --unset core.hooksPath`).
NOTE
