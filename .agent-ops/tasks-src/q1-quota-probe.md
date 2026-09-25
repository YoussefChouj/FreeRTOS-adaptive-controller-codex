# q1-quota-probe: how can a script read remaining quota for agy and Ark?

Research only. No code changes to the repo. Write findings to `.agent-ops/out/q1-quota-probe.md`
and commit that one file.

You run on a Linux VPS where the Antigravity CLI is installed at `~/.local/bin/agy` and is signed in.

## Questions

1. **agy (Antigravity CLI)**: the interactive `/usage` command shows the remaining weekly quota
   and the rolling 5-hour window per model pool. Can a script read the same numbers?
   - Check `~/.local/bin/agy --help` and the help output of every subcommand. Look for flags like `usage`, `quota` or `status`.
   - Look under `~/.gemini`, `~/.antigravity`, `~/.config` and `~/.cache` for files that store quota or usage state.
     List the file names and the relevant keys only. Never print tokens, cookies or credentials.
   - Can the `/usage` slash command be run headless? Examples: `agy -p "/usage"`, or stdin piping.
     Try it once with the cheapest model and report the exact output (truncate at 30 lines).
   - Is there an HTTP endpoint that the CLI calls for quota? Answer from docs or source only; do not sniff traffic.
2. **Volcengine Ark "Agent Plan" / ArkCoding**: is there a documented API or console endpoint that
   returns remaining plan usage (5h window / weekly / monthly)? Use web search.
3. For both: what exact error text or HTTP status appears when a quota window runs out, and does it state the reset time?

## Rules

- Quote your sources: the URL plus a short excerpt, or the command plus its output. If you find nothing, say "not found" and list the places you searched. Do not guess.
- Keep the output file under 120 lines.
- End the file with a `## Recommendation` section: the most reliable way to probe each quota, in 3–6 lines.
