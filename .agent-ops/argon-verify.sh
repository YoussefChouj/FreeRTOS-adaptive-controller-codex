#!/usr/bin/env bash
# argon-verify.sh - second opinion from Gemini 4 Argon (Antigravity CLI, plan mode) on a claim or question.
#
#   bash .agent-ops/argon-verify.sh [--web] [--effort high] "<claim or question>" file[:start-end]...
#
# Argon reads only the listed files and ranges and answers through a JSON schema. Prints the verdict JSON and one
# usage line; the raw agy JSON goes to logs/argon-verify/<timestamp>.json (stderr next to it as .err).
# --web allows web search (off by default). --effort high runs gemini-4-argon-high instead of -medium.
# Exit: 0 verdict printed, 1 agy gave no structured_output, 2 bad arguments.
# Env: AGY (default D:/Tools/agy/bin/agy.exe), ARGON_TIMEOUT seconds (default 600).
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 2

usage='usage: bash .agent-ops/argon-verify.sh [--web] [--effort high] "<claim or question>" file[:start-end]...'
effort=medium web=0 args=()
while [ $# -gt 0 ]; do
    case $1 in
        --web) web=1; shift ;;
        --effort) [ $# -ge 2 ] || { echo "$usage" >&2; exit 2; }; effort=$2; shift 2 ;;
        *) args+=("$1"); shift ;;
    esac
done
[ ${#args[@]} -ge 2 ] || { echo "$usage" >&2; exit 2; }

files=""
for f in "${args[@]:1}"; do
    path=$f range=""
    if [[ $f =~ ^(.+):([0-9]+-[0-9]+)$ ]]; then path=${BASH_REMATCH[1]} range=" lines ${BASH_REMATCH[2]}"; fi
    [ -f "$path" ] || { echo "argon-verify: no such file: $path" >&2; exit 2; }
    files+="- $path$range"$'\n'
done
if [ $web = 1 ]; then web_rule="You may search the web when the files cannot settle it."
else web_rule="Do not search the web."; fi

prompt="You are a second-opinion verifier for another coding agent. Check this claim, or answer this question,
about the repository in the working directory:
${args[0]}

Read only these files and line ranges, and open nothing else (not AGENTS.md or the handoff page unless listed):
${files}Follow the GEMINI.md reading rules: search before you read, ranges of about 80 lines, at most 8 file views.
$web_rule
Answer only through the JSON schema: verdict agree, disagree or unsure (unsure when the listed files cannot settle
it); confidence 0-1; issues = each problem you found, with file, line (0 if none), problem and the quoted line as
evidence (empty list if none); next_check = the one check that would settle what is still open."

schema='{"type":"object","required":["verdict","confidence","issues","next_check"],"properties":{'\
'"verdict":{"type":"string","enum":["agree","disagree","unsure"]},'\
'"confidence":{"type":"number","minimum":0,"maximum":1},'\
'"issues":{"type":"array","items":{"type":"object","required":["file","line","problem","evidence"],"properties":{'\
'"file":{"type":"string"},"line":{"type":"integer"},"problem":{"type":"string"},"evidence":{"type":"string"}}}},'\
'"next_check":{"type":"string"}}}'

mkdir -p logs/argon-verify
log=logs/argon-verify/$(date +%Y%m%d-%H%M%S)-$$.json
model=gemini-4-argon-$effort
start=$SECONDS
# MSYS_NO_PATHCONV / MSYS2_ARG_CONV_EXCL: stop Git Bash rewriting "/..." inside the prompt as Windows paths.
MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' timeout -k 10 "${ARGON_TIMEOUT:-600}" "${AGY:-D:/Tools/agy/bin/agy.exe}" \
    --model "$model" --mode plan --output-format json --json-schema "$schema" -p "$prompt" \
    > "$log" 2> "${log%.json}.err"
rc=$?

python -I - "$log" "${log%.json}.err" "$rc" "$((SECONDS - start))" "$model" <<'EOF'
import json, sys
log, err, rc, wall, model = sys.argv[1:]
raw = open(log, encoding="utf-8-sig", errors="replace").read()
try:
    j = json.loads(raw)
except ValueError:
    j = {}
j = j if isinstance(j, dict) else {}
out = j.get("structured_output")
if out is None:
    why = j.get("error") or j.get("response") or raw.strip() or open(err, encoding="utf-8", errors="replace").read()
    print(f"argon-verify: no structured_output (agy rc={rc}, status={j.get('status')}): {ascii(str(why)[-400:])}")
else:
    print(json.dumps(out))
u = j.get("usage") or {}
print(f"argon usage: in={u.get('input_tokens')} out={u.get('output_tokens')} cache_read={u.get('cache_read_tokens')}"
      f" wall={wall}s model={model} log={log}")
sys.exit(0 if out is not None else 1)
EOF
