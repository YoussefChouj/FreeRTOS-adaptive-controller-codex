# Handoff — stream `main`

- **Updated:** 2026-10-20 01:50 +08:00
- **Branch / Base:** `claude/labday-1019-fixes` (2026-10-19 cloud session, from `overnight-2026-10-08` at `90934b60`, which is mirrored to `main`). Not merged: the CEO reviews and merges.
- **Goal:** Complete all 2026-10-19 post-flight analyses and land/verify all fixes for the 2026-10-20 lab session (`docs/lab/days/2026-10-20.yaml`, 21 flights).

## Completed in 2026-10-19 Session
1. **Merged & Pushed All Cloud Agent Branches (`d4898337`):**
   - Merged `elated-cartwright-4fa5e7`, `loving-mccarthy-wr7naa`, `compassionate-rubin-xojtv2`, `kind-goldberg-o9rfdm`, `clever-darwin-8drxdj`, `angry-saha-12497c`, `dazzling-cori-oh88qd` + cherry-picked unique commits (`faddfc85` `/api/actions`, `32cb52af`, `89ec0428`, `00f9bff1`, `1188eccd`, `9863cb3e`, `66ed79b8`, `33ce1003`, `34ed57a9`, `058d05a5`).
2. **2026-10-19 Flight Logs & Initial Analysis Committed to Git:**
   - Force-added the lightweight 2026-10-19 CSV/JSON/NPZ logs (`logs/flights/2026-10-19/`, `logs/analysis/2026-10-19/memory_premise.json`, `logs/lab/2026-10-19/labday_state.json`, `logs/video_truth/floor-1019/aruco.npz`, `logs/video_truth/*-lab-*/fixes.csv`) so cloud agents have full access to today's flight telemetry and camera truth.
   - Generated `docs/lab/days/2026-10-19-report.md` and `docs/lab/days/2026-10-19-report.json`.
   - Created tomorrow's campaign deck `docs/lab/days/2026-10-20.yaml` (21 flights).

## Completed in 2026-10-19 Cloud Session (`claude/labday-1019-fixes`)
All built inline. Jules is unreachable from the cloud and Claude subagents are revoked. Each commit passed `bash tools/check.sh --fast` plus its suites. Nothing was flashed or flown.

| Task | Commits | Status |
|---|---|---|
| `j-1019-labday-scorecard` | `08cac16` | DONE (host) |
| `j-1019-tracker-center` | `35653d1` | DONE (host) |
| opencv pin | `a78e43a` | DONE (`requirements.txt`: `opencv-python<5`; 5.0 dropped `cv2.undistortPointsIter`) |
| `j-1019-exp-gains` | `cd8326d` | Host DONE; firmware PROPOSED (not flashed) |
| `j-1019-ingest-lag` | `cb35c0a` | DONE (host) |
| Abort limit `pos_err_m` 0.50 | `973b73c` | DONE (host) |
| Figure-8 VFF on GS paths + analysis | `a5ffa3c` | Firmware PROPOSED |
| Landing hold band + SETTLE aim | `868f4ba` | Firmware PROPOSED |
| `vp_table_host.c` `Keil_PreTakeoff` stub | `c05c7a3` | DONE (fixes the `test_vp_table_host` link error from `cd8326d`) |
| Drift NN target leak | `a1561d0` | DONE offline; 10-19 evaluation blocked (item 3) |

- **10-19 report NOT regenerated.** The cloud has no `logs/sessions/*` telemetry. Running the scorecard here drops the config, hold, rms, ingest and V columns, so the committed `docs/lab/days/2026-10-19-report.md` / `.json` are left as they were. On the lab PC, run `python -m ground_station.analysis.scorecard 2026-10-19`. That run picks up the carried `1017-E1..E4` cards and the preset figure-8 TRAJ scoring from `08cac16`.
- **Flash before 10-20.** Build and flash `cd8326d` + `a5ffa3c` + `868f4ba` together. `campaign_fly.preapply_exp_id` works on today's firmware; the exp latch needs the new one. Then fly:
  - the figure-8 A/B (item 1);
  - the landing A/B (item 2);
  - one exp row and check that `exp_active` matches the flight.
- **Pre-existing test failures.** The full `python -m pytest` on this branch gives 49 failed + 1 error, 2979 passed. Every one of them also fails on base `90934b60`, which gives 50 failed + 1 error. The extra one on base is `ground_station/comm/tests/test_mavlink_limit.py::test_downlink_sim`, timing under load; it passed on the branch. They are not regressions from this branch. The known causes are the cloud environment: no torch, no Keil/ARM toolchain, no `-m32`. By file:
  - analysis: `test_flight_debrief` 1 (torch), `test_mrac_log_replay` 1, `test_video_truth_plumb` 1 (`fixes_clock`);
  - comm: `test_telemetry_harness` 1 error, `test_panel_revival` 1;
  - flashtool: `test_compile_commands` 3, `test_rebuild_and_flash` 4;
  - livewatch: `test_campaign_capture` 3 (`_guard_bps` 23400 vs 46800), `test_manifest` 1, `test_stream_log` 1;
  - platform: `test_capability_manifest` 2, `test_firmware_command_table` 5;
  - service: `test_agent_mcp` 1, `test_api_symbols` 4, `test_campaign_api` 1, `test_campaign_outputs` 1, `test_streams` 1, `test_t5` 4, `test_terminal` 1, `test_workflow_b_e2e` 4;
  - sim: `bench/test_ablate` 2, `bench/test_demo_loads` 1, `sil/test_sil` 1;
  - `tests/test_nn_io.py` 3 and `tests/test_nn_replay.py` 2 (torch).

  Outside pytest, `tools/host_tests.py` `subscribe_8_slots` (`-m32`) also fails on base.

## Root Causes Diagnosed (Ready to Implement / Verify)
1. **Why Figure-8 Tracking Error Peaked (~36.5 cm) Near `y = -60 cm` & Tripped `L1 position_error` (Operator Approved Fix):**
   - **CORRECTED (2026-10-19 cloud session, `docs/lab/figure8-tracking-analysis-2026-10-19.md`):** not v/Kp lag.
     - `mem2`..`mem6` flew the Keil `preset figure8`, whose VFF (`g_traj_vff_x/y`) was active and was added once (`g_xy_ctrl_kind=0`).
     - The reference fits move_s 18 exactly. Its peak speed was 66 cm/s; without FF it would have lagged by 82 cm.
     - The error is a growing overshoot at each x extreme: +5..17, -17..-25, +20..+50 cm. It peaks at θ≈270° on the y=-60 lobe. It is the same in the u0 and u100 arms.
     - A VFF+P model with a first-order inner loop explains 12-15 cm of it.
     - The rest is open: either inner-loop phase lag, or EKF fusion of the frozen camera fixes if `g_ext_pos_on>=2`, which is unknown from the cloud copies of the logs.
     - The GS `circle`/`sinusoid`/`figure8_path` commands had no VFF. They now get it through `Traj_Commit` + `Traj_FFUpdate` in `TASK/AutoflyTask.c`.
     - Recommended: an A/B figure-8 arm with the `ground_station/service/campaigns/lab_c_tuned.fly` gains (shift 0.31). Log `velocity_loops` + `camera_fusion` at 100 Hz.
   - **Camera lens / nadir proximity check (DONE: no effect).** The pose maps to pixel (1377, 480). The height-parallax sensitivity there is 2.01 cm/cm, against 2.19 at the pad. Original note: check `logs/video_truth/floor-1019/aruco.npz` and `logs/video_truth/mem2..mem6-*/fixes.csv` (`ground_station/analysis/live_tracker.py:111-114`, `video_truth.py:559-568`) for how close `(x=+0.30, y=-0.60, z=0.80)` is to the camera lens / frame edge and whether `TelHeight` parallax compensation (`\Delta xy = \frac{z}{c_z - z}(xy - c_{xy})`) or lens distortion near the camera edge contributes additional error at the `y = -0.60\text{ m}` lobe.
   - **Why the drone landed off-pad after the abort — CORRECTED (2026-10-19 cloud session, `docs/lab/landing-offset-analysis-2026-10-19.md`):** the L1 abort does not run `wfb_prim_land_in_place`. The GS abort sends a land, so `wfb_prim_land` returns to the pad first (RETURN -> SETTLE -> DESCEND); DESCEND started within 10 cm of the pad on all eight landings and the xy Des stayed at (0, 0) to touchdown. `land_in_place` is only the fence/ceiling and pilot-takeover action (`API/wfb_safety.c:112-131`). The offset is the landing drift below.
   - **Operator-approved fix:** Update `AbortLimits.pos_err_m = 0.50` m in `ground_station/service/abort_monitor.py` AND check/enable velocity feedforward for figure-8 so tracking error stays small.
2. **Why Landing Performance Was ~25 cm Off-Pad Across All Flights (`mem2..mem6`, `z1`, `z2`):**
   - **CORRECTED (2026-10-19 cloud session, `docs/lab/landing-offset-analysis-2026-10-19.md`):**
     - Not an abort artifact (see above). All eight landings (01-08) ended 17-41 cm off the pad (camera), at y -10..-41, x -2..+27 cm.
     - The drift builds up through the whole descent: 7-27 cm above 0.25 m, 5-30 cm from 0.25 to 0.12 m, 4-26 cm below 0.12 m (EKF).
     - Below 0.12 m the 10-15 `LandProfile_XyFreeze` set `land_contact`: the contact path (`TASK/StabilizerTask.c`, "F8 2026-10-06, PX4 style") cleared the four xy integrators (the hover trim, up to about 5.8 deg) and zeroed `locxs/ysPID.U`. `TWC.execute` is cleared by LANDING every tick (`StabilizerTask.c:2517`), not by `land_contact`; the xy Des keeps the last applied setpoint.
     - The drift above 0.12 m is still open: slow position loop vs fast descent, ground effect, or optical-flow bias near the floor. Needs 100 Hz `velocity_loops` on a landing.
   - **Done (PROPOSED, not flown):** xy loops stay closed to 0.04 m. Between 0.04 and 0.12 m the integrators are held (not cleared) and `locxs/ysPID.U` is clamped to 150 cm/s^2 (about 8.7 deg); below 0.04 m the touchdown freeze is unchanged (`API/land_profile.h`, `Pos_Compute`). Prim/fake SETTLE timeout now descends aimed at (0, 0), the setpoint the vehicle holds.
   - **Recommended:** a landing A/B against the old 0.12 m freeze, scored on the camera final position, watching the hold band for the 10-15 false-velocity chase.
3. **Optical-flow drift NN target leak — FIXED offline (2026-10-19 cloud session, `docs/lab/drift-nn-leak-fix-2026-10-19.md`):**
   - **The leak.** With `g_ext_pos_on` 2, `Ext_Pos_Tick` (`TASK/StabilizerTask.c:961`) ramps the camera correction into `ano_of.earth_x/y`. With 3, `Ext_Pos_Ekf` (`TASK/StabilizerTask.c:933`) also runs `EkfOf_UpdatePos` (`API/ekf_of.c:281`), which bends the EKF velocities and innovations. The old `nn4_data` target (truth − earth), four of its EKF input channels and its scored track (earth + ∫ prediction) all carried the camera on fused flights. The R4 table (unfused) is not affected.
   - **The fix.** `nn4_data` now defaults to target `rawof`: camera truth − `NN4_OF_SCALE` × raw OF (`ano_of.of2_dx/dy_fix`, read before the EKF at `TASK/StabilizerTask.c:846`), with `NN4_RAW_CH` features (13 channels, no fused channel). `nn4_track` scores raw OF + prediction. `nn4` adds a `rawof` baseline row and records `ext_pos_on` per flight (`ext N` in the CLI). Tests: `ground_station/analysis/tests/test_nn4_data.py` (includes a leak check: two fused flights with different camera-aided channels give identical data).
   - **10-19 evaluation: NOT RUN.** The cloud has no truth4 flights; the 10-19 campaign sessions are on the lab PC only.
   - **Session reader DONE (2026-10-19 cloud session).** `read_slots`, `fixes_clock` and `r4_truth` read a campaign session `telemetry.csv` directly (`--stem logs/campaigns/<session>`), no converter. Tests: `ground_station/analysis/tests/test_session_slots.py`. Exact lab-PC commands: `docs/lab/COMMANDS.md` 2.8 and the doc's "Local steps". Caveat: the fix-count sync assumes the tracker sent nothing before the recording; `.\cam fusion` before `.\fly` breaks that, so compare `offset_s` with `offset_takeoff_s` in `truth4.json`.
   - **Shadow / active enable: PROPOSED** (doc, "Enabling on the vehicle"). The firmware still runs no flow model; the MLP is 5314 params, 52 kMAC/s at 10 Hz.
   - **Follow-ups:** `nn_io.seq` still uses the leaky `target="earth"`; regenerate `nn4_lofo.json` and the `truth4_score_nn4.json` files on the lab PC.
4. **Other 4 Bug Fixes — DONE (2026-10-19 cloud session; briefs in `.agent-ops/briefs/j-1019-*.md`; deviations are listed in each commit message):**
   - **Fix 1 (`j-1019-exp-gains`, `cd8326d`).**
     - Firmware (PROPOSED, not flashed): `Keil_PreTakeoff()` in `TASK/StabilizerTask.c` is true on the ground, or when armed at idle with no controller driving the motors. `Exp_Poll`, `Keil_PresetPoll`, `Limit_ModePoll` and `Keil_VariantPoll` latch on it. `API/mrac.c` gets 64-bit mask shifts.
     - Host: `ground_station/service/campaign_fly.py` `preapply_exp_id` writes the row before `wait_for_arm`. `wait_txn` waits a full `ACK_GRACE_S` after the first ACK. The debrief counts a moving HOVER target as TRAJ.
   - **Fix 2 (`j-1019-ingest-lag`, `cb35c0a`).** `ground_station/service/storage.py` `SessionStore` hands appends to a bounded writer thread. Reads flush first.
   - **Fix 3 (`j-1019-tracker-center`, `35653d1`).** `ground_station/analysis/live_tracker.py`:
     - floor scenery masks the ROI while the static is learned;
     - the ROI re-centres on the pad once it is learned;
     - the trail and pad origin restart on the learned edge and on take-off.
   - **Fix 4 (`j-1019-labday-scorecard`, `08cac16`).**
     - `ground_station/lab/labday.py` `bad_end` marks aborted and never-airborne flights `ok: false`.
     - `ground_station/analysis/scorecard.py` matches the carried IDs `1017-E1..E4` and the state folders, and scores the preset figure-8 as TRAJ.

## Active / Recent Jules Sessions
- None open from this stream. The four `j-1019-*` sessions (`labday-scorecard-2`, `tracker-center`, `exp-gains`, `ingest-lag`) were superseded: the 2026-10-19 cloud session built all four briefs inline (item 4). Close or ignore any of their PRs.

## Do-Nots
- Never stage `USER/`, `OBJ/`, or `.mkv`/`.mp4` video files.
- Before any commit: run `bash tools/check.sh --fast` and relevant pytest suites.
