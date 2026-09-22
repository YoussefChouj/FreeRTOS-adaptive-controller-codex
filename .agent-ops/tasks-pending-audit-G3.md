# Task G3: browser-shell XSS and resource-leak audit (read-only report)

Scope: `docs/dashboard-platform/shell/index.html` and
`docs/dashboard-platform/shell/plugins/*.js`, plus the SSE/subscriber and journal
bookkeeping in `ground_station/service/api.py` and `core.py`.
Tier 2. **READ ONLY. Do not edit any file except your result file.** Do not POST to
the live 8081 service. Do not touch firmware.

You are producing evidence, not fixes.

## What to check

1. **XSS.** Grep the shell and every plugin for `innerHTML`, `insertAdjacentHTML`,
   `outerHTML`, `document.write`, and template literals fed into any of them. For each
   hit, decide whether the interpolated value can carry attacker- or device-controlled
   text. The values that matter here: DWARF **symbol names** read off the firmware,
   telemetry field names, **copilot chat text**, journal/activity entries, plan step
   descriptions, session and recording names, and error strings from the service.
   A static string or a number is not a finding — do not pad the report with those.
   Report each real one as: file:line, which value reaches it, and the path that value
   travels from its source.
2. **Resource leaks.**
   - SSE: is a disconnected subscriber removed from the subscriber set on every exit
     path (client close, broken pipe, exception), or only on the clean one?
   - Threads: any thread started per request or per session that is never joined or
     never exits.
   - Unbounded growth: dicts, lists, deques, journals, activity logs and per-session
     caches that only ever get appended to. Say which ones have a cap and which do not,
     and estimate the growth rate for a long session (e.g. "one entry per telemetry
     frame at 20 Hz" is a real leak; "one per operator click" is not).
3. **Event listeners** in the shell added on panel mount and not removed on unmount —
   report only if a panel can actually be mounted more than once.

## How to report

| # | Severity | file:line | CONFIRMED / PLAUSIBLE | what goes wrong |

CONFIRMED means you demonstrated it. For XSS, the demonstration is: show the source
of the value and show that no escaping happens between source and sink — quote both
lines. For an unbounded structure, show the append site and the absence of any
eviction (`grep` for the variable name and paste the full hit list).
PLAUSIBLE means you read it and believe it — say so. **Never label something
CONFIRMED that you did not verify.**

Propose a fix per finding (usually `textContent` instead of `innerHTML`, or a cap plus
eviction), name the file that should own it, but **do not apply it**.

## Verification of your own work

Run, and paste verbatim:
- `node ground_station/service/tests/node_harness.js 2>&1 | tail -5`
- `python -m pytest ground_station/service -q 2>&1 | tail -3`

Write the report to `.agent-ops/results/audit-G3.md`. Exit cleanly when done.
