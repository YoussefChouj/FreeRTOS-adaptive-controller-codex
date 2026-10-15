# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: inline, no subagents.

## Now: 2026-10-15 evening lab ended & synced to `origin` (`main` and `overnight-2026-10-08`)

Branches `overnight-2026-10-08` and `main` are synced and pushed to `origin` (`9393c5e7` + handoff).
- `7c9f9b4a`: `LiveWfbClient` `CMD_TRAJ` (`0x1B`) waits `TRAJ_ACK_TIMEOUT_S = 3.0` s (PROPOSED) for late firmware results; 2 host tests in `ground_station/service/tests/test_campaign_live.py`.
- `2f434380`: 2026-10-15 evening lab record in `docs/lab/lab-session-2026-10-15.md` (read it first; next plan: `docs/lab/run-sheet-2026-10-16.md`).
- `9393c5e7`: synced local MRAC replay/variant/contract updates (`ground_station/analysis/mrac_log_replay_host.c` 4-axis mask, `ground_station/analysis/mrac_variants.py` `basis` `0..11`, `ground_station/platform/firmware_contract.py` `CMD 0x1A` field `0..25`), `sim/bench/` Yucelen P2 scripts, `docs/design/vp-maskable-basis.md`, `docs/research/vp16/briefs/`, `docs/agent/briefs/`, `docs/learning/gap-ledger.md`, and `docs/flights/ledger.csv`.
- Lab status: F1 attempt 1 aborted L1 `position_error` at takeoff (26.5 cm drift, camera agrees); F1 attempt 2 aborted before liftoff on late trajectory `BEGIN` ack (addressed by `7c9f9b4a`). F2-F6 not flown. Firmware is the 10-14 build (unchanged, no flash).
- Uncommitted local working-tree items intentionally left unstaged: Keil IDE/build files (`USER/JX_FLY.uvguix.Acer`, `USER/JX_FLY.uvoptx`, `USER/build_log.txt`) and local `.agent-ops/` scratch files.

## Next 3 actions
1. Cloud/local worker handoff (base on `origin/overnight-2026-10-08` or `origin/main`):
   - Replay F1 attempt-1 takeoff drift (`docs/lab/lab-session-2026-10-15.md` fix 3: C3 outer Kp 0.32 vs 0.25 m takeoff abort threshold).
   - Implement parametric goto trajectories (`docs/lab/lab-session-2026-10-15.md` fix 5: send goto target `x/y/z + move_s` parameters instead of ~135 single-float `APPEND`s; requires firmware build + flash).
2. Lab: power-cycle, re-write C3 stop-and-wait (operator chat approval), fusion 3, `.\fly lab_c_robust --pack 1 --once`.
3. Then F2-F6 per `docs/lab/run-sheet-2026-10-16.md` (livetune rate/pos with fusion 2, ES gains chat-approved, LQR, MPC).

## Do not
- Arm/spin outside an operator-opened workflow-B battery session; flash while armed or with `--force`.
- Stage OBJ/ or USER/ or logs/; `git add -A`.
- Start recording until the operator confirms preset, variable list and session name.
- Write unmeasured numbers without PROPOSED/computed.
- Run `lab end` on test or rehearsal state against the real catalog (it rewrites `docs/lab/catalog.yaml`).

<!-- AUTO:BEGIN -->
Refreshed: 2026-10-15 22:45 (mechanical, no LLM)
- Tree: .; branch / HEAD: overnight-2026-10-08 @ 9393c5e7; unpushed commits: 0
- Dirty paths outside OBJ/: 97
  -  M .agent-ops/.ark-last-spawn
  -  M .agent-ops/served/estimator-panel.js
  -  M .claude_state.md
  -  M USER/JX_FLY.uvguix.Acer
  -  M USER/JX_FLY.uvoptx
  -  M USER/build_log.txt
  -  M USER/main.c
  -  M docs/agent/HANDOFF.md
  - ... +89 more
- Dashboard 8081: UP (HTTP 200)
- Last commits:
  - 9393c5e7 chore: sync local MRAC replay/variant updates, flight ledger, sim bench, and research docs
  - 2f434380 docs: 2026-10-15 record + HANDOFF: upload fix 7c9f9b4a done, cmd 26 idx 2 = heartbeat (correction), fix 5 parametric trajectories (operator idea), drift replay still open
  - 7c9f9b4a campaign_live: trajectory upload commands (CMD_TRAJ 0x1B) wait TRAJ_ACK_TIMEOUT_S 3.0 s (PROPOSED) for the firmware result, others keep 1.0 s; 2026-10-15 re-fly BEGIN applied 1.55/1.70 s late -> host said 'begin rejected' and aborted on the pad. Late results still land in _txn_results after core expire_commands (verified in code). Cmd 26 idx 2 x445 = the 5 Hz PRIM heartbeat, required, not a rogue sender. 2 tests (late traj accepted / takeoff still 1.0 s; full upload with 1.6 s late replies ok in 1 attempt)
  - b802f4e0 lab 2026-10-15: session record (F1 C3 2 attempts: position-error abort at takeoff; re-fly refused before liftoff, trajectory BEGIN late ack -> begin rejected; F2-F6 not flown), fix list + next lab plan; HANDOFF current state
  - ad83fa79 livetune: loop=pos tunes the xy cascade in flight (locx/locy + locxs/locys Kp, Kd; idx 24-35; POS_EXCITE 0.3-2 Hz chirp as disturbance, pos error score, popsize 4, trust 0.6, all PROPOSED); lab_d_livetune_rate/pos fly scripts; lab_c_autotune flies the ES best gains; run sheet 2026-10-16 = 4-way C3 PID / ES PID / LQR / MPC (6 flights); 4 tests
<!-- AUTO:END -->
