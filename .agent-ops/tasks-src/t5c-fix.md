# Task T5c: fix 5 test failures on branch worker/20260924-191827 (commit a147425)

In your worktree: `git merge worker/20260924-191827`. That commit adds the T5 terminal panel.
The supervisor already ran the tests. The exact failures (do not re-derive them with a full-tree run):

1. `ground_station/service/tests/test_agent_mcp.py::test_mcp_initialize_tools_and_run_plan`
   (line 97): the expected MCP tool-name list does not include the 3 new tools
   (`ui_navigate`, `ui_highlight`, `file_finding`). Update the expected list in the test to add
   exactly those 3 names in the order the server returns them. Nothing else in that test.
2. `ground_station/service/tests/test_all_panels_audit.py::TestAllPanelsAudit::test_all_panels_offline_audit`:
   `Panel terminal-panel.js failed: sandbox.pluginInit is not a function`.
   Open one existing, passing panel in `docs/dashboard-platform/shell/plugins/` and the audit test,
   see how that panel registers itself / exports its init, and make `terminal-panel.js` follow the
   same pattern. Fix the panel, not the audit.
3. `ground_station/service/tests/test_service.py::test_http_api_routes_endpoint` (line 654):
   `AssertionError: /api/terminal/ws`. Read the test around line 654: the route list must match the
   documented/declared route table. Register `/api/terminal/ws` wherever the test expects routes to
   be declared (the same place other routes are listed), or, if the test calls each route via plain
   GET and a websocket route cannot answer 200, follow how the test already handles special routes
   (e.g. `/experiments` -> 503). Choose the smallest correct change and say which in the result.
4. `ground_station/service/tests/test_service.py::test_http_api_manifest_endpoint` (line 879):
   `assert 18 == 17`: one new panel was added. Update the expected count to 18 only after checking
   that the 18th entry is the Terminal panel.
5. A 5th failure is outside `ground_station/service`. Find it with
   `python -m pytest ground_station/platform ground_station/research .agent-ops/tests -q -p no:cacheprovider --tb=line`
   and fix it the same way (it is likely the capability manifest or a panel count).

Do NOT skip, delete, or loosen any other assertion. Keep each file's line endings
(`git diff --stat main` must not show whole-file rewrites).

## Verification (paste last lines verbatim)
- `python -m pytest ground_station/service/tests/test_agent_mcp.py ground_station/service/tests/test_all_panels_audit.py ground_station/service/tests/test_service.py -q -p no:cacheprovider`
- Last step, once: `python -m pytest ground_station .agent-ops/tests -q -p no:cacheprovider -o faulthandler_timeout=120`
  (through win.sh with `WIN_TIMEOUT=1500`).

Commit on your branch (message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`),
then write the result file. This overrides the standing "create no commits" rule for your branch only.
Rules: no firmware edits, no probe, no flashing, no contact with 127.0.0.1:8081.

## Workspace rule (hard)
Work ONLY inside your worktree. `git rev-parse --show-toplevel` must end in .worktrees/<id>.
