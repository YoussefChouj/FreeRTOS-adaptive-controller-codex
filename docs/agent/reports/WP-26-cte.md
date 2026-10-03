Status: DONE
Commits: 614faee cte(wp26) autotune package, excite step, autotune campaigns, operator doc; + this report (on wp/26)
Gate: GATE PASS (--base main --max-lines 3000, size 1600/3000; scope 19 files, ruff 15 clean, pytest 76 passed;
  clang-tidy skipped, no C). Default --max-lines 200 cannot hold briefs A-D (same as WP-10).
Verification (laptop, this run):
- pytest -q ground_station/autotune + service scenario_schema, excite_step (new), fly_scenario, runner, fake_drone,
  campaign_schema/live/outputs, wfb_e2e -> 234 passed. FAILS: test_workflow_b_e2e::test_go_allow_agent_arm_false (stays
  "running"); its campaign has no abort/excite, so likely pre-existing, NOT proven (main-baseline run was denied).
- python -m ground_station.autotune.synth <dir>; python -m ground_station.autotune.cli <dir> --axis roll -> prints the proposal:
  synthetic plant k 8.5 / tau 30 ms / delay 10 ms fitted as 7.77 / 33.0 / 13.1 ms (residual 0.06). Proposes gyroxPID.Kp 5 -> 4,
  Kd 10 -> 12.5 (cmd 0x01 idx 9/11), PM 39.0 -> 50.6 deg. Writes autotune_roll_rate.json.
- load_campaign accepts autotune_rate_roll / _pitch / _verify (budget 85 / 85 / 95.4 s of 120).
Worker rounds: CTE, effort xhigh (no manager or worker rounds).
What was built:
- ground_station/autotune: excitation.py (mirror of API/sysid.c: CMD 0x14 map, clamps, rebuilt dither); frf.py (IV FRF
  T = Phi_xd/Phi_rd, P = T/(C(1-T)), coherence, band coverage, fit of fit_rate_plant's model on the complex FRF);
  design.py (firmware C(z), margins, step ITAE with pid.c clamps, +-30 % step box, string refusals); cli.py (propose, --loop
  angle, --verify keep/revert); synth.py (synthetic flights for the tests and the demo).
- Scenario step excite {axis, signal, f0, f1, amp, duration_s}: it must follow a hold, needs z >= 0.4 m, step time = duration + 5 s.
  takeoff takes an optional mrac_injection (CMD 0x0F idx 10, sent on the ground). The runner waits up to 5 s for prim HOVER
  within 0.15 m of the hover point, sends idx 0-5, 7, 6, waits, sends abort (a no-op if the run is done), waits RECOVERY, and sends
  abort on any flight abort. FakeDrone handles 0x0F and 0x14.
- run_campaign now gives a non-empty campaign `abort:` map to the monitor. Before this it was never applied live, so tilt_deg 15
  had no effect. hover_ladder and example_circle use abort {}, so they are unchanged.
Design decisions:
- The plant comes from Des/FB plus the known C, not Phi_xd/Phi_ud. The core streams carry no gyro?PID.U, and u_nom needs
  mrac_to_mixer, which is 286 or 1170 depending on the build. The two estimates are the same when u = C(r - x).
- Only rate Kp and Kd change. They are the only pid.yaml knobs, so apply_params can write them; Ki is not a knob.
- Design target PM >= 50 (45 + 5 buffer). Without the buffer, a verify flight failed: k error ~9 % gave re-measured PM 43.5.
  A noise-gain cap |C(Nyquist)| <= 1.5x current, because D has no filter. Both are PROPOSED.
Firmware: none needed. MRAC injection is CMD 0x0F idx 10 (send_data.c:1779). SysID_Start turns id_frame_on on itself
  (sysid.c:221).
Risks to flag:
- The start re-zeroes the OF origin and loc setpoints (send_data.c:1848-1865), so the fence shifts by the drift at start.
  Only the 0.15 m pre-check and the hold before it bound this.
- The 0x03 frame cannot go in a log_plan (groups are subscribe symbols only), and wifi_bridge.py:1918-1943 decodes it with an
  old layout. So the cli uses the 100 Hz core streams plus a rebuilt dither; it uses id.* only from serial-bridge logs.
- A CMD 0x14 start reads "applied" even if SysID_Start refuses it; the cli then says "no multisine found". MRAC injection
  stays off after these campaigns (re-enable it). Hover-RMS verify is noisy (6 s window +-10 % by seed), hence 15 s holds.
- All amplitudes, bands, thresholds and tolerances are PROPOSED. None were measured on the airframe.
