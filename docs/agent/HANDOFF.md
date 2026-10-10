# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-10 — lab day, new image built, not flashed or flown

Branch `overnight-2026-10-08`. Lab guide: `docs/lab/today-2026-10-10.md` (section 5 = staged matrix).
New in the image (build 0/0): fixed start (`traj_go` flies to (0, 0, 0.85 m), settles 2 s, then the preset),
`f8_laps` up to 4, land-gate saturation log-only (D1), `kp_id` boot 7 (D2).

Matrix (operator answers a-f, 2026-10-10): stages 1/2/3 at laps 2/3/4, `move_s` 20 (69 / about 104 / about
138 cm/s, last two computed). Stage 4 = PID + best 5 with 0.2 kg hanging under one motor. Stage 5 = best 1 with
0.2 kg asymmetric + 0.4 kg near-symmetric, both hanging. Sling length measured in the lab per flight.
Rank by position error vs the planned path (50 Hz).

## Next 3 actions
1. Operator at the lab: `python -m ground_station.flashtool.rebuild_and_flash --yes`, check `kp_active == 7`.
2. **6-axis MRAC** (operator: "i want all 6 axes to adaptaion layers"). Wiring 7d83471, replay d8bd1a4
   (`docs/analysis/mrac-xy-replay-2026-10-10.md`), x/y ref model 75710e9 (ref_type 1, bw 2 measured, operator OK):
   45 logs 0 freeze, 0 clamp, max u_ad 0.80. Off at boot, built (Keil 0/0), NOT flashed, unflown.
   Telemetry dd7b22f: g_tlm.mrac arrays [6]. After the flash: regenerate capability_manifest.json.
3. Logging option f PROPOSED (`docs/lab/logging-f-proposal-2026-10-10.md`): EKF-OF innovations + bias states at 50 Hz,
   reject counters + vel variance + estimator flags at 25 Hz; +3400 B/s, all families fit (max 73050/87552).
   Operator confirms list and rates, THEN edit vp15-24 presets. Nothing recorded yet.
   Note: build-only runs restore the 11:04 board ELF (artifact_custody), so new symbols resolve only after the flash.

## Do not
- Arm/spin outside an operator-opened workflow-B battery session; flash while armed or with `--force`.
- Stage OBJ/ or USER/; `git add -A`.
- Start recording until the operator confirms preset, variable list and session name.
- Write unmeasured numbers without PROPOSED/computed.
