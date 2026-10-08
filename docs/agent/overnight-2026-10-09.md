# Overnight autonomous run, 2026-10-09 → morning 10-10

Goal: a "final boss" adaptive variant that beats PID on the 570 g rope and the 293 g arm load, chosen by data
(today's logs, simulations, public datasets, literature), ready for you to flash and fly tomorrow.
I tick the boxes as items finish. Result links are added next to each item.

Rules I keep: no flashing, no arming, no Keil/gcc firmware builds (host compiles and tests only), no Claude
subagents. Research, downloads and heavy runs go to the VPS agy and ark workers. Every worker claim is checked
against the opened link before it goes into a doc. Numbers stay PROPOSED unless I measured them tonight.

## How I read your words (correct me in the morning)

| you said | I read it as |
|---|---|
| "SINTY algorithm" | SINDy (sparse identification of nonlinear dynamics) |
| "attenuation algorithm, and others" | more feature-ranking methods: LASSO, mutual information, tree importance, lagged ARX |
| "TIPAC" | not found in the repo: I read it as the whole test pipeline (presets, vp switch, logging, dashboard) |
| "reset trajectory" | the trajectory presets (traj_id / traj_go) |

## Added 2026-10-09 (your mid-run message), now on top

- [x] A. **3-layer controller flight-ready tomorrow**: built in the firmware (vp id, host tests pass), a logging
  preset that records its signals, an analysis script for its flight, and the flash + fly commands in the
  morning pack. This outranks items 4-9.
- [x] B. (S10X swing row replayed, docs/flights/plots/2026-10-09-features/b_s10x_replay.md: beats row 17 on 32/59 logs but blows up on every injected log even with all ext slots masked, so the basis != 0 path is the cause, not the features; NOT in the pack, row 17 stays) **Not limited to the current variants**: new variants are allowed where the data says so (for example a
  prediction-error / composite law, or a swing-phase feature row). Each new one gets a vp id, host tests and
  an offline replay score before it goes into the morning pack.
- [x] C. (ead6bd0: 7 sources link-checked in docs/design/vp16-research.md; R8 had no URLs, rows dropped; no flown MRAC-vs-PID rope result found) **Research: MRAC flown in real systems**, especially multirotors carrying fixed or changing payloads
  (pick-up, drop, slung, offset). What was built, on what hardware, against which baseline, with what gain.
  VPS workers, every claim checked against its link.

- [x] D. (part 2 9016355, d_gain_range.md: the D gain only moves with p_forget; g20 + D p_max 20 p_forget 5 gives median cancel +0.04/+0.02, phase -69 -> -50 deg, better than row 17 on 44/59 pitch and 50/59 roll logs, but worst on the f17 load-removal logs (-8.75); next: vp row 19 with these values, flown after row 17. part 1 ccecde4: gamma_c 2..20 replayed on every log, L2only pitch cancel flat -0.07..-0.09, roll best g20 -0.04, phase -99 -> -69 deg; the gain is not the limit, row 17 keeps g8; D self-tuning gain: no effect. Part 2 open: a gain that actually moves) **Tune gains, weight limits and parameters** of the 3-layer stack and the final variant from the replay
  and the simulations (not by guess), and **add a real-time gain-finding mechanism**: adaptation gains that
  tune themselves in flight (I read this as a time-varying, self-scaling adaptation gain such as a
  least-squares / covariance gain with bounds, so you do not hand-pick gamma; the PID gains stay fixed). Tested
  offline on today's logs and in the sim before it goes into the firmware.

- [x] E. (14 rope logs, docs/flights/plots/2026-10-09-features: tilt-driven pendulum observer, tension x swing, omega x v and Euler p*r add <= +0.005 LOSO R^2; the MEASURED swing phase acc_bpq is #1 on pitch (5/5 methods) and top-5 on roll; all-feature R^2 pitch 0.39 / roll 0.31, so most of the disturbance is not linear in these signals. Next: acc_bpq + angacc + thrust row = item B) **Physics-structured cross-coupling features** (your 2nd mid-run message): build the regressor from the
  rigid-body laws (Newton-Euler momentum balance) instead of generic bumps. Candidates, each with its physical
  cause: gyroscopic rate products p*q, q*r, p*r (omega x J omega); offset-CG torque r x m(f - g) = accelerometer
  specific force f_x, f_y, f_z and gravity-in-body sin(theta), cos(theta)sin(phi); thrust x CG offset
  (collective u_z times a constant); slung-load cable torque (swing angle and rate, pendulum momentum exchange).
  Test: rank them with feature_id (SINDy with a physics library) on the rope and arm Delta_hat, then replay the
  winners as a new feature row (vp id) against vp13. Literature check (physics-informed / Lagrangian regressors
  in adaptive control) by a VPS worker.
- [x] A1. 3L-v2 law in firmware: L2 composite + D self-tuning gain, off by default, host tests 28/28 (f00412a).

- [x] A2. vp rows 16-18 + host table test (daf790b); log group mrac_3l + adaptive_review "3L-v2 prediction error"
  section (18ec159); stream_log frame vp16_frames.md (2aa54c6); campaign rope570_3l.yaml, rows 17 -> 16 -> 18, 20 s
  hover each (910bbb8). Morning: flash (`python -m ground_station.flashtool.rebuild_and_flash --yes`), Keil watch
  vp_id = 17 and check vp_active = 17, PID hover on the rope first, then the campaign. Not flown, numbers PROPOSED.

## Checklist, priority order

- [x] 1. **Circle preset stays in place.** DONE: x/y fence removed from Traj_Check (floor, ceiling, speed and range checks kept); scratch harness: circle and figure-8 from x=-64, y=116 cm now accepted, floor/ceiling/speed still refused. Needs a flash. Likely cause: the preset soft-fence check refuses (0xEF). The circle reaches
  2 x radius toward -x, and the drone started at x = -64 cm (limit -130 cm). Remove the x/y fence from the preset
  check as you asked; keep the speed, range and floor checks. Add host tests.
- [x] 2. DONE (6fbf00a, ea809a8): campaign_capture's 4 failures were the test (set sized for STRUCT6; now from the built
  feature count, 0 fail); Traj_Check unused xm/ym removed. Checked OK: rope570_3l injects (controller mrac -> 1), vp table
  host test in both builds, flashtool 140 pass, mrac_3l / vp16_frames names all in source. Watch: (a) the local axf
  (10-08 18:08) predates f00412a, so mrac_3l symbols resolve only after the morning flash rebuilds it; (b) the in-flight
  safety fence (|x| 1.6 m, |y| 2.0 m: push back, land after 2 s or 0.3 m over) still applies to presets, so start
  circles near the origin. **Bug hunt on the test pipeline.** Look for bugs that could block tomorrow: presets, vp switching,
  inject mask, logging var lists, dashboard, flash tool. Fix them or list them.
- [x] 3. **Replay all of today's logs** through adaptive_review. DONE (20 logs, commit 653288e): [meta.md](../flights/plots/2026-10-08-meta/meta.md). Measured: no variant cancels the swing (pitch/roll cancel -0.15..+0.15, phase -80..-157 deg) while a perfect estimator behind a 16 rad/s filter would cancel 84-91%. The gap is the lag of tracking-error learning, not the features. Static trim is the drone's own CG plus load (pitch -0.09..-0.11 u_max on every load, roll flips sign rope +0.05 vs arm -0.06).
- [x] 4. (feature_id.py + test committed; rankings above) **Feature identification**: SINDy plus the other methods on the disturbance estimate of each axis. Which
  features (rate, tilt, accel, delayed states, swing phase) explain it, for the rope and the arm load?
- [x] 5. (covered by the item 3/B/D replays on every log: [meta.md](../flights/plots/2026-10-08-meta/meta.md)
  vp13 and the 2026-10-08 variants cancel nothing, tracking-error lag; [d_gamma_sweep.md](../flights/plots/2026-10-09-features/d_gamma_sweep.md)
  composite L2 gamma 2..20; [d_gain_range.md](../flights/plots/2026-10-09-features/d_gain_range.md) L2 + D self-tuning
  gain, the first positive median cancel; [b_s10x_replay.md](../flights/plots/2026-10-09-features/b_s10x_replay.md)
  swing/accel feature row blows up on injected logs. Q19 (accel features): not with the basis != 0 path; Q20 (omega_u):
  not replayed separately, the L2 gain is the lever that moved) **Offline replay of the adaptive laws** (vp13, vp16 candidates, accel features, higher omega_u, composite).
  Score cancel ratio and swing phase. This answers Q19 (accel features) and Q20 (omega_u) with data.
- [x] 6. ([sim_rope.md](../flights/plots/2026-10-09-features/sim_rope.md), sim, 5 seeds: on the 570 g / 0.43 m rope,
  rows 17 and 19 cut hover RMSE 0.24 -> 0.08 m, almost all altitude (PID sags 0.22 m); swing not damped (tilt +1-2 deg);
  rope cut at 10 s: no gain, row 19 tilts 20 vs 14 deg. CIs cross zero. Arm offset not simulated here) **Closed-loop simulation** with the rope pendulum and the arm offset. Test and iterate your 3-layer design
  (layer 1 is built, layers 2 and 3 are not) against vp13 and PID.
- [ ] 7. **Research meta-analysis** by VPS workers, gated by link checks: slung-load adaptive control that was
  flown, SINDy for drones, composite adaptation, saturation hedging, swing damping from IMU only.
- [ ] 8. **Public datasets** (for example Neural-Fly): download and run the variants and the feature ranking on them.
- [ ] 9. **Final boss variant**: design doc, firmware, host tests, Q20-Q22 decided from the data above.
- [x] 10. ([morning-2026-10-10.md](morning-2026-10-10.md), 5e979d6) **Morning pack**: test plan for tomorrow (flight order, var list, flash command) and a short report.
