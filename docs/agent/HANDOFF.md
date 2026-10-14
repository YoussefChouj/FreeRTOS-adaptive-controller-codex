# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-14 pre-lab - `fly`, /lab skill, position-gain writes built; nothing flashed or flown

Branch `overnight-2026-10-08`, local commits, UNVERIFIED on the drone. Drone: ELF e4dd3652 (10-13 15:10, has 3492cbdb +
the 5f30b9b8 mode 3 fix). Built, NOT flashed: WP-44 VAR_WRITE (41013a0e), WP-45 preset from a WiFi hover (03716d5c),
CMD 0x01 axes 7-11 = Z_pos/locx/locy/locxs/locys (6aa642fc, not Keil-built).
- `fly` (03ddcf5b..bb41660e): arm = go, keys s/n/l/p, `.fly` scripts, 5-line debrief + a camera landing line while
  fusion runs (71f364cf). Lab playbook: `/lab` skill (bring-up, fly, debrief, gains, compaction between flights).
- Fusion alongside 8081/`fly` (884c79d3, UNVERIFIED): `camera_truth.sh fusion <name> 2 --yaw-deg 94`.
- Measured: wobble = position loops (overshoot 6-16 cm, settle 1.6-2.7 s or never, period 2.6-3.5 s; flight_debrief
  "Setpoint moves"). Mode 2 r7_f3 drift 7-23 cm per 14-35 s window, landed 35 cm from the pad (camera). Pad landing
  NOT proven.

## Next 3 actions
1. Stop 8081, `rebuild_and_flash --yes` (WP-44/45 + 6aa642fc). Ground, disarmed: VAR_WRITE round trip, 0x01 axis 8
   write + read-back, `camera_truth.sh check` ready 1920x1080, VOFA+ files, `p` opens PJ.
2. Re-check the phone pose and floor marks; `fusion r8_f1 2 --yaw-deg 94`, then `fly preset_tour`; read the camera
   landing line.
3. Wobble A/B, one change per flight (PROPOSED): baseline, locx Kd 4->6, locxs Kp 3->4, or `program profile=scurve`.
   LINK_LOST_S 3 s is PROPOSED.

## Do not
- Arm/spin outside an operator-opened workflow-B battery session; flash while armed or with `--force`.
- Stage OBJ/ or USER/ or logs/; `git add -A`.
- Start recording until the operator confirms preset, variable list and session name.
- Write unmeasured numbers without PROPOSED/computed.

<!-- AUTO:BEGIN -->
Refreshed: 2026-10-13 09:50 (mechanical, no LLM)
- Tree: .; branch / HEAD: overnight-2026-10-08 @ e4c275c7; unpushed commits: 183
- Dirty paths outside OBJ/: 458
  -  M .agent-ops/.ark-last-spawn
  -  M .agent-ops/served/estimator-panel.js
  -  M .claude_state.md
  -  M USER/JX_FLY.uvguix.Acer
  -  M USER/JX_FLY.uvoptx
  -  M USER/main.c
  -  M docs/agent/ledger/state-2026-10-05-improvements.md
  -  M docs/flights/ledger.csv
  - ... +450 more
- Dashboard 8081: UP (HTTP 200)
- Last commits:
  - e4c275c7 agent: HANDOFF refresh, nightly 2026-10-13 closed (F3B merged not flashed, next: lab per run sheet)
  - fb228975 nightly: row 7 F3B done (merged, not flown), item 12 drift summary; run sheet: flash 3492cbdb before mode 3
  - 3492cbdb F3B: mode 3 q_pos 1e-6 -> 0.05 (worker 4690fa39, selective), pinned by a host test
  - fb54c546 nightly: item 2 mode-1-only NN variant, no held-out gain over baseline
  - 89a4814d live_tracker: post-lock yaw refit monitor (nightly item 6, not applied), fit --auto prints it; nightly rows 6, 9, 11 table fix
<!-- AUTO:END -->
