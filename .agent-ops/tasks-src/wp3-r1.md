<guardrails>
1. Edit only the files in the ALLOW-LIST. Every other path is read-only.
2. Write complete implementations. No "...", TODO, `pass` stubs or "rest of code" comments.
3. After each file edit run `python -m py_compile <file>`; fix syntax before running tests.
4. Run every command in the foreground and paste its verbatim output into the digest.
   Report a test as passing only when you ran it and saw "passed" in the output.
5. Catch only specific exceptions. Bare `except:` and `except Exception: pass` are forbidden.
6. Do not change any existing module. Reuse the modules listed in <context>; do not reimplement them.
7. Python 3.10+, standard library + modules already in the repo only.
8. If a command or edit fails twice the same way, change approach; never repeat an identical call.
9. Write the digest `.agent-ops/out/wp3-r1.md` BEFORE printing DONE.
10. Output: no preamble, no summary prose. Minimal code: target campaign_runner.py <= 350 lines,
    test_runner.py <= 350 lines (the total diff must stay under 800 lines).
11. SAFETY: the runner must NEVER call `client.kill()` anywhere. No serial port, no network, no Keil.
</guardrails>

<context>
Workflow-B task G12: the campaign runner, the deterministic autonomous flight loop. Spec sources (read them):
`.agent-ops/grill-autonomous-flight-loop.md` Q2 (line 33, operator-gated battery sessions), Q8 (line 137,
cooldown), Q12 (lines 238-249, abort levels), summary items 1 and 4 (line 252 on);
`docs/workflow-b/build-plan.md` Task 13 + amendment G12.

Measured on the base (workflow-b @ 73bb5ca). Existing APIs to reuse (signatures copied from the source):
- `ground_station/service/campaign_schema.py`: `load_campaign(path) -> Campaign`; Campaign fields
  `packs: tuple[str,...]`, `max_flights: int`, `envelope`, `experiments: tuple[Experiment,...]`, property
  `abort_limits -> AbortLimits`. Experiment fields `name, shape, params, profile, capture, repeats`.
  Example file: `ground_station/service/campaigns/example_circle.yaml`.
- `ground_station/platform/wfb_commands.py`: `WfbClient(send)`; methods arm, idle, kill, takeoff, land,
  heartbeat, set_hover_z, traj_begin/append/commit/start/stop/clear, all `-> bool`. kill() = MOTORS OFF.
- `ground_station/service/fake_drone.py`: `FakeDrone(limits, params, hover_z=0.5)`, `.send(frame) -> int`,
  `.step(dt)`, `.status() -> dict[str,float]` with keys prim_state, traj_state, traj_n, traj_rx, traj_crc_hi,
  traj_crc_lo, traj_t, last_err, safety_trip, hb_age, gs_flight_active, hover_z, airborne_t. Properties
  `armed`, `motors_idle`, `position`, `yaw_deg`; settable `roll_deg`, `pitch_deg`, `vbat_v`, `sbus_live`.
  PrimState IDLE=0 CLIMB=1 HOVER=2 TRAJ=3 RETURN=4 SETTLE=5 DESCEND=6; TrajState EMPTY=0 LOADING=1 READY=2
  EXECUTING=3 DONE=4. hb_age suggests the fake trips HEARTBEAT if heartbeat() is not sent: read the file.
- `ground_station/service/trajectory_pipeline.py`: `generate(shape, params, profile, limits=TrajLimits())
  -> list[TrajPoint]`; shapes line, circle, figure8, square, library (no "hover" shape).
- `ground_station/platform/trajectory_upload.py`: `upload(points, client, attempts=3) -> UploadResult`.
- `ground_station/service/abort_monitor.py`: `AbortMonitor(limits)`, `begin_flight()`, `end_flight()`,
  `step(AbortSample) -> AbortDecision(level:int, reason:str)`, property `consecutive_aborts`.
  `AbortSample(t_s, age_s, airborne, pos_m, ref_m, roll_deg, pitch_deg, rate_err_dps, sat_frac,
  safety_trip=0, soc_pct=None)`.
- `ground_station/analysis/battery_model.py`: `PackRegistry.next_flight_allowed(pack_id, resting_v,
  cooldown_s) -> tuple[bool, str]`, `record_flight(pack_id, soc_before, soc_after)`, `predict_soc(pack_id, resting_v)`.
- `ground_station/analysis/tuner.py`: `Tuner.propose(history) -> dict[str,float]`, `record(params, J, valid)`.
- `ground_station/analysis/workflow_b_adapter.py`: `flight_rows(session_dir)`, `score(rows) -> float`.
- `ground_station/flashtool/code_gate.py`: `CodeGate.check_change(diff_text, justification, files_after)
  -> GateResult(ok, step, reasons)`, `next_flight_must_hover() -> bool`, `on_flight_result(aborted, j) -> str`
  ("keep" | "revert"), `record_flight(flight_id, fw_hash)`.
Baseline: `python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_fake_drone.py
ground_station/service/tests/test_abort_monitor.py ground_station/service/tests/test_campaign_schema.py
ground_station/flashtool/tests/test_code_gate.py` -> `172 passed in 8.41s`.
</context>

<allow-list>
ground_station/service/campaign_runner.py        (new)
ground_station/service/tests/test_runner.py      (new)
.agent-ops/out/wp3-r1.md                         (digest)
</allow-list>

<spec>
New module `ground_station/service/campaign_runner.py`. Every side effect is injected.

@dataclass RunnerDeps (field names exact; numeric args carry unit suffixes):
  client                      WfbClient-like (arm, takeoff, land, heartbeat, traj_start, traj_stop, ...)
  step: Callable[[float], None]          advance the world by dt_s (FakeDrone.step in tests)
  clock: Callable[[], float]             monotonic seconds
  sleep: Callable[[float], None]         wait dt_s (fake in tests: advances the fake clock)
  status: Callable[[], dict]             telemetry dict (FakeDrone.status keys)
  sample: Callable[[float], AbortSample] build an AbortSample at t_s
  packs                                  PackRegistry-like
  monitor                                AbortMonitor
  tuner                                  Tuner-like
  gate                                   CodeGate-like
  flash: Callable[[], str]               build+flash, returns fw_hash
  analyze: Callable[[str], float | None] flight_id -> J (None = invalid)
  wait_for_go: Callable[[str], bool]     operator go for pack_id (Q2); False -> stop, status "operator_stop"
  resting_v: Callable[[str], float]      resting voltage of pack_id
  arm_allowed: Callable[[], bool] = lambda: False
  change_request: Callable[[], dict | None] = lambda: None   agent's justification only, or None = no change
  diff_source: Callable[[], str] | None = None   None -> default_diff_source(repo_root, lkg_commit, c_files)
  repo_root: str = "."   lkg_commit: str = "HEAD"   c_files: tuple[str, ...] = ()
  dt_s: float = 0.02   flight_timeout_s: float = 120.0   hover_s: float = 5.0   cooldown_min_s: float = 0.0

def default_diff_source(repo_root, lkg_commit, c_files) -> str:
  subprocess `git diff <lkg_commit> -- <c_files...>` in repo_root, return stdout.
def files_after_from_diff(diff_text, repo_root) -> dict[str, str]:
  paths from `+++ b/<path>` lines (skip /dev/null); read each from disk under repo_root.
  The runner NEVER takes diff text or file contents from the agent; only the justification.

@dataclass FlightRecord: flight_id, pack_id, experiment, j: float | None, abort_level: int,
  abort_reason: str, decision: str ("keep" | "revert" | ""), hover_only: bool, duration_s: float
@dataclass CampaignReport: campaign, flights: list[FlightRecord], status: str
  ("complete" | "operator_needed" | "arm_refused" | "operator_stop" | "gate_refused"), reason: str

def run_campaign(yaml_path, deps) -> CampaignReport:
  Load campaign. Queue = experiments expanded by repeats, cycled until max_flights flights.
  Packs used round-robin from campaign.packs. Per flight, in this order:
  1. wait_for_go(pack_id) (Q2). False -> status operator_stop, return.
  2. Cooldown (Q8): sleep in dt_s steps until clock() - last_landing_t >= max(last flight duration_s,
     cooldown_min_s); then packs.next_flight_allowed(pack_id, resting_v(pack_id), cooldown_s=<elapsed
     since last landing>). Not allowed -> keep sleeping, re-check (bounded; give up after
     flight_timeout_s -> status operator_needed with the reason).
  3. Optional code change: just = change_request(); if not None: diff = diff_source(); files =
     files_after_from_diff(diff, repo_root); res = gate.check_change(diff, just, files); not ok ->
     status gate_refused, reason = "; ".join(res.reasons), return. ok -> fw_hash = flash().
     If no change: fw_hash = "" unless flash already ran once (keep last hash).
  4. hover_only = gate.next_flight_must_hover().
  5. arm_allowed() False -> status arm_refused, return (no arm sent).
  6. params = tuner.propose(history); monitor.begin_flight(); client.arm(); client.takeoff();
     run the loop (below) until HOVER. If not hover_only: generate(shape, params-from-experiment,
     profile) -> upload(points, client) -> client.traj_start(); fly until traj_state DONE.
     hover_only: hold HOVER for hover_s. Every loop tick: client.heartbeat(); step(dt_s);
     decision = monitor.step(sample(t)). decision.level >= 1 -> abort branch.
  7. client.land(); loop until prim_state IDLE (or timeout); monitor.end_flight().
  Abort branch (Q12, CEO decision): level 1 in flight = client.traj_stop() then client.land()
  (firmware returns, settles, lands at origin); loop until IDLE. NEVER call client.kill().
  8. j = analyze(flight_id) if no abort else None; tuner.record(params, j, valid=j is not None).
  9. decision = gate.on_flight_result(aborted=level>=1, j=j) (level 2 = revert via aborted=True);
     gate.record_flight(flight_id, fw_hash). Append FlightRecord.
  10. Level 3 = monitor decision level 3 OR monitor.consecutive_aborts >= 2 -> status
     operator_needed, return; no further arm.
  Stop at max_flights -> status complete.
  flight_id = f"{campaign.campaign}-{n:03d}" (n from 1).
</spec>

<tests>
`ground_station/service/tests/test_runner.py`, at least 8 tests, all against FakeDrone with a fake clock
(no real sleep, no git: inject diff_source in every test except a unit test of files_after_from_diff on tmp_path).
Use fakes/spies for packs, tuner, gate, flash, analyze (record calls). Build `sample` from FakeDrone
(position, roll_deg, pitch_deg, status()["safety_trip"]). Use example_circle.yaml or a small tmp_path yaml.
  A. e2e: 3+ flights complete, J recorded for each, status "complete", tuner.record called per flight.
  B. level-1 abort (force via sample or a spy monitor returning level 1): client sends traj_stop then land;
     wrap client so kill() is spied and assert it was never called.
  C. level-2: aborted flight -> gate.on_flight_result called with aborted=True.
  D. level-3: two consecutive aborts -> status "operator_needed", no further arm after.
  E. cooldown: fake clock shows the second takeoff happens >= first flight duration after landing.
  F. arm_allowed False (default) -> status "arm_refused", no arm frame sent.
  G. after a code change (change_request returns a dict, spy gate next_flight_must_hover True) -> that flight
     is hover_only, no traj_start.
  H. gate.check_change receives exactly the string returned by diff_source and files read from disk.
Run: `python -m pytest -q -p no:cacheprovider ground_station/service/tests/test_runner.py` and the baseline
command from <context> (must still show 172 passed).
</tests>

<digest>
`.agent-ops/out/wp3-r1.md`, at most 25 lines: files changed + line counts, each command + its last 3 output
lines, deviations from this spec, open risks.
</digest>
