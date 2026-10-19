# HANDOFF - current state (overwrite at every task boundary, keep under 3 KB)

Read order: `AGENTS.md` -> this file -> `docs/agent/memory/rules.md` -> `.claude_state.md` (newest entry first).
History: `docs/agent/ledger/` (grep, never read whole). Desktop session is CEO: delegates implementation to Jules workers.

## Now: 2026-10-19 Lab Analysis Complete -> Prepare `.\labday 2026-10-20` (`docs/lab/days/2026-10-20.yaml`)

- **Cloud branches merged & pushed (`93d2e7fd` on `overnight-2026-10-08` and `main`):** all 2026-10-19 cloud branches (`elated-cartwright`, `loving-mccarthy`, `compassionate-rubin`, `kind-goldberg`, `clever-darwin`, `angry-saha`, `dazzling-cori`, plus cherry-picked `mrac` shift, `fw_trace`, `write_gains` `/api/actions`, `video_truth` ArUco, `drift_nn`) merged and verified (`tools/check.sh --fast` + 243 pytest pass).
- **2026-10-19 Flight Results (`docs/lab/days/2026-10-19-report.md`, `logs/analysis/2026-10-19/memory_premise.json`):**
  - **Camera-vs-EKF bias fix (`52784922`, `floor-1019`) verified:** `cam-EKF rms` is **2.8-4.6 cm** across all 7 airborne flights (down from ~30 cm on 10-18); camera vs onboard landing offsets agree within **0-2 cm** (`mem2` 26/27, `mem3` 38/37, `mem4` 39/41, `mem5` 30/30, `mem6` 34/34, `z1` 17/17, `z2` 25/26 cm).
  - **Hover RMS (`u100` vs `u0`):** `u100` (`mem3` 8.2/9.0, `mem5` 7.9/8.4 cm) beat `u0` (`mem2` 9.4/9.9, `mem4` 8.1/8.9, `mem6` 10.0/10.3 cm) by ~1.1 cm in hold RMS; during ~10 s of figure-8 before abort, `u100` MRAC weights drifted 9.1% (`mem3`) and 11.5% (`mem5`).
  - **Why flights aborted on 10-19 & Root Causes Fixed (4 Jules briefs in `.agent-ops/briefs/j-1019-*.md`):**
    1. **Obs 1 (Tracker corner latch at start + jump line on arm):** `TrackCoral` left `self.static = None` for the first 30 frames (`static_n`) even when `floor_scenery` was loaded, and `Preview.uvs` never cleared after `static_n` or on arming (`live_tracker.py:L117-L200, L804`). Brief: `j-1019-tracker-center`.
    2. **Obs 2 (`1017-F1` 12-gain write abort):** `/api/diagnostics/bundle` capped command results at 10 (evicting 2 of 12 gains); fixed via `/api/actions` in `faddfc85` + `wait_txn` grace deadline fix (`campaign_fly.py:L901`). Brief: `j-1019-exp-gains`.
    3. **Obs 3 (`1017-E1..E4` `exp_active` 1-flight lag `255->4->5->6`):** `Keil_OnGround()` (`TASK/StabilizerTask.c:L1373`) required `state != FLIGHT_STATE_ARMED`, so `set exp_id` after RC arm on the pad never latched into `g_exp_active` until disarm after abort. Fixed in `StabilizerTask.c` (`flight_phase == FLIGHT_PHASE_IDLE`) AND pre-applied before `wait_for_arm` in `campaign_fly.py`. Brief: `j-1019-exp-gains`.
    4. **Host `ingest_lag_s` spikes (0.52-1.34 s in `z1`/`mem1`):** synchronous SQLite commit/prune under `store._lock` on the real-time ingest thread (`storage.py:L124-L171`). Offloaded to background writer + WAL. Brief: `j-1019-ingest-lag`.
    5. **`mem2..mem6` & `z2` `position_error` at figure-8 first turn (~45 s):** 60 cm Gerono figure-8 (`v_pk ~38 cm/s`) has 36.4-36.9 cm 3D phase lag (`z2` 29 cm) vs `AbortLimits.pos_err_m = 0.25` m (`abort_monitor.py:L18`, PROPOSED; `.yaml` campaigns use `0.50` m). Needs operator OK to raise `pos_err_m` (or add `pos_err_traj_m = 0.50`) for dynamic trajectories.
    6. **`labday` `bad_end` & `scorecard` carried IDs:** `bad_end` now checks `aborted` / `debrief.txt` (`j-1019-labday-scorecard`).

## Next 3 actions
1. Review and merge the 4 Jules worker PRs (`j-1019-exp-gains`, `j-1019-ingest-lag`, `j-1019-tracker-center`, `j-1019-labday-scorecard`), run `tools/check.sh --fast` + pytest, push `overnight-2026-10-08` and `main`.
2. Operator decision before flying `.\labday 2026-10-20`: approve raising `AbortLimits.pos_err_m` (e.g. `0.50` m during trajectory/preset, matching `campaigns/*.yaml`) so 60 cm figure-8 (`36.9 cm` lag) and `vz_steps` (`29 cm` lag) complete without false `position_error` aborts.
3. At lab on 2026-10-20: rebuild & flash if firmware changed, run `.\cam floor floor-1020`, then `.\labday 2026-10-20` (21 flights: `mem1..mem6`, `z1..z2`, `1017-F1`, `1017-E1..E4`, `1017-H0..H2`, `1017-F2..F6`).
