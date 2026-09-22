# Task G1: dashboard HTTP-layer audit (read-only report)

Scope: `ground_station/service/api.py`, `core.py`, `storage.py`, `__main__.py`.
Tier 2. **READ ONLY. Do not edit any file except your result file.** Do not POST to
the live 8081 service. Do not touch firmware.

You are producing evidence, not fixes. A finding with no reproduction is worthless.

## What to check

1. **Path traversal.** Every route that maps a request value to a filesystem path:
   static file serving, session ids, recording ids, `analyze_session`, log/journal
   reads, plugin file serving. Try mentally and then *actually*: `..`, `%2e%2e%2f`,
   a leading `/` or `C:\`, backslashes (this is Windows — `os.path.join(root, "..\\x")`
   escapes on Windows even when `/` is filtered), a NUL byte, and a symlink-free
   absolute path. Report whether the final resolved path is checked against the root
   with `os.path.realpath` + `os.path.commonpath` (correct) or with a string prefix
   or a blocklist of `..` (usually bypassable).
2. **Request handling.** Missing body-size limit (can a huge POST body exhaust RAM?),
   `Content-Length` mismatch, malformed JSON, missing Content-Type, chunked encoding.
   Does any handler call `rfile.read(int(headers['Content-Length']))` without a cap
   or without handling a missing/invalid header?
3. **Host / Origin checking on POST.** The service binds 127.0.0.1, so it is reachable
   by any page in the operator's browser (CSRF) and by DNS rebinding. Report exactly
   which checks exist today on POST requests, if any.
4. **The known flake** `ground_station/service/tests/test_agent.py::test_mode_off_cancels_running_plan_and_returns_423`
   — WinError 10053. Theory: the server writes the 423 response and closes before
   reading the request body, so the client's send fails. Confirm by reading the code
   path and say exactly which line returns before draining `rfile`.

## How to report each finding

For each, a row plus a short evidence block:

| # | Severity | file:line | CONFIRMED / PLAUSIBLE |

CONFIRMED means you ran something and saw it. Put the exact command and its real
output under the row. Write a small pytest-style snippet against an in-process
server or call the handler function directly; never against port 8081.
PLAUSIBLE means you read the code and believe it but did not execute it — say so
plainly. **Do not label anything CONFIRMED that you did not actually run.**

Also list, for each real finding, the one-line fix you would make and which module
should own it (the seam), but **do not apply it**.

## Verification of your own work

Run `python -m pytest ground_station/service -q 2>&1 | tail -5` once at the end and
paste it verbatim, so we know the tree is unchanged and green.

Write the report to `.agent-ops/results/audit-G1.md`. Exit cleanly when done.
