# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-14 afternoon - xy tune, ADRC/LQR/MPC, camera-gated landing built; nothing flashed or flown

Branch `overnight-2026-10-08`, local commits, UNVERIFIED on the drone. Drone: ELF e4dd3652. Built, NOT flashed: WP-44
VAR_WRITE (41013a0e), WP-45 (03716d5c), CMD 0x01 axes 7-11 (6aa642fc), xy_ctrl (bff1d29f: VAR 44 `g_xy_ctrl_kind`
0 PID/1 ADRC/2 LQR/3 MPC, latched on the ground; VAR 45 `g_traj_ff_shift_s` 0-0.6 s), camera-gated descent
(a14f67e7: VAR 46 `g_land_xy_gate_cm` 0-50, 0 = off). Keil build 0 err/0 warn.
- Sim (b922a6a9, `analysis.xy_tune` / `xy_ctrl`), replay `logs/xy_tune/20261014-replay*/`: 213613 x overshoot (log
  11.4 cm, mean of 3 plants) current 11.6, tuned_shift 1.7, LQR 4.2, MPC 4.6, ADRC 9.9. "tuned" (no shift) 41-102 cm
  on r7 -> never. Sim faithful only on 213613 (r7/r6 are RC-velocity flights).
- -x landing drift: EKF descent dx +2..+6 cm (3/29 sessions) -> OF estimate drift, only the camera sees it. Plan:
  `docs/flights/2026-10-14-camera-landing-plan.md`. Fw land + fusion never flown; pad landing NOT proven.

## Next 3 actions
1. Stop 8081, `rebuild_and_flash --yes` (all of the above). Ground, disarmed: VAR_WRITE round trip (44/45/46), 0x01
   axis 8 write + read-back, `camera_truth.sh check` ready 1920x1080.
2. Phone pose + floor marks; `fusion r8_f1 2 --yaw-deg 94`, `fly preset_tour` baseline; read the camera landing line.
3. One change per flight, operator picks (PROPOSED): (a) tuned_shift: 0x01 locx/locy .67/.0014/4.93, locxs/locys
   4.70/.0031/0 + VAR 45 0.31; (b) LQR VAR 44 = 2; (c) VAR 46 = 10 with fusion 2, `fly`-landed. Never "tuned"/ADRC;
   MPC (VAR 44 = 3) last, optional.

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
