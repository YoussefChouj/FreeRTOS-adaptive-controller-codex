# Trial task: reject cross-site POSTs to the dashboard service (2026-09-23)

The dashboard service (`python -m ground_station.service`, port 8081) accepts POSTs that
can change agent mode and run plans. A web page on another site open in the operator's
browser could POST to http://127.0.0.1:8081 (CSRF), or reach it through DNS rebinding.

1. Find the HTTP request handler in `ground_station/service/` and report file:line.
2. Before dispatching ANY POST/PUT/DELETE:
   - reject with 403 if the `Host` header's hostname is not one of 127.0.0.1,
     localhost, ::1 or the host the server was bound to;
   - reject with 403 if an `Origin` header is present and its host is not in that
     same list;
   - reject with 415 unless Content-Type is application/json (an empty body with no
     Content-Type is allowed).
   Put this in ONE function with a small interface (request headers in, None or an
   error tuple out) so it can be unit-tested without sockets. GET stays unchanged.
3. Check that nothing legitimate breaks: grep every `fetch(` POST in
   `docs/dashboard-platform/shell/` and every POST in `ground_station/service/agent_mcp.py`,
   and confirm they send JSON. List any that do not, and fix them.
4. Tests: unit tests for the function (good host, evil host, evil origin, no origin,
   form content type) plus one in-process server test (not 8081).

Verify: `python -m pytest ground_station/service -q` once through win.sh, and
`node ground_station/service/tests/node_harness.js`. Paste the result lines verbatim.
Do NOT POST to the live service on 8081. Touch only `ground_station/service/` and, for
item 3 only, the shell plugins. Exit cleanly when done.
