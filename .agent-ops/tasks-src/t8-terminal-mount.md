# Task T8: Terminal workspace renders no panel

Symptom (measured): `python -m ground_station.service.browser_smoke` against the live service prints
`== Terminal: errs+0 nan=0 undef=0 panels=[]`. Every other workspace lists its `panel-*` elements
(for example Bench -> ['panel-motor-bench']). The terminal plugin is
`docs/dashboard-platform/shell/plugins/terminal-panel.js`, which registers with
`window.__registerPlugin__("Terminal", ..., {workspace:"terminal"})` (fixed in commit 6dbd284).

Do:
1. Find how the shell turns a plugin registration into a `panel-<id>` element for a workspace, how
   browser_smoke collects `panels=`, and whether the terminal plugin is loaded at all. Check
   `index.html`/plugin loader lists, the workspace id spelling, the element id/class the plugin
   creates, and any server-side static route or plugin manifest in `ground_station/service/`.
2. Fix the root cause with the minimum change. Add or extend a unit test if the repo has tests for
   plugin lists or manifests.
3. Run the service tests: `.agent-ops/win.sh "python -m pytest ground_station/service -q -p no:cacheprovider"`
   (run it in your worktree) and paste the summary line.

Rules: do NOT contact 127.0.0.1:8081 or run browser_smoke against it; do not start a service on
8081. You may start a throwaway instance on another port (for example 8093) if the service supports
`--port`, and run browser_smoke against that one. No probe use, no flashing. Commit on your branch
(message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`). Keep existing line
endings (LF). Write the result file with the root cause, the diff stat and the test summary.

## Workspace rule (hard)
Work ONLY inside your worktree. `git rev-parse --show-toplevel` must end in .worktrees/<id>.
