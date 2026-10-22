# Handoff — stream `main`

- **Updated:** 2026-10-22 07:05 +03:00 (desktop CEO, overnight run 10-21 -> 10-22)
- **Branch / Base:** local `main` is the work tip (`38943d0d`), mirrored to `origin/nightly-1022` at ship points (`git push origin main:nightly-1022`). `origin/main` moves only through `tools/land.sh`.

## Stand-down note (operator 2026-10-20, applies to every agent in every harness or account)

The operator hit the Claude "Individual quota reached" wall on the prior session and asked for two changes
that apply project-wide, including to outside workers and to future supervisor sessions:

1. **Always be strategic globally and tactical locally** (see `AGENTS.md` > Conventions, and
   `docs/agent/memory/rules.md` > Operating principle). Persist every cross-agent truth to a file
   other agents actually read (`docs/lab/`, `docs/agent/HANDOFF.md`, `docs/agent/memory/`); pick
   the smallest, cheapest locally-verifiable step that advances the plan.
2. **Survive quota hits** by saving at every subtask, mirroring artefacts to `origin/*`, and
   dropping a `.agent_state/last-cutoff.md` resume file when the wall closes in
   (`docs/agent/memory/rules.md` > Quota-hit survival).

## Overnight 2026-10-21 -> 10-22 (stream `nightly-1022`)

Day plan: `docs/lab/open-items.md` > DAY PLAN 10-22 (D1-D10). Briefing: https://claude.ai/artifact/Sb6w1RVhUEdPnVst3pJGrj

| item | result | commit |
|---|---|---|
| D1 oscillation (LQR / MPC / ADRC) | xy predictor delay VAR 47 12 -> 21 ticks in all exp scripts; ADRC observer 19.36 -> 12 rad/s (firmware). On 10-21 the predictor ran 12 ticks, 9 short of the measured 21 (not the header 33). Root cause is inference until the retests fly. | 7c4d7e21, 5486a26b |
| D3 MRAC wrapped around ADRC | the wrap fights the PID reference model (sim + doc) | 889afab7 |
| D7 sysid quality | delay unidentifiable from the data; fixed-delay refit; coloured residuals | 53a7957f |
| D8 high-speed drift | OF health trip latches FIXED, so mode 3 stops applying camera fixes; tight vs wide figure-8 = x lobe bandwidth, not the tilt cap | 4d76df59, c2744df0 |
| D9 firmware + pipeline review | xy kind switch, VAR 19 timeout, service threads | 9630ad62 |
| N5 gate mask | supervised gate mask + c3 robust xy gains as default (firmware) | 82f4f250 |
| D10 decks | main (13 flights) + fast (speed ladder, camera mode 2). Main deck fake-drone soak PASS 13/13, 9/9 checks (`logs/soak/deck22b/report.md`) | 3b910cb4 |
| soak fix | soak refuses to start when another service holds UDP 14550; Windows-safe process cleanup | d20acfa7 |
| not started | D4 outer/inner MRAC A/B, D5 drift NN (Kaggle, Neural-Fly), D6 thesis effect sizes | |

Workers: Jules d9b failed; Argon quota 429 until about 10-24 01:30; colab-mcp CONNECT_TIMEOUT (Kaggle is the NN lane).

## Next Steps (2026-10-22 lab)
1. **One service per laptop.** The service binds UDP 14550 with SO_REUSEADDR, so a second service goes deaf without an error (open item `bridge-port-shared-silently`). The stale 8081 was stopped 10-22 06:30; let `labday` start a fresh one.
2. **Flash** (bundles 82f4f250 gate mask + 5486a26b ADRC wo 12): `python -m ground_station.flashtool.rebuild_and_flash --yes`, then `python -m ground_station.livewatch verify`. The first flight after the flash is `smoke-hover`.
3. **Fly** `docs/lab/days/2026-10-22.yaml` (13 flights; pack swap at `memory-mrac-on-7`), then `docs/lab/days/2026-10-22-fast.yaml` if time allows.
4. **After the day:** `python -m ground_station.analysis.scorecard 2026-10-22`. Check the config column of the report: the 10-21 report shows `pid / gains default / mrac_injection 0` for every exp row (suspected bug, not yet checked).

## Repo sync log (2026-10-20 04:02 +03:00)

This session found and fixed three inconsistencies between cloud branches and `origin/main`:

| What | Before | After |
|---|---|---|
| `origin/main` vs `origin/claude/labday-1019-fixes` | 13 commits behind (VAR_WRITE 56/57, landing, storage, abort, exp latch, tracker, scorecard fixes) | Merged (`226ecd45` tip) |
| `origin/main` vs `origin/claude/friendly-hypatia-3ln6o5` | 2 commits behind (`2889150a` = nn_io.seq rawof move, `cfcd3f17` = retrain) | Merged |
| `origin/main` vs `origin/overnight-2026-10-08` | 242 commits behind | Fast-forwarded |

The local `overnight-2026-10-08` branch (`f6a2fd66`: checkpoint before switching to main) is stale — `origin/overnight-2026-10-08` and `origin/main` share the same tip (`90934b60`). No local work was lost.

## Completed in 2026-10-19 Session (lab PC)
1. **Merged & Pushed All Cloud Agent Branches (`d4898337`):**
   - Merged `elated-cartwright-4fa5e7`, `loving-mccarthy-wr7naa`, `compassionate-rubin-xojtv2`, `kind-goldberg-o9rfdm`, `clever-darwin-8drxdj`, `angry-saha-12497c`, `dazzling-cori-oh88qd` + cherry-picked unique commits.
2. **2026-10-19 Flight Logs & Initial Analysis Committed to Git:**
   - Force-added the lightweight 2026-10-19 CSV/JSON/NPZ logs so cloud agents have full access to today's flight telemetry and camera truth.
   - Generated `docs/lab/days/2026-10-19-report.md` and `docs/lab/days/2026-10-19-report.json`.
   - Created tomorrow's campaign deck `docs/lab/days/2026-10-20.yaml` (21 flights).

## Completed in 2026-10-19 Cloud Session (`claude/labday-1019-fixes` + `claude/friendly-hypatia-3ln6o5`)
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
| nn_io.seq rawof move | `cfcd3f17` → `2889150a` | DONE (17 tests pass) |
| Drift NN live switch (VAR_WRITE 56/57) | `226ecd45` | Firmware DONE, NOT BUILT (needs Keil + flash) |
| Campaign session telemetry reader | `69922e9f` | DONE (host) |

- **10-19 report NOT regenerated.** The cloud has no `logs/sessions/*` telemetry. On the lab PC, run `python -m ground_station.analysis.scorecard 2026-10-19`.
- **Flash before 10-20.** Build and flash the `226ecd45`/`a5ffa3c`/`868f4ba` firmware together (the NN live switch, figure-8 VFF, and landing fixes). Then fly: the figure-8 A/B, the landing A/B, one exp row and check that `exp_active` matches the flight.
- **Pre-existing test failures.** The full `python -m pytest` gives 49 failed + 1 error, 2979 passed on this merged tip. Every failure also fails on base `90934b60`. They are not regressions. Known causes: no torch, no Keil/ARM toolchain, no `-m32`.

## Root Causes Diagnosed (Ready to Implement / Verify)
1. **Why Figure-8 Tracking Error Peaked (~36.5 cm) Near `y = -60 cm` & Tripped `L1 position_error` (Operator Approved Fix):**
   - **CORRECTED (`docs/lab/figure8-tracking-analysis-2026-10-19.md`):** not v/Kp lag.
     - `mem2`..`mem6` flew the Keil `preset figure8`, whose VFF (`g_traj_vff_x/y`) was active.
     - The error is a growing overshoot at each x extreme. VFF+P model explains 12-15 cm of it.
     - GS `circle`/`sinusoid`/`figure8_path` commands had no VFF. They now get it through `Traj_Commit` + `Traj_FFUpdate` in `TASK/AutoflyTask.c`.
     - Recommended: an A/B figure-8 arm with the `lab_c_tuned.fly` gains (shift 0.31). Log `velocity_loops` + `camera_fusion` at 100 Hz.
   - **Camera lens / nadir proximity check (DONE: no effect).** The pose maps to pixel (1377, 480). The height-parallax sensitivity there is 2.01 cm/cm, against 2.19 at the pad.
   - **Operator-approved fix:** Update `AbortLimits.pos_err_m = 0.50` m in `ground_station/service/abort_monitor.py` AND check/enable velocity feedforward for figure-8 so tracking error stays small.
2. **Why Landing Performance Was ~25 cm Off-Pad Across All Flights:**
   - **CORRECTED (`docs/lab/landing-offset-analysis-2026-10-19.md`):**
     - Not an abort artifact. All eight landings ended 17-41 cm off the pad (camera), at y -10..-41, x -2..+27 cm.
     - Below 0.12 m the `LandProfile_XyFreeze` cleared the four xy integrators and zeroed `locxs/ysPID.U`, leaving the drone to descend completely open-loop on hardcoded attitude trims.
   - **Done (PROPOSED, not flown):** xy loops stay closed to 0.04 m. Between 0.04 and 0.12 m the integrators are held (not cleared) and `locxs/ysPID.U` is clamped to 150 cm/s^2; below 0.04 m the touchdown freeze is unchanged (`API/land_profile.h`, `Pos_Compute`). Prim/fake SETTLE timeout now descends aimed at (0, 0).
   - **Recommended:** a landing A/B against the old 0.12 m freeze, scored on the camera final position.
3. **Optical-flow Drift NN Target Leak — FIXED offline:**
   - **The leak.** With `g_ext_pos_on` 2-3, the EKF camera correction bent the `nn4_data` target (truth − earth) and its scored track on fused flights.
   - **The fix.** `nn4_data` now defaults to target `rawof`: camera truth − `NN4_OF_SCALE` × raw OF (read before the EKF), with `NN4_RAW_CH` features (13 channels, no fused channel). Tests: `ground_station/analysis/tests/test_nn4_data.py`.
   - **nn_io.seq rawof move** (`2889150a`): `ground_station/analysis/nn_io.py` now trains on the `rawof` target by default. 17 tests pass.
   - **Firmware TCN live switch DONE in code, NOT BUILT.** `g_drift_nn_live` (VAR_WRITE 56: 0 shadow, 1 position track, 2 also EKF velocity FB) and `g_drift_nn_clamp_mps` (57, default 0.10) apply its clamped body-velocity residual, gated per tick by `Dnn_LiveGate` (`TASK/StabilizerTask.c`). **Needs Keil build + `rebuild_and_flash` before any flight that logs `drift_nn` or sets live** (the old ELF has no `live_on`/`dvx` fields).
   - **Follow-ups:** `nn_io.seq` was regenerated on the rawof target; regenerate `nn4_lofo.json` and `truth4_score_nn4.json` files on the lab PC.
4. **Other 4 Bug Fixes — DONE:**
   - **Fix 1 (`j-1019-exp-gains`, `cd8326d`).** `Keil_PreTakeoff()` in `TASK/StabilizerTask.c` latches exp rows on RC-arm. `campaign_fly.py` writes `preapply_exp_id` before `wait_for_arm` with a full ACK_GRACE_S window.
   - **Fix 2 (`j-1019-ingest-lag`, `cb35c0a`).** `ground_station/service/storage.py` `SessionStore` hands appends to a bounded writer thread. Reads flush first.
   - **Fix 3 (`j-1019-tracker-center`, `35653d1`).** `live_tracker.py`: floor scenery masks ROI during static learning; ROI re-centres on the pad once learned; trail and pad origin restart on take-off.
   - **Fix 4 (`j-1019-labday-scorecard`, `08cac16`).** `labday.py` `bad_end` marks aborted/never-airborne flights `ok: false`. `scorecard.py` matches carried IDs and scores preset figure-8 as TRAJ.

## Next Steps (2026-10-20, superseded)
Flown or rolled into the 10-21 and 10-22 decks.

## Active / Recent Sessions
- No open cloud sessions. The four `j-1019-*` sessions were superseded by the inline work.

## Do-Nots
- Never stage `USER/`, `OBJ/`, or `.mkv`/`.mp4` video files.
- Before any commit: run `bash tools/check.sh --fast` and relevant pytest suites.
- Never flash while armed. Flash only from stream `main` with the `hw` lock held.

## Repo-architecture note (2026-10-20)

The context map (AGENTS.md, `docs/context/README.md` shards) and the
path-routed gate (4641a688: pre-push guard, routed checks, `tools/land.sh`)
are both on `main`. Gate = `bash tools/check.sh --changed`; main moves only
through `bash tools/land.sh <branch>` (`docs/agent/governance-zones.md`).

Overnight streams push `main:nightly-<date>`. The pre-push guard compares the changed files with the last `check.sh --changed` run, so run the check and the push back to back in one command.
