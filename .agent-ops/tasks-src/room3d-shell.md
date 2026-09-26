# Task room3d-shell: shell header -> sidebar, and room-scale Paths 3D polish

Read `.agent-ops/tasks-src/vps-common.md` first; its rules apply. Digest: `.agent-ops/out/room3d-shell.md`.
Dashboard front end only. No firmware, no tier-0 files, no drone commands, no push, no hard deletes.


## Hard rules (a previous attempt was rejected for breaking these)
- Never commit node_modules, package.json, package-lock.json or logs/. `git status` before commit; stage files by name.
- No duplicated blocks, no empty handlers, no stubs. Every button you add must do what its label says.
- The digest lists exactly the files in `git diff --stat HEAD~1` and nothing else. Claims you did not verify = rejection.
- Node is available for the harness; do not npm install anything.

## A. Shell layout
The dashboard shell lives in `docs/dashboard-platform/shell/` (find the index HTML/CSS/JS). Move the whole upper
header section (title, connection status, command box, flight-state/status panels, anything above the tab row)
into the existing collapsible sidebar. Only the tab list remains at the top. Keep every element id and
data-selector unchanged so plugins, `ground_station/service/browser_smoke.py` and tests still find them.
Sidebar collapse state persists (localStorage in try/catch). Works at 1280 px and at 800 px wide.

## Tests
Run `PYTHONUTF8=1 python3 -m pytest -q -p no:cacheprovider ground_station/service/tests` before and after; the failure set must not grow. Paste both summary lines.
Do not touch plugins/path-panel.js (another worker owns it).
