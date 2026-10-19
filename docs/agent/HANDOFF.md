# Handoff — stream `main`

- **Updated:** 2026-10-19 23:55 +08:00
- **Branch / Base:** `overnight-2026-10-08` (mirrored to `main`)
- **Goal:** Complete all 2026-10-19 post-flight analyses and land/verify all fixes for the 2026-10-20 lab session (`docs/lab/days/2026-10-20.yaml`, 21 flights).

## Completed in 2026-10-19 Session
1. **Merged & Pushed All Cloud Agent Branches (`d4898337`):**
   - Merged `elated-cartwright-4fa5e7`, `loving-mccarthy-wr7naa`, `compassionate-rubin-xojtv2`, `kind-goldberg-o9rfdm`, `clever-darwin-8drxdj`, `angry-saha-12497c`, `dazzling-cori-oh88qd` + cherry-picked unique commits (`faddfc85` `/api/actions`, `32cb52af`, `89ec0428`, `00f9bff1`, `1188eccd`, `9863cb3e`, `66ed79b8`, `33ce1003`, `34ed57a9`, `058d05a5`).
2. **2026-10-19 Flight Logs & Initial Analysis Committed to Git:**
   - Force-added the lightweight 2026-10-19 CSV/JSON/NPZ logs (`logs/flights/2026-10-19/`, `logs/analysis/2026-10-19/memory_premise.json`, `logs/lab/2026-10-19/labday_state.json`, `logs/video_truth/floor-1019/aruco.npz`, `logs/video_truth/*-lab-*/fixes.csv`) so cloud agents have full access to today's flight telemetry and camera truth.
   - Generated `docs/lab/days/2026-10-19-report.md` and `docs/lab/days/2026-10-19-report.json`.
   - Created tomorrow's campaign deck `docs/lab/days/2026-10-20.yaml` (21 flights).

## Root Causes Diagnosed (Ready to Implement / Verify)
1. **Why Figure-8 Tracking Error Peaked (~36.5 cm) Near `y = -60 cm` & Tripped `L1 position_error` (Operator Approved Fix):**
   - **Controller phase lag (primary):** In `TASK/AutoflyTask.c:328-341` (`AutoflyTask_CommitRef` `L137-174`), `g_traj_vff_x/y` (velocity feedforward) is NOT set for `figure8` (only set at `L819` for Keil presets), or `pos_ctrl` uses pure position error with $K_p = 0.8\text{ s}^{-1}$ (`API/pid.c:36`). At $v_{pk} = 0.38\text{ m/s}$, steady-state tracking lag is $e = v / K_p = 0.475\text{ m}$, tripping `AbortLimits.pos_err_m = 0.25\text{ m}` (`ground_station/service/abort_monitor.py:18`).
   - **Camera lens / nadir proximity check:** Check `logs/video_truth/floor-1019/aruco.npz` and `logs/video_truth/mem2..mem6-*/fixes.csv` (`ground_station/analysis/live_tracker.py:111-114`, `video_truth.py:559-568`) for how close `(x=+0.30, y=-0.60, z=0.80)` is to the camera lens / frame edge and whether `TelHeight` parallax compensation (`\Delta xy = \frac{z}{c_z - z}(xy - c_{xy})`) or lens distortion near the camera edge contributes additional error at the `y = -0.60\text{ m}` lobe.
   - **Why the drone returned/hovered and landed off-pad after abort:** L1 abort triggers `wfb_prim_land_in_place` (`API/wfb_prim.c:89-99, 230-234`), which freezes `target_x, target_y` at the abort position (`x≈+0.23, y≈-0.13..-0.20 m`) and descends there instead of returning to `(0, 0)`.
   - **Operator-approved fix:** Update `AbortLimits.pos_err_m = 0.50` m in `ground_station/service/abort_monitor.py` AND check/enable velocity feedforward for figure-8 so tracking error stays small.
2. **Why Landing Performance Was ~25 cm Off-Pad Across All Flights (`mem2..mem6`, `z1`, `z2`) & How to Fix Near-0 cm Landing:**
   - **Cause A (L1 abort lands in place):** `wfb_prim_land_in_place` (`API/wfb_prim.c:89-99, 230-234`) freezes `(x, y)` at the abort point instead of returning to `(0, 0)` when safe (`position_error` / `stale_telemetry`).
   - **Cause B (`LAND_XY_FREEZE_ALT = 0.12f` open-loop attitude trim drift in `z1` and all landings):** Below $z = 0.12\text{ m}$ (`API/land_profile.h:23,34-37`), `LandProfile_XyFreeze()` sets `land_contact = 1` (`TASK/StabilizerTask.c:2350`). In `TASK/StabilizerTask.c:2214-2224`, `land_contact` zeros all outer XY integrators and outputs (`locx/yPID.SumE = 0`, `locxs/ysPID.U = 0`) and sets `TWC.execute = 0` (`L499-503, 2598`), leaving the drone to descend the last 12 cm in ground effect completely open-loop on hardcoded attitude trims (`StabilizerTask.c:1920-1921`: roll `-1.24°`, pitch `-0.90°`), which pushes it consistently to `x ≈ +20..24 cm, y ≈ -12..-18 cm`!
   - **Fix required:** Keep closed-loop XY position/velocity hold (and live camera EKF corrections) active down to touchdown (`z <= 0.04 m` or until touchdown detection, preserving low-authority clamped integrator/PD instead of zeroing `locxs/ysPID.U = 0` at 12 cm), and ensure normal/soft-abort `RETURN` centers tightly on `(0, 0)` before descending.
3. **Optical Flow Drift Neural Network (`nn_drift` / `video_truth.py:1824-1880`):**
   - **Current status:** Firmware does NOT yet run an optical-flow drift NN in shadow mode (firmware only runs MRAC RBF weights in `API/mrac_variant.h:28`). The OF drift MLP (`2-layer tanh MLP`, `nn4_data` / `_mlp`) currently lives offline in `ground_station/analysis/video_truth.py:1824-1880`.
   - **Does live camera EKF fusion (`g_ext_pos_on >= 2`) corrupt NN training data?**
     - **Partially true if trained on `earth` (EKF fused state), FALSE if trained on raw optical flow (`ano_of.of2_dx_fix`, `of1_dx/dy`, `gyrox/y`, `z`) vs camera truth (`fixes.csv`):** Because `FuseExtPos` (`TASK/StabilizerTask.c:914-995`) injects live camera position corrections into `s_ekf` (`earth`), training on $Y = \frac{d}{dt}(\text{truth} - \text{earth})$ on closed-loop camera flights learns the post-correction EKF residual rather than pure optical-flow sensor drift. However, the raw optical-flow sensor measurements (`of_vx, of_vy`) and IMU/height are unaffected by `FuseExtPos`, so training the drift NN on `(raw optical flow + IMU + height) -> (camera truth velocity - raw optical flow velocity)` makes 100% of closed-loop camera flights valid training data!
4. **Other 4 Bug Fixes (Briefs in `.agent-ops/briefs/j-1019-*.md`):**
   - **Fix 1 (`j-1019-exp-gains`):** `TASK/StabilizerTask.c:1373` `Keil_OnGround()` (`flight_phase == FLIGHT_PHASE_IDLE`), `API/mrac.c:412` (`mrac_HasConverged`), `ground_station/service/campaign_fly.py` (`preapply_exp_id` before `wait_for_arm` + `wait_txn` grace window).
   - **Fix 2 (`j-1019-ingest-lag`):** `ground_station/service/storage.py` async SQLite writer thread + WAL so `_ingest_loop` never blocks for 0.5–1.3 s.
   - **Fix 3 (`j-1019-tracker-center`):** `ground_station/analysis/live_tracker.py` `TrackCoral` uses `floor_scenery` from frame 0, resets `self.last_uv = None` when `static_n` finishes, and clears `Preview.uvs` on arming (`armed_edge`) so there is no motor-guard corner latch or jump line.
   - **Fix 4 (`j-1019-labday-scorecard`):** `ground_station/lab/labday.py` `bad_end` marks `ABORTED` / `air_s <= 0` as `ok: false`; `ground_station/analysis/scorecard.py` matches carried flight IDs (`1017-E1..E4`) and preset figure-8 trajectory samples.

## Active / Recent Jules Sessions
- `j-1019-labday-scorecard-2`: session `16900527191721982883` (running; brief `.agent-ops/briefs/j-1019-labday-scorecard.md`)
- `j-1019-tracker-center`: session `14727321303794590842` (`AWAITING_USER_FEEDBACK`; brief `.agent-ops/briefs/j-1019-tracker-center.md`)
- `j-1019-exp-gains`: session `4640439767887508114` (`FAILED` VM error; brief `.agent-ops/briefs/j-1019-exp-gains.md`)
- `j-1019-ingest-lag`: session `5177524343767172919` (`FAILED` VM error; brief `.agent-ops/briefs/j-1019-ingest-lag.md`)

## Do-Nots
- Never stage `USER/`, `OBJ/`, or `.mkv`/`.mp4` video files.
- Before any commit: run `bash tools/check.sh --fast` and relevant pytest suites.
