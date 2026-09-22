# Finish: cross-site POST guard (2026-09-23)

A previous worker started `.agent-ops/tasks-pending-trial-host.md` (read it: that is the spec)
and died midway. State in `ground_station/service/api.py`:
- `_validate_request_headers(headers)` exists near line 23 and is called at the top of
  `do_POST` (~line 1593).

Finish it:
1. Content-Type: accept any media type whose part before `;` is `application/json`
   (case-insensitive), e.g. `application/json; charset=utf-8`. Keep 415 otherwise.
2. Also allow the host the server was bound to (spec item 2), if it is not a loopback name.
   If the bound host is 0.0.0.0, allow only loopback names.
3. On a rejection, call the existing `self._drain_body()` before `self._json(...)`.
4. Check do_PUT / do_DELETE if they exist; guard them the same way.
5. Spec item 3: confirm every POST in `docs/dashboard-platform/shell/` and
   `ground_station/service/agent_mcp.py` sends JSON with a JSON Content-Type.
   List the ones you checked in the result file. Fix only real mismatches.
6. Spec item 4: tests in a NEW file `ground_station/service/tests/test_post_guard.py`:
   unit tests (good host, evil host, evil origin, no origin, form content type, json with
   charset, empty body without content type) plus one in-process server test (ephemeral
   port, not 8081).

Verify through win.sh: `python -m pytest ground_station/service -q` and
`node ground_station/service/tests/node_harness.js`. Paste the result lines verbatim.
Do NOT POST to the live service on 8081. Touch only `ground_station/service/` (and the
shell plugins only for a real item-5 mismatch). Exit cleanly when done.
