<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only. No scratch scripts anywhere
   (not at the repo root, nothing under `.agent-ops/served/`).
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each Python file edit run `python -m py_compile <file>`; fix syntax before running tests.
4. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
5. Catch only specific exceptions. Bare `except:` and `except Exception: pass` are forbidden.
6. Keep every existing function name, signature and return shape. Do not reformat code you did not need to touch.
7. One statement per line; no `;`-joined statements.
8. Python 3.10+. No new third-party dependencies.
9. Never import or call `ground_station.flashtool.flasher`, flashtool CLI, ST-Link or any hardware path.
   Never contact a real drone, port 8081, or a serial port.
10. If a command or edit fails twice the same way, change approach; never repeat an identical call.
11. Write the digest `.agent-ops/out/wp7-r1.md` BEFORE printing DONE.
12. Output: no preamble, no summary prose.
</guardrails>

<context>
Goal: the whole workflow-B campaign loop runs against FakeDrone through the real dashboard HTTP API with the
DEFAULT `ApiServer` wiring (no injected campaign_service), plus an operator RUNBOOK for the real lab.
Measured facts (manager read these files on this branch):
- `ground_station/service/api.py` 2627-2631: `if campaign_service is None:` builds
  `CampaignService(agent=self.agent)` with deps_factory None. `self.service.source` holds the service source
  (`core.py` 252: `self.source = source`); tests use `GroundStationService(store=SessionStore(), source="sim")`.
- `ground_station/service/campaign_api.py` 35-36: `if self.deps_factory is None: return 503, {"error": "deps_factory None"}`.
  `CampaignService(agent=None, deps_factory=None, knobs=(), param_timeout_s=30.0)`. `go()` replaces
  wait_for_go / arm_allowed / control / apply_params / on_flight in the deps via dataclasses.replace.
  `state()` returns status idle | running | waiting_for_go | <report.status>, plus waiting_pack, flights, reason, control.
  `apply_params` sends knob changes as an agent plan with `command` steps; `arm_allowed` reads `agent.allow_agent_arm`.
- `ground_station/service/campaign_runner.py` 40-67: `RunnerDeps` fields. The runner calls:
  packs.next_flight_allowed(pack_id, resting_v, cooldown_s); gate.check_change / next_flight_must_hover /
  on_flight_result / record_flight; tuner.propose(history) / tuner.record; monitor.begin_flight / step / end_flight /
  consecutive_aborts; flash(); analyze(flight_id).
- Real classes with those methods:
  - packs: `ground_station/analysis/battery_model.py` `PackRegistry.load(path=None)` (None = packs.yaml next to the
    module; packs.yaml says min_rest_s 60.0, gate_soc_pct 30.0; read it for the pack ids).
  - tuner: `ground_station/analysis/tuner.py` `Tuner(descriptor, seed)`; descriptor from
    `ground_station/analysis/controller_descriptor.py` `load(path)`, files `ground_station/analysis/controllers/pid.yaml`
    and `mrac.yaml` (`CONTROLLERS_DIR`). Descriptor knobs are `Knob` objects (`symbol`, `cmd_id`, `idx`, ...).
  - gate: `ground_station/flashtool/code_gate.py` `CodeGate(*, build, ram_check, ledger_path, clock, obj_dir,
    ram_limit_bytes, tolerance_frac, sil=None, custody=None, protected=None, lkg_j=None)`. code_gate.py itself imports
    only os/yaml/json/re/hashlib/dataclasses; with custody=None it imports `ground_station.flashtool.artifact_custody`,
    so pass injected stubs for build / ram_check / custody and a ledger_path under a temp dir.
  - monitor: `ground_station/service/abort_monitor.py` `AbortMonitor(limits=AbortLimits())`, `AbortSample`.
- `ground_station/service/tests/test_campaign_api.py` lines 62-115: class `FakeClock` and `create_deps(drone, client,
  clock)` (FakeDrone + `WfbClient(drone.send)`, with Mock packs/tuner/gate/monitor/flash/analyze). Fixture `api` at
  ~125-143 injects its own CampaignService with `knobs = [Knob("param", 100, 0, 1.0, 0.0, 10.0, 1.0)]`.
  Its tests use `server.agent.set_control(OPERATOR)` with `OPERATOR = {"mode": "autonomous", "allow_agent_arm": True,
  "source": "operator"}`. Over HTTP the same is `POST /api/agent/control` with that body (api.py 1983 / 2398-2401).
- `ground_station/service/campaigns/example_circle.yaml` uses `controller: pid`; schema in `campaign_schema.py`.
- Base acceptance run before your work: the 5-file pytest command in <tests> printed `82 passed in 49.78s`.
- CEO decisions: sim only. For a non-sim source keep deps_factory None; the 503 text becomes exactly
  `live campaign wiring not built: hardware path needs operator approval`. `flash` in the sim factory is a stub
  returning a fixed fake hash. `analyze` may be a deterministic stub if the flightlab analyzer cannot read FakeDrone
  output (say which in the digest).
- Manager decision: the zero-arg factory cannot see the campaign, so the sim wiring uses the `pid` controller
  descriptor for both the Tuner and CampaignService knobs; the e2e campaign YAML uses `controller: pid`.
</context>

<allow-list>
ground_station/service/campaign_deps.py          (new)
ground_station/service/api.py
ground_station/service/campaign_api.py
ground_station/service/tests/test_campaign_api.py
ground_station/service/tests/test_workflow_b_e2e.py   (new)
docs/workflow-b/RUNBOOK.md                        (new)
docs/dashboard-platform/capability_manifest.json (only if regenerated because a route/description changed)
.agent-ops/out/wp7-r1.md                         (digest)
</allow-list>

<spec>
1. `ground_station/service/campaign_deps.py`:
   - `FakeClock` moved verbatim from test_campaign_api.py (callable returning t; `sleep(dt)` advances t).
   - `sim_knobs(controller: str = "pid") -> tuple[Knob, ...]` = knobs of controllers/<controller>.yaml.
   - `sim_deps_factory(controller: str = "pid", seed: int = 0, dt_s: float = 0.1, workdir: str | None = None)`
     returns a zero-arg callable; each call builds a NEW `FakeDrone()` + `WfbClient(drone.send)` + `FakeClock()` and
     returns `RunnerDeps` with the REAL `AbortMonitor()`, `PackRegistry.load()`, `Tuner(descriptor, seed)` and
     `CodeGate(...)` (stub build / ram_check / custody, ledger under workdir or a tempfile.mkdtemp()).
     sample/step like create_deps (step advances FakeClock and FakeDrone; keep the real-time yield small).
     flash = stub returning a fixed fake hash string. analyze = real analyzer if it works on FakeDrone, else a
     deterministic stub. resting_v must let PackRegistry allow the flight (a full-pack voltage for the pack's cells),
     cooldown so no real 60 s wait happens (FakeClock time, or cooldown_min_s 0). change_request returns None.
     Use Mock only where no real class exists; list every Mock/stub in the digest.
2. `test_campaign_api.py`: delete its FakeClock class, `from ground_station.service.campaign_deps import FakeClock`.
   Change nothing else in it.
3. `api.py` near 2627: if campaign_service is None and `getattr(service, "source", None) == "sim"`:
   `CampaignService(agent=self.agent, deps_factory=sim_deps_factory(), knobs=sim_knobs())`; otherwise
   `CampaignService(agent=self.agent)` as now. Smallest change.
4. `campaign_api.py` line 36: the new 503 error text (exact string above).
5. If any route list / description in api.py changed, regenerate `docs/dashboard-platform/capability_manifest.json`
   with the repo's own generator (see `ground_station/platform/tests/test_capability_manifest.py` for how).
6. `docs/workflow-b/RUNBOOK.md`, operator steps in this order: install the phone app (adb install); bench-check yaw
   sign; clamp the phone and start recording; mark the end wall and pad centre; label packs (ids as in packs.yaml);
   review crash/abort thresholds (`abort_monitor.py` AbortLimits are PROPOSED, not flight-validated); write a campaign
   with the flight-campaign skill; open the Campaign panel; set allow_agent_arm; per-battery Go with the checklist;
   Pause / Land / Abort; RC ch10 kill; what each end status means (complete, arm_refused, operator_stop,
   operator_needed, error, and any other status the runner can return — read campaign_runner.py) and what to do next.
   State plainly near the top: live (non-sim) campaigns are not wired yet; Go on a non-sim service returns 503.
   Do not invent numbers: quote values only from files you read, with the file name.
</spec>

<tests>
New `ground_station/service/tests/test_workflow_b_e2e.py`: ephemeral-port `ApiServer(GroundStationService(
store=SessionStore(), source="sim"))` with NO campaign_service argument; campaign YAML written to tmp_path with
`controller: pid`, 2 packs from packs.yaml, 1-2 short experiments (copy the shape of example_circle.yaml; keep flights
short). Every step over HTTP (`/api/agent/control`, `/api/campaign/go|state|pause|land|abort`). At least 5 tests:
  a. go with source "agent:x" -> 403; go with a checklist containing one False -> 409.
  b. allow_agent_arm False (POST /api/agent/control) -> go -> state ends `arm_refused`, no flight armed.
  c. allow_agent_arm True -> go pack 1 -> `running` -> flights appear -> `waiting_for_go` with waiting_pack = pack 2
     -> go pack 2 -> `complete`; every flight has a decision; flight count matches the YAML.
  d. pause, land and abort during a run each reach their documented status (land -> operator_stop,
     abort -> operator_needed; pause: read the runner for its documented behaviour and assert it).
  e. an ApiServer on a non-sim GroundStationService -> go -> 503 with the new text.
Poll state with a deadline (short sleeps, e.g. 0.05 s); no long fixed sleeps. Always stop the server in a finally/fixture.
Whole file must run under 60 s.
Run, and paste the last 3 lines of each:
  python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_workflow_b_e2e.py --durations=5
  python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_campaign_api.py ground_station/service/tests/test_runner.py ground_station/service/tests/test_agent.py ground_station/service/tests/test_abort_monitor.py ground_station/platform/tests/test_capability_manifest.py
The second must show no failures (base: 82 passed).
</tests>

<digest>
`.agent-ops/out/wp7-r1.md`, at most 30 lines: files changed; each command + its last 3 output lines; which
deps are real vs Mock/stub (and whether analyze is real or a stub); whether the manifest was regenerated;
deviations from this spec; open risks.
</digest>
