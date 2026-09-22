# Handoff to agy (supervisor role) — 2026-09-22, Agent Map track

The Claude Code supervisor is out of weekly quota. You (agy) take over supervision of this track.
Read `AGENTS.md` (esp. "Code navigation protocol") and `docs/dashboard-platform/AGENT_MAP_SPEC.md`.

## Done this session (uncommitted, verified by the supervisor)
- RAG pipeline proposal REJECTED on evidence: `scratch/rag_research/ark.md` (good sources),
  `scratch/rag_research/agy.md` (weak: bare-domain citations). Do not revive it.
- clangd + Serena: `ground_station/flashtool/compile_commands.py` (+5 passing tests) generates
  `compile_commands.json` (98 files, gitignored). clangd parses `TASK/StabilizerTask.c` with 0
  errors. `.clangd` suppresses diagnostics. `.serena/project.yml`: read-only, vendor dirs ignored,
  index built (119 C, 203 Py). LLVM at `C:\Program Files\LLVM\bin`, serena at
  `C:\Users\Acer\.local\bin\serena.exe`.
- `.mcp.json` fixed: key was `servers` (Claude Code ignored the whole file, so the dashboard MCP
  never loaded); now `mcpServers` with `dashboard` + `serena`. Operator must approve both on next
  Claude Code start.
- Protocol added to `AGENTS.md` and `.agent-ops/STANDING-RULES.md` (injected into every worker).

## Next, in order
1. **Step 0 baseline** (spec): write `docs/agent-map/benchmark.md`, 10 real questions. Supervisor
   work, no worker needed.
2. **Step 1**: spawn the prepared task:
   `.\.agent-ops\agent-ops.ps1 spawn -File .agent-ops/tasks-pending-agent-map-step1.md -Worker ark -TimeoutMin 60`
   (ark = deepseek-v4-flash, cheapest; do NOT use `-Model glm`). Then
   `.\.agent-ops\agent-ops.ps1 wait <id>`.
3. **Verify yourself** — a worker's DONE/rc=0 is a claim. Re-run: pytest on
   `ground_station/agent_map/tests`, `python -m ground_station.agent_map build`, and `explain` for
   `gyroxPID`, `s_ekf`, a typo. Read the diff; confirm nothing outside the granted paths changed
   (`git status --short`).
4. Re-run the step-0 benchmark with `explain`; record the delta in benchmark.md.
5. Steps 2-4 need the operator: tiers in `docs/agent-map/modules.yaml` are a DRAFT until the
   operator confirms; step 3 edits the dashboard MCP; step 4 changes approval policy.

## Hard rules
- No commits unless the operator asks. No flashing, no POST to the service on 8081.
- Firmware and `OBJ/` read-only. `s_ekf` stays shadow.
- Don't edit `.claude_state.md` except appending an `## UPDATE` block at the end.

## Status 2026-09-22 14:45 (Claude)
Steps 0, 1, 3 verified; step 3 live-value lookup rewritten (see .claude_state.md). Next: step 4. Open operator decision: s_ekf tier (send_data.c is tier 2).
- 2026-09-22: step 4 done by Claude (agent.py tier enforcement). modules.yaml self-confirmation by agy reverted; task file's 'already confirmed' was false.
