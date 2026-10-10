# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-11 - OF/EKF drift fix in code, not built in Keil, not flashed or flown

Branch `overnight-2026-10-08`. Result doc: `docs/video-truth/r3_drift_truth_2026-10-11.md` (plot, scores, R4 plan).
R3 (10-11) camera truth showed the EKF error is born in fast RC moves: the raw-flow anchor (of1) is gated out
at 5 sigma and never heals. Fix fb615c7: `of1_gate` 5 -> 10. Replay vs camera (computed): rms from 72 s
48.9 -> 26.3 cm, revisit p90 55.5 -> 38.5 cm. Hover drift unchanged at 0.44 cm/s (needs an absolute reference
or a trim confirmed by R4). Gyro compensation of of1 and Q/R variants did not beat the gate alone.

## Next 3 actions
1. Lab, drone disarmed, uVision closed: `python -m ground_station.flashtool.rebuild_and_flash --yes`.
   The image carries fb615c7 plus the 10-10 items below. gcc golden tests pass. Keil not run yet (UV4 open).
   After it: check `kp_active` 7, `g_ctrl_axis_mask` 63, `lab_stage_active` 1 (run sheet `docs/lab/run-sheet-2026-10-10.md`).
2. R4 flight (doc "R4 flight plan"): camera records the whole battery, 90 s hover, 4 x (1 m out, back, 15 s hold),
   land with the LANDING switch, tape the touchdown. Success = rms and revisit p90 at least 30% below R3 logged.
3. Still pending from 10-10: 6-axis MRAC (off at boot, `docs/analysis/mrac-xy-replay-2026-10-10.md`), lab matrix
   `docs/lab/today-2026-10-10.md`, logging option f. After the flash: regenerate capability_manifest.json.

## Do not
- Arm/spin outside an operator-opened workflow-B battery session; flash while armed or with `--force`.
- Stage OBJ/ or USER/; `git add -A`.
- Start recording until the operator confirms preset, variable list and session name.
- Write unmeasured numbers without PROPOSED/computed.
