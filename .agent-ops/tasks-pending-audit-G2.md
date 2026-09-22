# Task G2: safety-gate and concurrency audit (read-only report)

Scope: `ground_station/service/agent.py`, `api.py`, `core.py`, `agent_mcp.py`,
`journey.py`. Tier 2. **READ ONLY. Do not edit any file except your result file.**
Do not POST to the live 8081 service. Do not touch firmware.

You are producing evidence, not fixes.

## What to check

1. **Gate coverage.** Enumerate *every* POST/mutating route in `api.py` and
   `agent_mcp.py` in a table: route, what it can do (arm, set a param, subscribe,
   flash, run a plan, cancel, approve), and which checks it passes through
   (mode off/supervised/autonomous, safety tier, approval queue, arm gate).
   The finding we care about is a route that can change drone or service state but
   skips a check its siblings apply. Name it explicitly.
2. **`source: 'operator'` spoofing.** Approvals and some commands are trusted when the
   request says it came from the operator. Trace where `source` is read and whether an
   agent-originated request (via the MCP server or a plain HTTP POST) can set it to
   `operator` and self-approve its own plan. Report the actual trust model: what
   distinguishes an operator request from an agent request today — a different port, a
   token, a header, a different code path, or nothing at all?
3. **Approval queue ordering.** Can a plan step execute before its approval lands, or
   twice, if two requests race?
4. **Concurrency.** Shared mutable state touched from both HTTP handler threads and the
   telemetry/ingest thread: dicts, lists, counters, session objects, subscriber sets.
   For each, say whether a lock is held on every path. Iterating a dict/set while
   another thread mutates it raises `RuntimeError: dictionary changed size during
   iteration` — look specifically for that shape in SSE broadcast and telemetry fan-out.

## How to report

| # | Severity | file:line | CONFIRMED / PLAUSIBLE | what an attacker or a confused agent gets |

CONFIRMED means you ran something and saw it — paste the exact command and its real
output. A race is CONFIRMED only if you actually reproduced it (a loop with threads
is fine). Otherwise mark it PLAUSIBLE and say so. **Never label something CONFIRMED
that you did not run.**

For the trust model, a plain-English paragraph is the deliverable: an honest
"nothing distinguishes them" is a perfectly good answer and more useful than a guess.
Propose a fix per finding, name the module that should own it, but **do not apply it**.

## Verification of your own work

Run `python -m pytest ground_station/service -q 2>&1 | tail -5` once at the end and
paste it verbatim.

Write the report to `.agent-ops/results/audit-G2.md`. Exit cleanly when done.
