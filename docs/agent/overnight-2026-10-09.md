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

## Checklist, priority order

- [x] 1. **Circle preset stays in place.** DONE: x/y fence removed from Traj_Check (floor, ceiling, speed and range checks kept); scratch harness: circle and figure-8 from x=-64, y=116 cm now accepted, floor/ceiling/speed still refused. Needs a flash. Likely cause: the preset soft-fence check refuses (0xEF). The circle reaches
  2 x radius toward -x, and the drone started at x = -64 cm (limit -130 cm). Remove the x/y fence from the preset
  check as you asked; keep the speed, range and floor checks. Add host tests.
- [ ] 2. **Bug hunt on the test pipeline.** Look for bugs that could block tomorrow: presets, vp switching,
  inject mask, logging var lists, dashboard, flash tool. Fix them or list them.
- [ ] 3. **Replay all of today's 22 logs** through adaptive_review. Build one meta table: load x variant x PID.
- [ ] 4. **Feature identification**: SINDy plus the other methods on the disturbance estimate of each axis. Which
  features (rate, tilt, accel, delayed states, swing phase) explain it, for the rope and the arm load?
- [ ] 5. **Offline replay of the adaptive laws** (vp13, vp16 candidates, accel features, higher omega_u, composite).
  Score cancel ratio and swing phase. This answers Q19 (accel features) and Q20 (omega_u) with data.
- [ ] 6. **Closed-loop simulation** with the rope pendulum and the arm offset. Test and iterate your 3-layer design
  (layer 1 is built, layers 2 and 3 are not) against vp13 and PID.
- [ ] 7. **Research meta-analysis** by VPS workers, gated by link checks: slung-load adaptive control that was
  flown, SINDy for drones, composite adaptation, saturation hedging, swing damping from IMU only.
- [ ] 8. **Public datasets** (for example Neural-Fly): download and run the variants and the feature ranking on them.
- [ ] 9. **Final boss variant**: design doc, firmware, host tests, Q20-Q22 decided from the data above.
- [ ] 10. **Morning pack**: test plan for tomorrow (flight order, var list, flash command) and a short report.
