<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only.
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each file edit run `python -m py_compile <file>`; fix syntax before running tests.
4. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
5. Catch only specific exceptions. Bare `except:` and `except Exception: pass` are forbidden.
6. Keep every existing function name, signature and return shape. Additions allowed only where the
   spec below names them (new keyword args always get a default that keeps old behaviour).
7. Python 3.10+, standard library only (numpy/yaml already used by the repo are fine to import indirectly).
8. If a command or edit fails twice the same way, change approach; never repeat an identical call.
9. Never start a server on port 8081, never contact 8081, never flash. Tests use ephemeral ports + FakeDrone.
10. Write the digest `.agent-ops/out/wp4-r1.md` BEFORE printing DONE.
11. Output: no preamble, no summary prose.
</guardrails>

<context>
Goal: the operator and the agent drive the campaign runner (`ground_station/service/campaign_runner.py`)
through the dashboard HTTP API and the dashboard MCP server. Spec source: `.agent-ops/grill-autonomous-flight-loop.md`
Q2 (line 33), A4, summary from line 252; line 220-221 (first flight after a change = short hover; auto-revert).
Measured facts (base workflow-b @ 5b20102):
- `service/api.py`: `make_handler(service, hub=None, static_root=None, experiment_runtime=None, agent=None,
  copilot=None, terminal_manager=None)` (line 1104) captures `_AGENT = agent`. GET and POST routes are
  `elif route == "/api/..."` chains; agent control: GET line 1972, POST line 2353 (pattern: read body via
  `self._content_length()` + `json.loads(self.rfile.read(length) or b"{}")`, reply `self._json(code, obj)`,
  400 when body lacks "source"). Route-description dicts: "GET" ends line 498, "POST" ends line 526.
  `ApiServer.__init__` at line 2574 builds `self.agent` then calls `make_handler(...)` at line 2610.
- `service/agent.py`: `allow_agent_arm` memory-only, default False; `set_allow_agent_arm(value, source)` line 800;
  `create_plan(payload)` line 836 needs `source` starting with "agent:" and raises `AgentDisabledError` when
  mode is "off"; a step `{"action": "command", "args": {"command_id", "index", "value"}}` runs through
  `svc.submit_command` (line 1481) = the same path as POST /commands (arm gate + approval rules,
  `_step_needs_approval` line 898). Plan status: pending | running | done | failed | cancelled; `get_plan(id)` line 963.
- `analysis/controller_descriptor.py` `Knob(symbol, cmd_id, idx, default, lo, hi, scale)`; `analysis/tuner.py`
  `Tuner.propose(history) -> dict[symbol, float]`, knobs from `descriptor.knobs`.
- `service/campaign_runner.py`: `RunnerDeps` dataclass (line 13), `run_campaign` (line 79). Flight loop
  `run_loop_until(cond, phase)` line 168 polls `deps.monitor.step` each tick; level>=1 sets aborted and
  line 199-201 does `traj_stop()` then `land()`. `params = deps.tuner.propose(history)` line 155 is never sent to
  the drone. Line 228-229 call `gate.on_flight_result(aborted, j)` and `gate.record_flight` on EVERY flight.
- `flashtool/code_gate.py`: `on_flight_result` (line 155) reverts via `custody.restore(obj_dir)` on abort or
  `_worse(j)`, else `custody.commit` + `lkg_j = j`; then `pending_change = False`. So today the hover flight after
  a change already decides keep/revert from hover J, and a revert restores files but nothing reflashes them.
  `next_flight_must_hover()` = `pending_change and flights_since_change == 0`; `record_flight` increments
  `flights_since_change` while `pending_change`.
- `service/agent_mcp.py`: `TOOLS` list line 30, `McpServer._call_tool` line 240 calling `_http(...)`.
- Tests: `service/tests/test_agent.py` fixtures `service` + `api` start `ApiServer(service)` on an ephemeral port;
  `service/tests/test_runner.py` builds RunnerDeps with `ground_station.service.fake_drone.FakeDrone`, `WfbClient(drone.send)`,
  a FakeClock and Mock gate/tuner/monitor (copy that pattern).
- Base run of the acceptance set (test_agent, test_agent_mcp, test_runner, test_capability_manifest, test_code_gate):
  `1 failed, 77 passed`; the one failure is `test_capability_manifest_no_drift` (manifest already stale on base).
  `OBJ/JX_FLY.axf` is git-tracked.
</context>

<allow-list>
ground_station/service/campaign_api.py          (new)
ground_station/service/api.py
ground_station/service/agent_mcp.py
ground_station/service/campaign_runner.py
ground_station/flashtool/code_gate.py
ground_station/flashtool/tests/test_code_gate.py
ground_station/service/tests/test_campaign_api.py   (new)
ground_station/service/tests/test_agent_mcp.py
ground_station/service/tests/test_runner.py
docs/dashboard-platform/capability_manifest.json    (regenerated only, never hand-edited)
.agent-ops/out/wp4-r1.md                            (digest)
</allow-list>

<spec>
A. campaign_runner.py (smallest changes; keep all existing behaviour not named here)
  1. `class RunnerControl`: thread-safe (threading.Lock). `request(cmd: str)` with cmd in {"pause","land","abort"}
     (ValueError otherwise); `get() -> str | None` returns the strongest pending request, abort > land > pause;
     `clear()`.
  2. New RunnerDeps fields, appended at the end with defaults:
     `control: RunnerControl | None = None`, `apply_params: Callable[[dict], bool] = lambda params: True`.
  3. Between flights (top of every loop iteration, and when wait_for_go returns False): if control.get() is
     "abort" -> return CampaignReport(..., "operator_needed", "operator abort"); "land" -> ("operator_stop",
     "operator land"); "pause" -> ("operator_stop", "operator pause"). wait_for_go False with no request keeps
     today's ("operator_stop", "").
  4. In run_loop_until, each tick: if control.get() in ("land", "abort"): aborted = True,
     decision = AbortDecision(level=1, reason=f"operator {cmd}"), return False. The existing level-1 path then
     does traj_stop + land. "pause" never interrupts a flight. After landing, the step-3 check ends the campaign.
  5. Right after `params = deps.tuner.propose(history)` and before `client.arm()`: `if not deps.apply_params(params):`
     return CampaignReport(..., "operator_needed", "param write refused").
  6. Change judged over two flights. Runner-local state set when a change is flashed (line 141):
     judge = "hover". Flight with judge == "hover": `gate.on_flight_result(aborted=aborted, j=j, hover=True)`;
     result "revert" -> judge = None; else judge = "traj". Next flight with judge == "traj":
     `gate.on_flight_result(aborted=aborted, j=j)`; judge = None. Any other flight: no on_flight_result call,
     FlightRecord.decision = "". `gate.record_flight(flight_id, fw_hash)` still runs on every flight.
  7. When a decision is "revert": `fw_hash = deps.flash()` immediately (drone is landed, before the next arm);
     new FlightRecord field `reflash_hash: str = ""` (appended last, default) holds that hash.
B. code_gate.py: `on_flight_result(self, aborted, j, hover: bool = False) -> str`. hover and not aborted ->
   return "pending": no custody call, lkg_j unchanged, pending_change stays True, log
   {"event": "flight_result", "decision": "pending"}. Every other case unchanged.
C. campaign_api.py: `class CampaignService(agent=None, deps_factory=None, knobs=(), param_timeout_s=30.0)`.
   - Holds one runner thread (daemon) and its state under a lock; `control = RunnerControl()` per run.
   - `go(campaign_path, pack_id, checklist, source) -> (code, body)`: source missing/empty -> 400;
     source.startswith("agent:") -> 403; checklist not a non-empty dict whose values are all `True` -> 409;
     deps_factory None -> 503. No run active: build `deps = deps_factory()` and replace
     wait_for_go / arm_allowed / control / apply_params with dataclasses.replace, start the thread running
     `run_campaign(campaign_path, deps)`, store the report when it returns. Run active: grant the go for pack_id.
     Return 200 with state().
   - wait_for_go(pack_id): True at once if pack_id has a go grant; else state "waiting_for_go" (expose pack_id)
     and block on a threading.Condition until a grant for pack_id (True) or any control request (False).
   - arm_allowed: `lambda: bool(agent is not None and agent.allow_agent_arm)`.
   - apply_params(params): {} -> True. Any symbol not in knobs -> False. Agent None -> False. Else
     `agent.create_plan({"title": "campaign params", "source": "agent:campaign", "steps": [command steps
     {"command_id": k.cmd_id, "index": k.idx, "value": v}]})`; catch the agent's specific exceptions
     (AgentDisabledError, PlanBusyError, ValueError) -> False; poll `agent.get_plan(id).status` until done
     (True) / failed or cancelled (False) / param_timeout_s elapsed or abort/land requested (False).
   - `request(cmd)` for pause/land/abort -> 409 if no run active, else control.request + notify the condition; 200.
   - `state() -> dict`: {"status": idle|running|waiting_for_go|<report.status>, "waiting_pack": str|None,
     "campaign_path", "reason", "flights": [dataclasses.asdict(f) ...], "control": control.get()}.
D. api.py: `make_handler(..., campaign=None)`, `ApiServer(..., campaign_service=None)`; ApiServer sets
   `self.campaign = campaign_service or CampaignService(agent=self.agent)` and passes it. Routes:
   GET `/api/campaign/state`; POST `/api/campaign/go` {campaign_path, pack_id, checklist, source};
   POST `/api/campaign/pause`, `/api/campaign/land`, `/api/campaign/abort` (any source). 503 when campaign is None.
   Add each route with a one-line description to the GET/POST dicts. Then regenerate the manifest:
   `python -m ground_station.platform.capability_manifest`.
E. agent_mcp.py: tools `campaign_state` (GET /api/campaign/state), `campaign_pause`, `campaign_land`,
   `campaign_abort` (POST with the MCP's agent source, same pattern as existing tools). No go tool.
</spec>

<tests>
New `ground_station/service/tests/test_campaign_api.py` (at least 7 tests, ephemeral-port ApiServer like
test_agent.py, CampaignService with a deps_factory built from FakeDrone exactly like test_runner.create_deps,
FakeClock so flights finish fast; poll state with a real-time deadline <= 10 s):
  1. go with source "agent:x" -> 403. 2. go with an unticked checklist item -> 409.
  3. allow_agent_arm True (via agent.set_allow_agent_arm(True, "operator")) + go -> state reaches a final status
     and shows >= 1 flight.  4. allow_agent_arm False + go -> status "arm_refused".
  5. pause / 6. land / 7. abort while the runner waits for go on a second pack (or mid-flight) -> statuses
     operator_stop / operator_stop / operator_needed; land/abort with no run -> 409.
  8. apply_params with a knob table: an unknown symbol -> False; a known one creates an agent plan whose step
     args carry the knob's cmd_id/idx/value.
test_runner.py: add tests for RunnerControl land (mid-flight -> traj_stop+land, abort_reason "operator land",
status operator_stop), abort (operator_needed), apply_params called with the proposed params before arm and
False -> "param write refused", two-flight judging (on_flight_result called with hover=True then without,
not on other flights), revert -> flash called again and reflash_hash set. Existing tests must pass; if one
asserts the old every-flight on_flight_result call, update only that assertion and say so in the digest.
test_code_gate.py: hover=True + not aborted -> "pending", no custody call, next_flight_must_hover False after record_flight.
test_agent_mcp.py: the 4 tools listed; one call each against an ephemeral ApiServer (or the existing fake _http pattern).
Run, each separately:
  python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_api.py
  python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_agent.py ground_station/service/tests/test_agent_mcp.py ground_station/service/tests/test_runner.py ground_station/platform/tests/test_capability_manifest.py ground_station/flashtool/tests/test_code_gate.py
  ruff check <every .py file you changed>   (if ruff is missing write "ruff: not installed")
</tests>

<digest>
`.agent-ops/out/wp4-r1.md`, at most 30 lines: files changed, each command + its last 3 output lines,
deviations from this spec, open risks.
</digest>
