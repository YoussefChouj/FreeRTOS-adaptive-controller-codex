Status: DONE (code and tests done, acceptance passes; the gate fails on size only, see below)
Commits: d4d6b35 - cte(wp34): log replay of MRAC variants / autotune / livetune J floor, thrust-estimator tilt + NaN fix
  with host test; 6392c14 - cte(wp34): close the input stream when the replay driver cannot open its output (on wp/34)
Gate: GATE FAIL: size (scope PASS 17 files, clang-tidy PASS 3 files, ruff PASS 11 files, pytest PASS 15). size 1464/200:
  5 scripts + C driver + 6 tests + firmware fix cannot fit 200 lines.
Verification: python -m pytest -q ground_station/analysis/tests -> "326 passed, 5 skipped, 40 warnings in 265.21s"
  (the 5 skips are pre-existing; all 16 new tests run, none skip). Thrust host test (built by
  tests/test_thrust_estimators_host.py): "1 passed in 2.65s" = 42/42 C checks. On the old code it failed 3 checks.
Worker rounds: CTE, effort xhigh (no manager, no workers).
Outputs: docs/analysis/log-replay-2026-10-04.md (110 lines), docs/analysis/thrust-estimation-audit.md (53 lines).
Deviations / open questions:
- Logs are gitignored and live only in the main checkout. Every script takes --root; the numbers come from
  --root ../FreeRTOS-adaptive-controller-codex. Corpus: 374 logs (306 sessions + 68 VOFA); 34 have an airborne
  span >= 3 s. logs/campaigns holds one yaml; docs/flights holds only the ledger.
- Firmware edit (thrust only), proven by the new host test: imu_total was T/cos(tilt), now m(a_z + g cos_tilt).
  mass_hat read m cos(tilt). A NaN acc_z gave a NaN imu_total, now 0. NOT Keil-built (forbidden): CEO build check
  needed. Effect on the logged hovers is 0.02-0.08 % (tilt <= 2 deg).
- NOT fixed, needs a firmware WP: MRAC reads imu_data.pit/rol as radians, but they are degrees
  (mrac.c:318, 749, 750). In the replay this gives 510 would-be simplex trips in 1559 s on degree-fed input and 0 on
  radian input. Simplex mode 1 at default limits would freeze MRAC above 3.14 deg tilt. The V3 RBF grid is off
  either way (degrees: spiky; radians: constant at sum phi 3.07). Fix the units and rescale the grid before battery 4.
- MRAC replay is open loop (the plant does not respond). The OFF replay matches the logged u_ad with median corr
  0.84-0.96; it is low on older-firmware logs. Logs are 50-100 Hz, linearly resampled to 200 Hz.
- The flight-plan rules mis-fire on the logs. "|u_ad| > 0.5 |u_nom| for 1 s" fires on 24-30 of 37 hovers for every
  variant, today's law included (hover u_nom is tiny). "Theta growth > 5 s" flags slow convergence at gamma x0.25.
  PROPOSED floors are in the doc. V2 never acts (mixer deficit 0 on all 32 logs with Throttle_out). 3L at 2.2
  drifts (roll |Theta| rises for up to 43 s). PR/3L knobs are PROPOSED values.
- Autotune: no log has a SysID run. CLI: 243 "no multisine" and 48 "too short" refusals, plus 3 crashes
  (ValueError NaN in autotune/frf.py find_start, outside scope; PROPOSED guard: raise IdError when Des has < 3
  samples). Natural hover coherence median is 0.20-0.35 (< 0.6). Fitted delays are 59-76 ms vs SysID 12-15 ms
  (closed-loop bias). The 17 "proposals" sit on the +-30 % bound: do not apply them.
- Flown gains: pid.c before 2026-09-29 has no PID_ROW table, so the autotune sweep uses today's rows. livetune_floor
  infers the gains from the last pid.c commit before each recording (19 logs) and labels them "inferred".
- Live-tune J floor: within-flight CV 0.32 roll / 0.24 pitch, between 17 equal-gain flights 0.17 / 0.24; that is
  5-6x min_gain 0.05. J_osc (a >= 10 Hz line of about 17 deg/s on roll) is over half of J. PROPOSED: score each
  candidate on >= 3 windows, and lower w_osc or raise f_c. Re-measure the floor on the first excited flight.
- Thrust open items (audit doc): empirical[] shows 17-20 N while disarmed (call-site fix in TASK/); the LUT has no
  voltage term (-84 PWM ticks/V); mass 0.9885 kg (firmware) vs 1.2961 kg (sim) vs 1.49-1.63 kg (LUT) is
  unreconciled. Weigh the airframe.
