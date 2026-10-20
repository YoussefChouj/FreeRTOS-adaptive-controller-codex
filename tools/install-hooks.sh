#!/usr/bin/env bash
# Shared git hooks, tracked in the repo. One command per machine:
#
#   bash tools/install-hooks.sh             install + print the activation line (review first)
#   bash tools/install-hooks.sh --activate  install and wire core.hooksPath in the same run
#   bash tools/install-hooks.sh --remove    remove this repo's hook wiring
#
# Default: install a tracked git pre-commit and pre-push that use
# tools/check.sh and tools/agent_push_guard.py. The pre-commit hook runs
# the routed subset for the staged files (check.sh --changed), so it stays
# a second or two instead of the whole gate. The legacy local-mode (which wrote
# to .git/hooks/pre-commit) is preserved by passing --legacy.
#
# Bare install deliberately does NOT write git config: the hook files are
# plain bash, and reviewing them once before they fire beats reading about
# hooks that already fire. --activate is the same thing for an operator who
# has already reviewed them; it records a marker so --remove only undoes
# our own wiring.
set -eu
cd "$(dirname "$0")/.."

marker="tracked-hooks.installed"

case "${1:-}" in
    --remove)
        # Tracked-mode undo. If we set core.hooksPath under our marker (a custom config key), drop it and the key.
        if git config --get "$marker" >/dev/null 2>&1; then
            git config --unset-all "core.hooksPath" || true
            git config --unset-all "$marker" || true
            echo "removed hooksPath wiring from .git/config"
        else
            echo "no tracked-hooks wiring in .git/config"
        fi
        # Legacy-mode undo: erase the file we wrote into .git/hooks/, but only if it is ours.
        legacy_hook="$(git rev-parse --git-path hooks)/pre-commit"
        if [ -f "$legacy_hook" ] && grep -qF "$marker (legacy mode)" "$legacy_hook"; then
            rm -f "$legacy_hook"
            echo "removed legacy hook $legacy_hook"
        fi
        exit 0
        ;;
    --legacy)
        legacy=1
        ;;
    --activate)
        legacy=0
        activate=1
        ;;
    "")
        legacy=0
        ;;
    *)
        echo "usage: $0 [--activate|--remove|--legacy]" >&2
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

if [ "${activate:-0}" = 1 ]; then
    # The marker is a custom boolean config key: presence = ours, absence = not ours. Lets --remove leave any
    # pre-existing core.hooksPath setting alone (the conservative choice).
    git config --replace-all core.hooksPath .githooks 2>/dev/null \
        || git config core.hooksPath .githooks
    git config --type=bool "$marker" true
    echo "installed tracked hooks; core.hooksPath = .githooks"
    echo "to remove: bash tools/install-hooks.sh --remove"
    exit 0
fi

# Bare install: print the activation hint (the operator reviews the hook files first).
cat <<'NOTE'
Tracked-hooks wiring is ready. To activate on this machine:

    git config core.hooksPath .githooks

or in one shot, after reading the hooks above:

    bash tools/install-hooks.sh --activate

After activation, every commit runs .githooks/pre-commit (the routed
subset of tools/check.sh) and every push runs .githooks/pre-push (the
allowed-paths guard from tools/agent_push_guard.py).

To deactivate: `bash tools/install-hooks.sh --remove` (or
`git config --unset core.hooksPath`).
NOTE
