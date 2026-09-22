#!/bin/bash
# opencode worker launcher: oc-worker.sh <provider/model> <prompt>
# Loads the Windows proxy and the API keys (kept outside the repo in
# ~/.config/agent-keys.env, mode 600), then runs opencode headless.
# Providers and models live in ~/.config/opencode/opencode.json.
. /etc/profile.d/winproxy.sh 2>/dev/null
. ~/.config/agent-keys.env
case "$1" in
    openrouter/*:free|openrouter/openrouter/free) ;;
    openrouter/*) echo "oc-worker: refusing paid OpenRouter model $1 (use a :free id)" >&2; exit 2 ;;
esac
exec ~/.opencode/bin/opencode run -m "$@"
