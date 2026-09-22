# Task G: dashboard end-to-end audit, bugs and vulnerabilities (2026-09-22)

Scope: `ground_station/service/` and `docs/dashboard-platform/shell/`. Tier 2. Read-only
first; then fix only CONFIRMED findings, each with a test that fails before the fix.
Do NOT edit firmware. Do NOT POST to the live 8081 service; use in-process test servers.

Check at least the following:
1. HTTP layer: path traversal in static and file routes (`..`, encoded `%2e%2e`,
   absolute paths, backslashes on Windows); session, recording and analyze_session paths
   escaping the logs root; request body size limits; bad JSON and Content-Length
   handling; the known flake test_agent.py::test_mode_off_cancels_running_plan_and_returns_423
   (WinError 10053: the server answers 423 before reading the body; drain the body
   first).
2. Safety gates: every POST that can arm, change a param, subscribe or run a plan goes
   through the mode/tier/approval checks. Look for a route that skips them. Check that
   `source: 'operator'` in the body cannot be spoofed by an agent to self-approve.
   Report the trust model you find and whether a fix is possible without auth; do not
   add auth unless it is trivial.
3. CSRF/DNS rebinding: the service is on 127.0.0.1. Does it check Host/Origin on POST?
   If it does not, add a Host allow-list (127.0.0.1, localhost, the bound host) plus a
   JSON Content-Type requirement on POST, with tests.
4. XSS: plugins that put telemetry, symbol names, chat or journal text into innerHTML.
   Switch to textContent where the data is not trusted.
5. Resource leaks: SSE subscriber cleanup, threads, unbounded dicts/queues and
   journals.
6. Concurrency: shared state touched from HTTP threads and the telemetry thread without
   the lock.

Result: a table of findings (severity, file:line, CONFIRMED/PLAUSIBLE, fixed?). Name the
module and seam for each fix. Keep other uncommitted edits. Verification: `python -m
pytest ground_station/service -q` and all `node ground_station/service/tests/*harness*.js`;
paste the results verbatim. Exit cleanly when done.
