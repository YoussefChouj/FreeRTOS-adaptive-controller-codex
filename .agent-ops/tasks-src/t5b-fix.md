# Task T5b: fix the full-tree failures on branch worker/20260924-191827 (commit a147425)

Start your worktree from branch `worker/20260924-191827` (`git merge worker/20260924-191827` inside
your worktree). That commit adds the T5 terminal panel, findings and research catalog
(see `.agent-ops/tasks-src/t5-terminal.md` for the original task).

The supervisor's full-tree run on that commit: `5 failed, 1097 passed, 34 skipped`. Known failures:
- `ground_station/service/tests/test_all_panels_audit.py::TestAllPanelsAudit::test_all_panels_offline_audit`
- `ground_station/service/tests/test_service.py::test_http_api_routes_endpoint`
- `ground_station/service/tests/test_service.py::test_http_api_manifest_endpoint`
- two more: find them with step 1.

## Steps
1. Run `python -m pytest ground_station .agent-ops/tests -q -p no:cacheprovider -o faulthandler_timeout=120 -rf`
   once (through win.sh, `WIN_TIMEOUT=1500`) to get all 5 names and their assertion messages.
2. Fix the cause in the T5 code. Likely: the new terminal route/panel is not registered where
   `/api/routes`, the capability manifest and the panel audit expect it. If a checked-in generated
   file (e.g. capability manifest JSON) is stale, regenerate it with the project's generator; do not
   hand-edit it. Do NOT weaken, skip, or delete any existing test or assertion.
3. Keep the original line endings of every file you touch (`git diff --stat main` must not show a
   whole-file rewrite).
4. Rerun the full tree once as the LAST step and paste its last line verbatim in the result.

Commit on your branch (message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`).
This task overrides the standing "create no commits" rule: commit on your branch only.
Write the result file before exiting. Rules: no firmware edits, no probe, no flashing,
no contact with 127.0.0.1:8081.

## Workspace rule (hard)
Work ONLY inside your worktree. `git rev-parse --show-toplevel` must end in .worktrees/<id>.
