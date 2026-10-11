# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-11 - OF/EKF drift fix in code, not built in Keil, not flashed or flown

Branch `overnight-2026-10-08`. Result doc: `docs/video-truth/r3_drift_truth_2026-10-11.md` (plot, scores, R4 plan).
R3 (10-11) camera truth showed the EKF error is born in fast RC moves: the raw-flow anchor (of1) is gated out
at 5 sigma and never heals. Fix fb615c7: `of1_gate` 5 -> 10. Replay vs camera (computed): rms from 72 s
48.9 -> 26.3 cm, revisit p90 55.5 -> 38.5 cm. Hover drift unchanged at 0.44 cm/s (needs an absolute reference
or a trim confirmed by R4).

R3 follow-ups (computed, `r3_learned_velocity_2026-10-11.md`): NN/ridge, flow scale, bias freeze, OF delay: no gain.
Pure yaw moves the drone (63/35 cm, half from OF lever arm). Only camera fusion meets 0.17 cm/s.
Fusion replay (651531d, computed, design doc `live_camera_fusion_design_2026-10-11.md`): EKF-pos update at 1 s cuts
hover rms 10.9->4.7 cm, moves 31->16.5 cm; hold-out learns no lasting trim, so the camera must stay in the loop.
The 0.3 s lag in moves is a camera/telemetry sync offset (~0.25 s, 4882068); with it fixed, moves rms is 11.7 cm.
R4 must time-stamp one sharp event (tap or LED) on both camera and telemetry.
EXT_POS shadow mode (6e3b00b): CMD 0x2C uplinks a camera fix; firmware logs the innovation `g_ext_pos.*` only, no
state write, off at boot. Encoder `ground_station/comm/ext_pos_encoder.py`.
Live tracker dry run (985dadb, a6d319e): R3 red-ring mean-shift run online, matches offline R3 at 0.7 px median
(measured, consistency only); height = telemetry Z + ring offset. Sends nothing yet. Latency, link delay and ring
offset UNMEASURED (R4).

## Next 3 actions
1. Lab, drone disarmed, uVision closed: `python -m ground_station.flashtool.rebuild_and_flash --yes`.
   The image carries fb615c7, EXT_POS shadow and the 10-10 items. gcc tests pass. Keil not run yet (UV4 open).
   Then re-run `test_command_symbol_map` (fails until g_ext_pos_* are in the axf).
   After it: check `kp_active` 7, `g_ctrl_axis_mask` 63, `lab_stage_active` 1 (run sheet `docs/lab/run-sheet-2026-10-10.md`).
2. R4 flight (doc "R4 flight plan"): camera records the whole battery, 90 s hover, 4 x (1 m out, back, 15 s hold),
   top marker id1, yaw spins both ways at 2 rates, LANDING switch, tape touchdown. Success = rms, p90 -30% vs R3.
3. Still pending from 10-10: 6-axis MRAC (off at boot, `docs/analysis/mrac-xy-replay-2026-10-10.md`), lab matrix
   `docs/lab/today-2026-10-10.md`, logging option f. After the flash: regenerate capability_manifest.json.

## Do not
- Arm/spin outside an operator-opened workflow-B battery session; flash while armed or with `--force`.
- Stage OBJ/ or USER/; `git add -A`.
- Start recording until the operator confirms preset, variable list and session name.
- Write unmeasured numbers without PROPOSED/computed.
