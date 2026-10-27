# Handoff — stream `main`

## Ship point 2026-10-28 05:00 (CEO, autonomous night run)

- Next lab: `.\labday 2026-10-28` (deck `docs/lab/days/2026-10-28.yaml`, dry run 15/15 OK). FLASH FIRST: COM6 was absent
  tonight, so the firmware with VAR 47 default 33 and the sysid_u / sysid_state mirrors is built and committed, not flashed.
- Verdicts `docs/lab/2026-10-28-verdicts.md`: the 2.3-2.9 Hz wobble is the rate-loop crossover (PM 40-45); proposed
  preset `rate32d169y104` (roll/pitch Kp3.2 Kd16.9) is the day gains, approved y/n in the terminal. LQR failures after
  10-15 = Smith-predictor delay 12/18/21 instead of the 33 the gains were designed for (fixed in cards + firmware default).
- Open (`docs/lab/open-items.md`): in-flight stale 8081 height; vel-y sign map; `logs/ref_fit/lqr.json` missing
  (optional xy-ref flights); attitude u_ad reset between flights; faster log slot for gyroxPID FB/Des + roll u_ad/u_nom;
  check highdtsPID.Des is in the log group of `lab_d_sysid_vel_z.fly`.
- Nothing was written to the drone tonight.

## Current task: 2026-10-27 SysID gate repair and roll restart (Codex, inline)

- Goal: repeat roll -> pitch -> yaw in deck camera mode 0, then use the recorded-data PID gate.
- Facts: pitch/yaw mode-0 flights 19/20 completed without abort. Original tuning gate imported nonexistent
  ground_station.analysis.autotune and used obsolete arguments, flight-folder inputs and result keys.
  Repair resolves recorded telemetry, saved excitation bands and flown gains; recomputes fits and reads
  nested results. Saved pitch two-band replay reaches fitting: 58 coherent bins, 80% coverage, residual
  0.492 > 0.35, so it legitimately refuses. Low-band-only residual 0.172 is not a wide-band validation.
- Constraints/gates: no controller, estimator, excitation, rate or safety-limit changes; no gains applied.
  Synthetic contract checks validate software only. Mode 0 gives up camera drift correction.
- Next 3 actions: (1) quit/restart labday from rate-sysid-roll-wide; (2) fly mode-0 roll/pitch/yaw and inspect
  gate refusals before accepting any PROPOSED gain; (3) after lab investigate wide-band model quality,
  SysID scorecard semantics, background fit defaults and complete effective gain snapshots per open-items.
- Do-nots: no flash for this fix, no relaxed abort limits, no outside workers, preserve the unrelated dirty work.
- Broader checks: command-doc suite already fails on cfab launcher mapping; deferred in open-items.
- Resume: `.\labday 2026-10-27 --from rate-sysid-roll-wide` (new process; 8081 may stay up).


- **Updated:** 2026-10-25 18:2x +08:00 (desktop CEO: figure-8 verdict corrected, diagnosis ladder in deck)
- **Branch / Base:** local `main` is the work tip (`7bd26fd7`), mirrored to `origin/nightly-1022` at ship points (`git push origin main:nightly-1022`). `origin/main` moves only through `tools/land.sh`.

## Afternoon 2026-10-25 (desktop CEO): lab-ready pass
- **Commits:** b132d2e5 (capture logs RC authority + raw sticks on every flight, CAMPAIGN_SET 50 vars), e1618060 (10-25 deck: smoke-hover first, hands-off-sticks + 8081 restart lines, two abort branches).
- **Firmware:** flashed sha d5fe42bc = current `OBJ/JX_FLY.axf`, includes the last firmware commit 1b5f19a0; no dirty .c/.h. No reflash.
- **10-24 analysis:**
  - Figure-8 aborts (mpc, pid, matrix): 40-130 cm off path, roll +-12-15 deg, no arm/mode/prim change before Traj_End(3). CORRECTED 18:xx (operator): the drone diverged from the path and the operator took over to save it (takeover = effect, flight = FAILED). Measured: path x 0.36 / y 0.18 Hz, response swings at its own ~0.45-0.50 Hz (= hold wobble), 1.7-4.2x path amplitude. Two hypotheses, separated by the deck's block 5b diagnosis ladder: (a) lightly damped xy loop (all controllers wobble in hover), (b) operator's: path too coupled/hard (PID: small hold wobble but 130-160 cm on figure-8 supports it). adrc: prim 2->4; pid-track: disarm.
  - memory-mrac-off-6: host position_error guard (0.50 m / 0.3 s), a real excursion; guard unchanged.
  - Memory pairs, camera RMS cm (traj / hold): MRAC off 30.2, 24.0, 26.4 / 10.6, 7.1, 11.5; MRAC on 25.9, 25.8 / 8.0, 8.1. On is slightly better; n too small.
  - Landings (truth4): l0 53.9, l1 67.8, l2 60.3, x-delay 27.3 cm; pid-track 107 cm.
  - Alone-retests: ALL FAILED incl. LQR (operator verdict; scorecard now agrees: lqr hold p-p 49.6 cm, figure-8 swing 3.6x; the old "pass" was an RMS proxy); adrc/mpc/pid xy limit cycle 0.25-0.63 Hz. VAR 47 reaches LQR/MPC only (12 PROPOSED); ADRC cause OPEN.
- **Argon:** allowed for research and second opinions (operator 10-25). Done: `.agent-ops/out/argon-n25-fig8-resonance.md` (verdict loop resonance; caveats: used old 0.44 Hz path, blames VAR47 though ADRC/PID ignore it, too quick to drop the operator's hypothesis; its 1D X-only sine idea is a good next separator; unverified).
- **Sysid-outer (Jules):** review loop SKIPPED (sending replies to Jules = outside worker, revoked 10-25). Not merged, not flashed; the deck has a branch for it.
- **At the lab:** restart 8081; labday scans the floor itself at launch (drone out of view); hands off the sticks during trajectories; Q1 rate-gain gate (rate35 / yaw104 PROPOSED, chat approval).

## Overnight 2026-10-24 -> 10-25 (desktop CEO)
- **Oscillation verdict: two faults.**
  1. High-frequency (first LQR/MPC flights): an under-damped RATE loop shared by every controller. Current rate PID on the 10-24 fitted plant: PM roll 21.4 deg, pitch 28-30 deg, max|S| 2.4-3.0, wc ~21 rad/s.
     - Source: autotune `design.margins`, `.agent-ops/research/rate-fit-1025/<axis>-<log>.log` l.25-27 (untracked).
     - Roll re-derived by hand: PM 21.7 deg.
     - PROPOSED: roll/pitch Kp 5->3.5, Kd 10->13 (PM 34 / 43-46 deg). The ideal Kp~2-3, Kd~14-16 (PM 50-53) is beyond the CLI's +-30% step.
  2. Low-frequency 0.25-0.63 Hz (10-24 alone-retests): an outer xy limit cycle from VAR 47=21 (10 ms ticks, default 12, `TASK/StabilizerTask.c:2001-2004`), ext_pos_on=3, and the stiff LQR K (`API/xy_ctrl_gains.h`).
- **Rate fits (10-24):** roll 214336 k9.46 tau90ms T13.2ms; pitch 213634/214336 k6.0/6.5 tau53/58ms T19/18ms; yaw 215247 k1.25 T12ms. Refused: roll 213634 (residual 0.36), yaw 214954 (too short). Both are re-fly items.
- **Outer-loop sysid firmware (Jules session 11223073449949261776):**
  - Status: implemented, host tests pass (Jules' report).
  - It asked about an unrelated `test_install_hooks.py` failure; reply1 (round 1) sent: no unrelated edits, open the PR.
  - Next: review against `.agent-ops/briefs/n25-sysid-outer-review.md` checklist, port by hand, host test, flash via skill.
- **10-25 decks (commit 5c1b7b96, CEO inline):** `docs/lab/days/2026-10-25.yaml` (40 flights, 9 blocks, thesis first; +10 carried earlier flights at the tail) and `docs/lab/days/2026-10-25-mode1.yaml` (3 alone-retests, camera mode 1). Open questions Q1-Q3 are in the deck header. Gain presets `rate35`, `yaw104` (PROPOSED) and `defaults` (now restores rows 9-17) in `campaign_fly.py`; VAR47=12 scripts `lab_e_exp_{lqr,mpc}_u0_d12.fly`.
- **Jules sysid-outer: BLOCKED.** AWAITING_USER_FEEDBACK, no PR after reply1 + nudge2. Morning: the user presses Publish at https://jules.google.com/session/11223073449949261776, or the CEO ports it inline. Outer sysid flights stay gated.
- **Outside workers STOPPED (user, 2026-10-25):** no Argon/Jules/oc/VPS/ark dispatches; the CEO does all work inline. AGENTS.md and the global CLAUDE.md still describe the Argon workflow until the user updates them.
- **Morning lab steps:**
  1. Restart 8081.
  2. Floor scan (so floor-2026-10-24/aruco.npz exists).
  3. `d5_truth4_batch.py --day 2026-10-24 --run`.
  4. Decide: adopt the PROPOSED rate gains before or after the thesis blocks (chat approval for the gain write).

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

## Build run 2026-10-22 (no lab; spec `docs/design/2026-10-22-mrac-experiment-stack.md`)

| item | what | commit |
|---|---|---|
| A1 / A3 / A6 | tracker median of the guard pixels in the air; xy camera delay flight last in the fast deck; decks moved to 10-23 | 84751816, 1fc246ac, 733619c9 |
| 1 excite | 24-row multisine pattern library (CMD 0x2E), scenario `pattern` step, fake-drone sim | e4b4d287 |
| 2 L2 | MRAC feature gating, trap + revival per feature (CMD 0x1D fields 26-30) | 04741266 |
| 6 mrac_xt | experiment telemetry block (81 int16 words: pattern, e, u_ad, trap bits, group EMA) | f3d2ca68 |
| 7 mrac_active | host analysis: excited / active / repeatability / falsification on mrac_xt | 156d2168 (host only) |
| 3 L3 prior | MRAC prior over WiFi (CMD 0x2F..0x34), disarmed re-seed | 477d6663 |
| 4 D3 | x/y reference model row per nominal kind, selected on the ground | 7aa22edf |
| 5 D4 | x/y layers per flight (CMD 0x1D field 31: inner / outer / both), outer position layer; bw / zeta PROPOSED | d0f29927 |

Each firmware item has a host test that fails on the old code, mode 0 bit-identical (EQUIV OK plain + sigma-prior) and
Keil 0 Error(s). None is flashed or flown. The public CMD 0x1D sender landed 10-23 (`HttpWfbClient.mrac_variant` /
`.mrac_xy_layers`, COMMANDS 4.5, open item `xy-layers-sender` closed); the x/y layer target is still UNVERIFIED in
flight.

Overnight soak on the fake drone at f29459cd (`logs/soak/soak-2026-10-23*`, reports in each folder):

| deck | flights | complete | ingest lag p99 / max | backlog / drops / rss | result |
|---|---|---|---|---|---|
| main (`soak-2026-10-23`) | 13 | 13 | 10 / 245 ms | 0 / 0 / 466 MB | all checks PASS except the 200 ms lag gate: 1 frame of 81195 over (memory-mrac-on-6, one 245 ms spike after its pack swap). No stale_telemetry, no queue backlog, service never restarted. |
| fast (`soak-2026-10-23-fast`) | 5 | 5 | 15 / 123 ms | 0 / 0 / 591 MB | every check PASS. No block over 200 ms in 29860 frames. |
| mode1 (`soak-2026-10-23-mode1`) | 3 | 2 | 15 / 94 ms | 0 / 0 / 501 MB | `drift-nn-live-1` aborted `L3 firmware:HEARTBEAT` at 30.7 s air, right after the mid-day pack swap (the 12 s reboot silence + the 1 Hz lockout probe against the firmware's 1.0 s hb timeout); `drift-nn-live-2` then completed. UNFIXED - see the 10-21 no-gates item on locking out on rx_age. |

The 10-21 heartbeat-lockout root cause is still open: lock out on `rx_age` (the link really silent), not on reply age.

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
| built 10-22 / not started | D4 outer/inner MRAC layers built (d0f29927, research sim 9fb1dad3; A/B not flown). Not started: D5 drift NN retrain (Kaggle, Neural-Fly; decision A5), D6 thesis effect sizes | |

Workers: Jules d9b failed; Argon quota 429 until about 10-24 01:30; colab-mcp CONNECT_TIMEOUT (Kaggle is the NN lane).

## Next Steps (2026-10-23 lab; the 10-22 lab moved)
1. **One service per laptop.** The service binds UDP 14550 with SO_REUSEADDR, so a second service goes deaf without an error (open item `bridge-port-shared-silently`). The stale 8081 was stopped 10-22 06:30; let `labday` start a fresh one.
2. **Flash** (bundles 82f4f250 gate mask, 5486a26b ADRC wo 12, 94aca11f D8 fix B and the build run e4b4d287 04741266 f3d2ca68 477d6663 7aa22edf d0f29927): `python -m ground_station.flashtool.rebuild_and_flash --yes`, then `python -m ground_station.livewatch verify`. The first flight after the flash is `smoke-hover`.
3. **Fly** `docs/lab/days/2026-10-23.yaml` (13 flights; pack swap at `memory-mrac-on-7`), then `docs/lab/days/2026-10-23-fast.yaml` if time allows; `docs/lab/days/2026-10-23-mode1.yaml` = landing record + drift NN hovers (NN live only if the Kaggle retrain beats the old model, else shadow: decision A5).
4. **After the day:** `python -m ground_station.analysis.scorecard 2026-10-22`. Check the config column of the report: the 10-21 report shows `pid / gains default / mrac_injection 0` for every exp row (suspected bug, not yet checked).
5. **Landing is safe to use again.** `tools/land.sh` no longer dies with `python: command not found` under WSL bash (it probes like `check.sh`), and `tools/test_land_ci.py` no longer calls the real api.github.com from a unit test (Git Bash + a `CURL` override). Open item `land-sh-no-python`; `c-pytest` 80 passed.
6. **Run the gate under Git Bash on this box.** `bash tools/check.sh --changed` resolves to WSL bash, whose git normalizes to CRLF while the Windows git behind the pre-push guard keeps LF: the run passes, records a `tree=` that does not match, and the guard refuses the push. `& 'C:\Program Files\Git\bin\bash.exe' tools/check.sh --changed` records the matching tree (verified 2026-10-23). Open item `check-sh-under-wsl-bash-records-a-wrong-tree`.
7. **The green gate does not cover the ground-station suites.** `c-pytest` runs a hardcoded 6-file list, so `ground_station/comm/tests/` and `ground_station/service/tests/` never run in the gate; `test_mrac_param_encoder.py::test_drift_command_gate_admits_the_wide_block` is red on its own because it pins the pre-excite/pre-prior `CommandSafetyReject` line. Not a regression from the current diff (4 of the other 8 pass when run alone). Open item `c-pytest-misses-gs-tests`.
8. **Still open from the 10-23 soak:** lock the host heartbeat out on `rx_age` (link silent), not on reply age — that is what aborted `drift-nn-live-1` (`mode1` 2/3). Everything else in the 10-23 decks completed (main 13/13, fast 5/5).

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
path-routed gate (the pre-push guard, routed checks, `tools/land.sh`, merged)
are both on `main`. Gate = `bash tools/check.sh --changed`; main moves only
through `bash tools/land.sh <branch>` (`docs/agent/governance-zones.md`).

Overnight streams push `main:nightly-<date>`. The pre-push guard compares the changed files with the last `check.sh --changed` run, so run the check and the push back to back in one command.

<!-- AUTO:BEGIN -->
Refreshed: 2026-10-27 22:54 (mechanical, no LLM)
- Tree: .; branch / HEAD: main @ 22a93cc1; unpushed commits: 219
- Dirty paths outside OBJ/: 93
  -  M .claude/settings.json
  -  M .claude_state.md
  -  M API/tests/test_alt_cf.c
  -  M TASK/AutoflyTask.c
  -  M TASK/StabilizerTask.c
  -  M TASK/StabilizerTask.h
  -  M USER/JX_FLY.uvguix.Acer
  -  M USER/JX_FLY.uvoptx
  - ... +85 more
- Dashboard 8081: UP (HTTP 200)
- Last commits:
  - 22a93cc1 fix(lab): repair recorded-data PID tuning gate
  - b100b458 fix(lab): isolate SysID flights from camera corrections
  - 8a195ad8 docs(lab): open items from the cfab hover logs
  - 505a1fe5 fix(lab): CF reader keeps frames that share one receive stamp
  - 63c85040 docs(lab): CF resume plan step 6 uses cfab, Ki 0.005
<!-- AUTO:END -->
