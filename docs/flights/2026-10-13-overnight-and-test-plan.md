# Overnight run 10-12 → 10-13 and test day 10-13: sharpened plan

Status: PROPOSED. Every number here is PROPOSED or SIMULATED until it is measured on a flight.

## Bottom line

Tonight's flights decide what the overnight run can do. The most valuable one is the **stick-pulse flight (F4)**. It gives
the drone's measured response (system ID) and the camera delay. Without it, CMA-ES tuning, LQR, MPC and ADRC have no plant
model to work from, so they would be built on guesses.

## Gaps in the dictated plan (what to sharpen)

| Gap | Why it matters | Fix |
|---|---|---|
| No plant model yet | LQR and MPC are model-based. CMA-ES tuning needs a simulator that matches the drone. | F4 stick pulses tonight, then fit a per-axis model overnight (gain, lag, delay) and validate it on held-out pulses |
| Controller choice is compile-time only | A bad controller in the air means landing and reflashing | Compile one candidate next to PID and add a **runtime PID fallback** switch (RC or command). RAM still holds only 2 controllers. |
| "Automatic tuning" has no cost or bounds | An optimiser running on hardware with no limits can crash the drone | Tune in simulation overnight (CMA-ES). Tomorrow fly the top 2-3 candidates, each gain write approved in chat. |
| Which loop? | Attitude (inner) and position (outer) need different models and data | Start with the attitude angle loop: there is data, it is fast and the camera is not needed. Position loop after fusion is settled. |
| MPC compute cost on the MCU is unknown | It may not fit in the 5 ms tick | Measure cycle count on target first (DWT counter). If it does not fit: shorter horizon, explicit MPC, or host-sim only. |
| "Understand at a deep level" | Plots alone do not explain why | Each experiment gets a review page with 3 named plots and a one-paragraph "what to look for" |

## Tonight in the lab (quick flights, about 60-90 s each, stop between)

| # | Flight | Feeds |
|---|---|---|
| F4 | Stick pulses: about 5 each on roll, pitch, yaw, throttle, camera on | delay xcorr (`--delay-ms`), system ID for all controllers |
| F3 | Hands-off hover, ext_pos mode 3 (in-EKF camera) | first test of mode 3 |
| F1 | Hands-off hover, mode 0 (EKF only) | drift baseline, NN training data |
| F2 | Hands-off hover, mode 2 (offset outside the EKF) | mode A/B |

Log: preset `drift_rc_extpos` plus the RC sticks (`Remoter.*Ctrler`), with live camera truth recorded.

Flown 10-12 (measured from the logs; all ext_pos mode 1 shadow, firmware 91f9d404):

| Log | Pack | slot0 rows (100 Hz) | Sticks min..max | Camera: frames / detections / fixes / gate rej / reseeds |
|---|---|---|---|---|
| `r6_f4_sysid` | P5300 | 30170 | full on all 4 axes | 7590 / 7566 / 5660 / 24 / 0 |
| `r6_f4b_sysid_p5300` | P5300 (same pack, 2nd flight) | 30750 | 2132..3866 all axes | 8200 / 6151 / 4266 / 2056 / 7 (drone left view; locy FB to -362 cm) |
| `r6_f4c_sysid_p4000` | P4000 (fresh) | 27237 | roll/pitch/yaw full; throttle 2132..3347 only | 5720 / 5556 / 4418 / 165 / 1 |

An aborted first F4c attempt is in `logs/aborted/` (not for analysis). F1/F2 hovers were not flown.

**F3 `r6_f3_mode3` (mode 3, P-pack after power-cycle): sudden drift, operator killed the motors.** Measured in the log:
- Before: n_ok stuck at 620, stale = 1. Rejected fixes then agreed with each other, so `n_anchor` counted up 1, 2, 4.
- The rejected fixes had an innovation of about e_x -426 cm, e_y -860 cm. That is larger than the room, so the camera fix was wrong but self-consistent.
- In one slot1 step (20 ms), `locxPID.FB` (= earth_y) jumped from -80 to -937 cm. The position loop then saw an 8.6 m error.
- Tracker: 4760 frames, 4352 detections, 1879 fixes, 407 gate rejections, 3 reseeds.
- Cause (UNVERIFIED until replay): the consistency-checked re-anchor accepted a consistently wrong fix. Nothing bounds
  the size of a re-anchor step. Also, `--delay-ms 0` was used and the camera delay was never measured.

**Overnight item 0 (before anything else, a safety fix; host replay of this log must reproduce the jump first):**
1. Bound every re-anchor step and every fix. PROPOSED: reject any fix outside the room box; a single anchor step may not exceed about 30 cm.
2. On a re-anchor, shift the position setpoint by the same amount, so the controller sees no step (bumpless).
3. In the tracker, send no fixes after a reseed until the track is confirmed.
4. Add a golden test built from this F3 log.

Until then, do not fly mode 2 or mode 3 (mode 1 shadow is safe).

**F2 `r6_f2_mode2` (mode 2, 132.6 s, flown 10-13 at the operator's request): no jumps, but drift-prone (operator fought
it with RC).** Measured:
- Camera healthy: 2570 frames / 2533 detections / 2110 fixes / 26 gate rej / 0 reseeds. Firmware n_ok 2089, n_rej 4,
  n_anchor 2, stale fraction 0.05. |e| p50/p90/max 0.7 / 4.5 / 76.9 cm.
- Max FB step per sample: 2.3 cm (locx), 2.7 cm (locy). So the rate limits held.
- Correction c vs camera-truth position: y `c = 1.10 * pos + 2.2 cm` (r 0.93), x `c = 0.29 * pos - 13.9 cm` (r 0.13).
  On y the camera loop carries nearly all of the position; the OF path barely registers the motion.
- Cause UNVERIFIED: OF y scale, axis swap or sign, or pad-to-EKF rotation, plus `--delay-ms 0`. Mode 3 shares it.
- Overnight item 0 gets step 5: check OF y vs camera y on the F4 mode 1 logs (scale, sign, lag) before any closed loop.

**Quick fix in source (10-13, host C test + twin pass, NOT flashed):** re-anchor refused when |e| > 150 cm
(`EXT_POS_ANCHOR_MAX_CM`, PROPOSED, modes 2 and 3); the mode 3 innovation is capped at `EXT_POS_SLEW_CM` per fix.
Steps 2-4 of item 0 (bumpless setpoint shift, tracker reseed hold, F3 golden) are still open.

**F3b `r6_f3b_mode3` (mode 3 on ELF 6da69207 = quick fix, 194.7 s in mode 3 incl. ground time): no jump, loose tracking.**
Measured:
- Tracker: 4510 frames / 4493 detections / 3472 fixes / 11 gate rej / 0 reseeds.
- Max FB step per 20 ms sample: 9.9 cm (locx), 10.4 cm (locy), i.e. the per-fix cap held. FB range x -127..42, y -59..101 cm.
- Firmware: n_ok 2715, n_rej 749, n_anchor 624, n_disagree 68, stale fraction 0.061.
- |e| p50/p90/max 13.2 / 58.1 / 86.7 cm (F2 mode 2: 0.7 / 4.5 / 76.9). The in-EKF update rejects many fixes and lives on
  capped re-anchors, so the estimate lags the camera. Likely the same y mismatch as F2 plus the 5σ gate on a
  P that is too small (UNVERIFIED; replay item 0 with `replay --fw` on this log).
- First try (WiFi schema timeout + scrcpy window 1366x745, tracker refused the aspect) in `logs/aborted/r6_f3b_mode3_try1`;
  `camera_truth.sh` now pins the window to 1194x672.
Uses: fit the plant on one flight and validate on the others. The pack comparison asks whether the response changes
with battery voltage. F4b camera stats need masking where detection drops out.

## Operator goal for the next lab (10-13, stated after F3b)

Operator: both mode 2 (F2) and mode 3 (F3b) drifted and had to be held with RC. Goal: **drift-free mode 2 and mode 3, and
an NN with a real held-out gain.** Acceptance (PROPOSED):
- Mode 2 and mode 3 each: 60 s hands-off hover, 1 s-mean d60s p90 ≤ 10 cm against camera truth, and stick deflection
  near centre for that window (the RC sticks are logged, so "had to fight it" is measurable as stick effort).
- NN: held-out flight beats the plain EKF on 1 s-mean d60s p90 (item 8 target ≤ 10 cm, camera off).

What this changes overnight:
- Item 0 first: replay F2, F3 and F3b with `replay --fw`, splitting each into hands-off and stick-active segments. Stick
  input moves the drone on purpose, so it must be masked before blaming the estimator, and it measures how bad the drift was.
- Find the y mismatch (F2 `c_y = 1.10 * pos_y`) on the F4 mode 1 logs: OF y vs camera y, scale, sign and lag. A loop that
  fights a wrong-scale estimator is the most likely reason both modes drift.
- Mode 3: why 749 rejections and 624 re-anchors (gate vs P, delay 0). Fix in the twin, then the C, then a golden test.
- Every candidate fix must pass the replay acceptance above before it is flashed.

## Overnight work, in order (each item has a done-check)

1. **Ingest tonight's logs.** flight_review page per flight, camera-truth fit, ledger rows.
   Done: pages exist, numbers come only from the logs.
2. **Delay and yaw.** Delay = stick pulses xcorr → `--delay-ms`. Windowed yaw in live_tracker.
   Done: measured delay with its spread.
3. **Fusion A/B.** Score modes 0/2/3 on F1-F3 against camera truth (rms, d60s p90).
   Done: a table with one recommended mode.
4. **System ID.** Per-axis attitude model fitted on F4 and validated on held-out pulses.
   Done: fit error reported, plus a model file the simulator loads.
5. **Controller framework (firmware).**
   - `CTRL_SEL` compile-time macro selects which candidate is built next to PID.
   - A runtime toggle switches between the candidate and PID.
   - Host golden tests cover each controller.
   - Done: host tests pass, and the build fits the RAM budget (measured from the map file).
6. **ADRC, then LQR, then MPC** (cheapest first).
   - ADRC = extended state observer + PD on the estimated disturbance. Light on model needs.
   - LQR = gains from the identified model.
   - MPC = only if the cycle count fits.
   - Done: each passes the host sim (step plus disturbance) against the PID reference. Results marked SIMULATED.
7. **Auto-tune.**
   - Simulation: CMA-ES on the identified model. Cost = tracking error + control effort + overshoot penalty.
   - Bounds come from today's stable gain range.
   - Output: top 3 gain sets, with simulated step plots.
   - Hardware alternative to try tomorrow: safe Bayesian optimisation (few real flights, explicit safety bound). Notes only.
8. **NN** (camera-off target: 1 s-mean d60s p90 ≤ 10 cm).
   - Train on all camera-truth logs, with a held-out flight.
   - Done: held-out score vs the plain EKF.
9. **PID and MRAC analysis.** log_corpus over all flights. mrac_variants on the replay host. Done: one summary table.
10. **Live monitor tool.**
    - Pick by test on a recorded log: auto-named signals, grouped plots with legends, the window keeps the last flight until
      the next starts, saved layout.
    - Candidates: PlotJuggler (UDP JSON stream), Rerun (pip, per-flight recordings), Foxglove.
    - Wire `stream_log` to forward samples to it.
    - Done: one-command start, layout saved in the repo.
11. **Friction audit** (below). Fix the cheap ones, list the rest.
12. **Tomorrow's run sheet** with stops and per-step "what to look at", plus a HANDOFF refresh.

## Friction points from today (10-12)

| Friction | Fix |
|---|---|
| C golden tests skipped silently (no `math.h` under clang no-CRT), so they looked green | Fixed (shim). Make a skip fail loudly in CI-style runs. |
| Command-table test broke on a new command macro | Fixed. Add "new command → contract tests" to the firmware checklist. |
| Frames `.md` says "regenerate" but names no command | Put the regenerate command in the header |
| Flash refused while uVision was open. Right after closing, the uvoptx write hit a transient lock (Errno 22). | Run-sheet step "close uVision before flash". The tool could retry the uvoptx write for a few seconds. |
| A lingering unrelated hunk (firmware_contract enum, line 529) forces patch-surgery staging, and the CRLF text mode broke the first try | Decide: commit or revert that hunk |
| `OBJ/` build outputs are tracked, so every build floods git status | Decide: untrack `OBJ/` (it is the flash snapshot, so this is the user's call) |
| State file over 4 KB; stale facts in HANDOFF | Trim to the newest; move history to ledger |
| Context ran out mid-task | Bigger tasks start at a fresh context, with a state line written before each long step |

## Tomorrow (10-13) flight sequence, with stops (PROPOSED)

| Block | Flights | Stop: look at |
|---|---|---|
| A. Reference | PID hover 60 s, then pulses | attitude tracking, motor spread, z hold |
| B. Fusion | best mode from item 3, hover 60 s | camera vs EKF drift plot |
| C. ADRC | hover, then pulses; fallback to PID armed | ESO disturbance estimate vs real tilt |
| D. LQR | same | step overshoot vs PID |
| E. Auto-tuned PID | top CMA-ES set, then #2 | simulated vs real step (does the model hold?) |
| F. MPC | only if it fits on target | compute time, constraint activity |
| G. Load | winner + 500 g | disturbance rejection |

## Draft master goal prompt (edit and send)

> Overnight autonomous run, do it all in order, don't stop: `docs/flights/2026-10-13-overnight-and-test-plan.md`
> items 1-12. Rules:
> - Every result is from logs or marked SIMULATED.
> - Commit at each verified item.
> - No flashing of untested controller code. Reflash only reviewed firmware that passes host tests and fits RAM.
> - Never arm.
> - Install the live monitor tool after testing it on a recorded log.
> - Morning deliverables: run sheet + one review page per item + a short brief (bottom line, tables).
