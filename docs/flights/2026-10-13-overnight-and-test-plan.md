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

An aborted first F4c attempt is in `logs/aborted/` (not for analysis). F3/F1/F2 hovers were not flown yet.
Uses: fit the plant on one flight and validate on the others. The pack comparison asks whether the response changes
with battery voltage. F4b camera stats need masking where detection drops out.

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
