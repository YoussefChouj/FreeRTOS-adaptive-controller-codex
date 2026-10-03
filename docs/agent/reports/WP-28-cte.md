Status: DONE (the code is done and the tests pass; the gate fails, see below)
Commits: 4a94a20 - cte(wp28): livetune CMA-ES, window cost, supervisor, session loop, sim tests;
  c114022 - cte(wp28): livetune scenario step, runner hook, campaign, operator doc, firmware gain lease (default OFF) (on wp/28)
Gate: GATE FAIL: size,clang-tidy (scope PASS 18 files, ruff PASS, pytest PASS 67). size: 1450/200 lines; the brief's
  deliverables (4 modules + sim + tests + step + campaign + doc + firmware) cannot fit 200. clang-tidy: 2
  "undeclared PID_GainLease*" findings. These are a worktree artifact: the gate uses the main checkout's
  compile_commands.json, whose -I points at the main checkout's API/, and that pid.h has no prototypes. They are
  declared in this tree's API/pid.h.
Verification: python -m pytest -q ground_station/livetune ground_station/service/tests/test_scenario_schema.py
  ground_station/service/tests/test_livetune_step.py -> "61 passed in 10.74s"; wider set 201 passed; load_campaign OK (106 s).
Worker rounds: CTE, effort xhigh (no manager, no workers).
Deviations / open questions:
- Window is 6.5 s, not 4-6 s. sysid.c:16-17 fixes ramp 1.5 s x2 + run >= 1 s, and settle 2.5 s must cover the 2.0 s
  SysID RECOVERY (SysID_Start silently refuses unless IDLE). A 70 s budget gives ~10 windows: 2 baselines + ~8 candidates,
  about 1.2 generations (lambda 7). Several flights are needed per tune; the result carries es_state for resume
  (resume is not wired yet).
- Excitation: CMD 0x14 log chirp, not the multisine. Finding: sysid.c:209 normalises the multisine peak over
  min(dur, 8) s, but the signal plays for dur + 3 s. The WP-28 sim (same code path) measured peak rate setpoint ~91
  deg/s at amp 30. PROPOSED fix: scan dur + 2*RAMP.
- Origin reset (CMD 0x14 idx 6): chosen fix is "start from a hold". Each start waits <= 8 cm from the hover point; the
  offsets go into a walk sum; walk + position is checked against the fence box; the run ends at 0.4 m walk.
  Lighter path, PROPOSED: a 0x14 start that skips the reset. It needs firmware_contract.py, which is outside the allow list.
- Gain lease: firmware had no revert on link loss (0x01 handler send_data.c:1514-1532 writes and forgets). Implemented
  GAIN_LEASE_ROW(enable 0, 2000 ms) in API/pid.c (+4 lines pid.h, +5 send_data.c). It snapshots at the first in-flight
  0x01 write and restores when no 0x01 write arrives for lease_ms or when the drone leaves the air. The loop re-sends
  Kp every 1 s as the keep-alive. NOT built: Keil is forbidden, and the direct gcc call was not permitted. The
  _ccore golden build already skips on main code (uint8_t in TrajFF). CEO build check needed.
- Ki writes are not bumpless (Ui = Ki*SumE, pid.c:120 jumps on a Ki change). PROPOSED: rescale SumE in the 0x01 handler.
- Risk: the GS cannot see a SysID start refusal (s_state is not streamed). A refused window would score as no
  excitation, so its J looks falsely good. PROPOSED: stream SysID state. Also check in flight 1 that the subscribe
  slots keep streaming during a SysID run.
- Sim (tests/sim_drone.py, SysID rate plants + pid.c rows), from detuned Kp 3 / Kd 6: J_rel best 0.89 after 44
  candidates (300 s sim budget). Measured in sim only, not in flight. Trips, link loss, takeover and walk are tested.
- Unrelated: test_workflow_b_e2e::test_go_allow_agent_arm_false times out with status "running", before the
  arm check (campaign_api path, not touched here). I could not run it on main because checkout/archive were not permitted.
- CMA-ES vs extremum seeking (unverified, nothing fetched): ES needs time-scale separation, a dither period well
  above the closed-loop settle, and an averaging filter over many periods per gradient step. That is roughly 10-30 s
  per useful step for 3 gains, too slow for ~70 s/flight, and the dither keeps the loop perturbed the whole time.
  CMA-ES fits our windowed safety model (per-candidate revert, infeasible marking) and state resumes across flights.
  Recommendation: CMA-ES now. Consider ES only for slow drift tracking of a single gain after convergence.
