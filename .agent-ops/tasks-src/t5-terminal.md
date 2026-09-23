# Task T5: the dashboard terminal agent and the findings channel

Read docs/research-platform/SPEC.md; it is binding. Also read docs/dashboard-platform/AGENT_GUIDE.md.
Rules: no firmware edits, no probe, no flashing, no contact with 127.0.0.1:8081. Test with a service started by the tests on an ephemeral port only.

## Build
1. The service's terminal endpoint.
   - A WebSocket route (for example `/api/terminal/ws`) that spawns a PTY running a configurable command.
     - Default: `wsl -e bash -lc "cd <repo wsl path> && opencode"`.
     - Env `TERMINAL_CMD` overrides it; `TERMINAL_AGENT=claude` switches to `claude`.
     - On Windows use pywinpty if available, else report "terminal unavailable" and why.
   - Bind only to loopback. Require a one-time token that the service prints at start and writes to `.agent_state/terminal-token` (gitignored), passed as `?token=`.
   - Handle resize messages.
   - Tests use a harmless command (`python -c "print('hi')"`).
2. The shell plugin `docs/dashboard-platform/shell/plugins/terminal-panel.js`.
   - xterm.js and xterm-addon-fit from cdn.jsdelivr.net/npm.
   - A new "Terminal" tab, registered in PANEL_META and the capability manifest mirror (`ground_station/platform/capability_manifest.py`). Regenerate `docs/dashboard-platform/capability_manifest.json` with `python -m ground_station.platform.capability_manifest`.
   - A token input stored in sessionStorage in try/catch.
3. Opencode configuration for the dashboard agent. Write `opencode.json` at the repo root, or extend it if it exists. Check the opencode docs format in the existing .agent-ops worker configs first.
   - It includes the `dashboard` MCP server from `.mcp.json`.
   - A permission policy: allow everything EXCEPT asking before any command matching flash, `rebuild_and_flash`, `livewatch write|reset|halt`, or arm.
4. UI navigation for the agent: MCP tools `ui_navigate(tab)` and `ui_highlight(panel)`.
   - The service broadcasts an SSE `ui` event and the shell listens and switches tab or highlights.
   - Add them to the dashboard MCP server and the action registry.
5. Findings channel.
   - The `research/findings/` directory with `TEMPLATE.md`. Frontmatter: id, date, severity, status (open|in-progress|resolved), runs: [ids], summary; then evidence and a suggested action.
   - The CLI `python -m ground_station.research finding new|list` and an MCP tool `file_finding`.
6. `docs/research-platform/AGENT_RESEARCH_GUIDE.md` (at most 120 lines, hand-written style): how the dashboard agent does research here.
   - Autonomy levels, workflows, dry-run-first, envelopes, findings, never flash while armed.
   - Plus a generator `python -m ground_station.research catalog` that writes `docs/research-platform/CATALOG.md` from the workflow library, step signatures and the action registry.
7. Tests: token required (401 without); loopback only; a PTY echo; the ui event reaches SSE; finding frontmatter round-trip; the catalog generator is deterministic.

## Acceptance
- The full tree is green (paste the last line).
- The browser smoke is NOT required (there is no live service).
- Append a "## T5 as built" section (at most 20 lines) to the SPEC.
- Commit on your branch; the message ends with: Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
