
# WP-4 brief (CEO -> Manager), 2026-10-01. Workflow B task G13: campaign API + MCP tools + arm gate.

Base: `workflow-b` (gate with `--base workflow-b`). Spec: `.agent-ops/grill-autonomous-flight-loop.md` Q2
(line 33), A4, summary item 1 (line 252 on); plan: `docs/workflow-b/build-plan.md` amendment G13.

Goal: the operator and the agent drive the G12 runner (`service/campaign_runner.py`, merged in WP-3)
through the dashboard HTTP API and the dashboard MCP server. Tests use an ephemeral-port `ApiServer`
and FakeDrone, never 8081.

Facts the CEO measured on 2026-10-01 (workflow-b @ 5b20102):
- `service/api.py`: one handler from `make_handler`, GET and POST routes as `elif route == "/api/..."`
  chains (agent control at lines 1972 GET and 2353 POST); route descriptions in dicts near lines 475/519.
  `_AGENT` is the agent layer; `_AGENT.allow_agent_arm` is memory-only, default False, and only a non-agent
  source may set it (403 otherwise, agent.py lines 619-745).
- `service/tests/test_agent.py` fixtures `service` + `api` start `ApiServer(service)` on an ephemeral port.
- `service/agent_mcp.py`: `TOOLS` list (line 30) + `McpServer._call_tool` (line 240) calling `_http(...)`.
- Runner API (`service/campaign_runner.py`): `run_campaign(yaml_path, deps: RunnerDeps) -> CampaignReport`
  (flights: list[FlightRecord], status in complete|operator_stop|operator_needed|gate_refused|arm_refused, reason).
  `RunnerDeps` fields: client, step, clock, sleep, status, sample, packs, monitor, tuner, gate, flash, analyze,
  wait_for_go(pack_id), resting_v(pack_id), arm_allowed, change_request, diff_source, dt_s, flight_timeout_s...
  The flight loop is `run_loop_until(cond, phase)` (line 168); level-1 abort = traj_stop + land (line 199).
  It has no pause/land/abort hook yet.

Wanted:
1. `service/campaign_api.py`: `CampaignService` holds one runner thread and its state; deps from an injected
   factory (tests wire FakeDrone). `arm_allowed` is `lambda: _AGENT.allow_agent_arm` (False if no agent).
2. Routes in `api.py`: GET `/api/campaign/state`; POST `/api/campaign/go` {campaign_path, pack_id, checklist}
   (operator only: 403 for an `agent:` source; 409 unless every checklist item is true); POST
   `/api/campaign/pause`, `/land`, `/abort` (any source; land = level-1 traj_stop+land, abort = level-1 then
   status operator_needed). Add each to the route-description dicts, then regenerate
   `docs/dashboard-platform/capability_manifest.json` with `ground_station/platform/capability_manifest.py`
   (`platform/tests/test_capability_manifest.py::test_capability_manifest_no_drift` checks it).
3. If the runner lacks a pause/land/abort hook, add the smallest one to `campaign_runner.py` (a control object
   polled each step) and extend `test_runner.py`; keep all WP-3 tests passing.
3b. CEO review of the merged runner, fix in `campaign_runner.py` (+ `flashtool/code_gate.py` only if needed),
   each with a test:
   - Tuned params never reach the drone: add dep `apply_params(params)`, called after `tuner.propose` and
     before arm. The `CampaignService` factory wires it to the existing param-write command path of the
     agent layer (agent.py 0x01 PID_GAIN, 0x02 MRAC_GAMMA ...), so the agent's approval rules still apply.
   - Revert never reflashes: when the gate decides "revert", flash the restored last-known-good build
     (`deps.flash()`) before the next arm and record its hash.
   - Spec (grill line 220): first flight after a change = short hover only; auto-revert on abort or worse
     metrics. So a change is judged over two flights: the hover flight (abort -> revert, J not compared),
     then the first trajectory flight (abort or J worse than last-known-good -> revert, else keep).
     Call `gate.on_flight_result` only on those flights; other flights call only `record_flight`.
4. MCP tools: `campaign_state`, `campaign_pause`, `campaign_land`, `campaign_abort`. No `go` tool: the per-battery
   go is the operator's (Q2).
Tests `service/tests/test_campaign_api.py`: go refused for agent source and for an unticked checklist; go then
state shows flights against FakeDrone; arm refused while allow_agent_arm False; pause/land/abort; plus MCP tool
listing and one call each in `service/tests/test_agent_mcp.py`.

Acceptance (run by you):
- First, on the base: `python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_agent.py ground_station/service/tests/test_agent_mcp.py ground_station/service/tests/test_runner.py ground_station/platform/tests/test_capability_manifest.py ground_station/flashtool/tests/test_code_gate.py` -> note the last line.
- `python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_api.py` -> 0 failed, at least 7 tests
- the first command again -> no new failures vs the base line
- `python .agent-ops/gate.py --base workflow-b --max-lines 900 --allow ground_station/service/campaign_api.py --allow ground_station/service/api.py --allow ground_station/service/agent_mcp.py --allow ground_station/service/campaign_runner.py --allow ground_station/flashtool/code_gate.py --allow ground_station/flashtool/tests/test_code_gate.py --allow "ground_station/service/tests/test_campaign_api.py" --allow ground_station/service/tests/test_agent_mcp.py --allow ground_station/service/tests/test_runner.py --allow docs/dashboard-platform/capability_manifest.json --allow ".agent-ops/out/*" --allow "docs/agent/reports/*" --allow ".agent-ops/tasks-src/*"` -> GATE PASS

Scope (worker may edit): the files in the gate line above, its digest `.agent-ops/out/wp4-r<n>.md`
Allow globs: as the gate line.
Worker lane: agy-vps, chain `agy:gemini-3.1-pro-high,agy:gemini-3.8-flash-high`   Max worker rounds: 3
Report to: `docs/agent/reports/WP-4.md`; add one line: any MANAGER.md rule that was unclear or a denied command.

## CEO decision 2026-10-01, after the manager's BLOCKED report (for the CTE)
- Keep wp/4 at 2c658cf (do not reset). `git rm` the 10 root scratch scripts (compress*.py, manual_compress*.py,
  patch*.py and any other new root file), then make ruff clean by hand: one statement per line, no `;`, no
  duplicate imports. Do not reformat files you did not need to touch.
- Split `test_all_api_new` into one named test per item: apply_params True path against the real agent layer,
  live flights visible in /api/campaign/state, runner error status, bad JSON -> 400. Each must assert its own outcome.
- Size: if the gate still exceeds 900 after the scratch removal, use `--max-lines 1100`; report the measured total.
- Acceptance commands are otherwise unchanged. Report to `docs/agent/reports/WP-4-cte.md`.