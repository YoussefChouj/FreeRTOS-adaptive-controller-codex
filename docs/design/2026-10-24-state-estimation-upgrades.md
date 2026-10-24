# Spec: state-estimation upgrades (Part A firmware) and the drift NN done properly (Part B offline)

Status: SPEC, written 2026-10-24. Part A is built today in its own worktree, flag-gated, default off, so it can be
flashed with the all-loop build without changing anything until a flag is set. Part B is offline research + code,
not needed for today's flights. Both are built by agents the operator launches by hand.

## Part A: estimator upgrades (firmware)

### Step 0, map before building (report section 1)

For each item below, write one table row: does the firmware already do it, where (file:line), and how. The files:
API/ekf.c, ekf_of.c, gyro_filter.c, imu_update.c, calib.c, Accel_Calibartion.c, thrust_estimators.c,
time_estimate.c, Filter.c (and their .h). A grep on 2026-10-24 found hits for still / bias / temperature words
in calib.c (25) and ekf_of.c (4): read them first. An item that already exists is not rebuilt; the row says what
is missing, if anything.

### Items (build order = value for today, cheapest first)

| # | Item | What it fixes | Evidence before flying (offline, logs) |
|---|---|---|---|
| A1 | Gyro bias learned at rest (stationary detector: gyro and accel variance under a threshold for N s, disarmed) | heading creep | bias estimate vs. a still-on-pad log segment |
| A2 | Notch filter at the motor frequency on the gyro (fixed from the logged PSD; dynamic tracking of motor command optional) | vibration in rate and flow de-rotation | gyro PSD before / after on logged raw gyro |
| A3 | Optical flow de-rotation with the gyro, time-aligned to the flow sample | false speed when the drone tilts | flow vs. gyro correlation drops after de-rotation |
| A4 | Camera fix applied at the frame time (state history ring buffer, update the delayed state, propagate forward) | 330 ms late fixes in camera mode 3 | replay: position error at fix time vs. at arrival |
| A5 | Rotor drag as a body-velocity measurement (a_xy_body = -k v_xy_body, k fitted from logs) | slow hover drift | fit residual on hover / slow-flight segments |
| A6 | IMU temperature compensation (bias vs. temperature, fitted from warm-up logs) | warm-up drift | bias-vs-temperature fit; needs a logged IMU temperature: check |
| A7 | 6-position accelerometer calibration (ground procedure tool + stored offsets / scale) | tilt offset | before / after residual on the 6 poses (operator runs it at the lab) |

Rules for every item:
- One flag per item, default off. With all flags off the estimator output is **bit-identical** to today's
  (equivalence test first, like the MRAC EQUIV test).
- Every threshold, coefficient or frequency comes from a log, written next to it with the log name, or stays
  PROPOSED with a value that leaves the item inactive.
- The camera delay of A4 uses the measured delay; 330 ms is stale (tracker changed 10-22): read it from the
  10-24 deck camera.args, never hard-code it.
- Numbers: start 8 above the next free CMD / VAR / field number (the MRAC agent takes the next free ones). List
  every number taken in the report.
- Only the estimator files above, plus your own marked section of ground_station/platform/firmware_contract.py and
  offline scripts under tools/est_*.py. No MRAC files, no campaign_fly.py, no decks.

### Flights (A/B, added to the deck by the CEO after review)

Per item that passes its offline check: hover-and-box script, order off, on, on, off (flag via VAR write on the
ground). Metric: drift against camera truth4 (cm/min) and the item's own signal (PSD, bias, delay residual).
Pass marks PROPOSED against the same-day off arm.

### Done means (Part A)

Branch `est-upgrades`, one commit per item, not merged, not pushed. Equivalence test green, Keil clean (or the
worktree limit reported). Report docs/design/2026-10-24-state-estimation-upgrades-report.md: the step-0 map, per
item what is built + the offline evidence + flag + numbers taken, PROPOSED values, what is left.

### Prompt A (paste as is)

> Before you start, the operator has run, in D:\Projects\UAV_lab\FreeRTOS-adaptive-controller-codex:
> `git worktree add ../fc-estimator -b est-upgrades main`. You work ONLY in D:\Projects\UAV_lab\fc-estimator
> (branch `est-upgrades`; stop if it is not). Read AGENTS.md and docs/firmware-table-pattern.md, then build
> docs/design/2026-10-24-state-estimation-upgrades.md Part A: step 0 (the map) first, then A1 to A7 in order, one
> commit per item. Flight logs are not in git: read them from
> D:\Projects\UAV_lab\FreeRTOS-adaptive-controller-codex\logs, read-only; never write in that checkout. Rules: never flash, never touch the 8081 service or a real drone, do not merge or push; only the
> files the spec gives you (another agent is building MRAC layers at the same time); stage by path, never
> `git add -A`; no invented numbers (from a log, or PROPOSED and inactive); all flags off = bit-identical output,
> test that first; run only the tests of the files you change plus a Keil build at the end. Blocked more than 20
> minutes on one item: write it in the report, go on. Finish with the report the spec names and a 10-line summary.

## Part B: drift NN, done the way the field does it (offline)

**Superseded 2026-10-24 by docs/research/2026-10-24-drift-nn-v2-spec.md (prompt 4 in
docs/agent/2026-10-24-agent-prompts.md). Do not use prompt B.**

### Where we are (measured)

The 10-13 TCN: 15.7 cm p90 on its training flights, 260 cm p90 on a held-out flight
(docs/research/2026-10-13-sota-extpos-nn-plot.md:164-165). A 17x gap = it memorised the training flights. No
leave-one-flight-out (LOFO) result exists. So it is not good enough for the live loop yet; it may run in shadow.

### What the literature does (agent: verify each citation and quote the key result)

| Work | Idea we copy |
|---|---|
| Brossard, Bonnabel, Barrau 2020, AI-IMU dead-reckoning (IEEE T-IV) | the NN does not output position; it tunes the EKF's noise covariances |
| Brossard et al. 2020, gyro denoising with deep learning (RA-L) | denoising is its own small network, judged alone, before any drift work |
| Liu et al. 2020, TLIO (RA-L) | NN predicts displacement over a window + its covariance; an EKF fuses it as a measurement |
| Cioffi et al. 2023, learned inertial odometry for drone racing (RA-L) | for drones, feed thrust / motor commands, not only IMU |
| Qiu et al. 2025, AirIO | body-frame inputs (gravity removed, attitude given separately) generalise better on drones |

### What an expert would do, in order

1. **Fix the test first:** LOFO over every usable flight. Report p50 / p90 per held-out flight. Nothing else counts.
2. **Clean labels:** camera truth4 positions, time-aligned with the measured delay; drop segments with tracker
   loss. Count the flights and minutes per flight type; few flights means a small model.
3. **Inputs:** body-frame gyro + accel (gravity removed), attitude separate, motor commands / thrust; normalised
   per channel with training-set statistics only.
4. **Output:** displacement or velocity over a 1-2 s window **plus a covariance** (TLIO style), never a position.
5. **Fuse, do not replace:** offline EKF replay with the NN as one more measurement, Mahalanobis gate on it.
6. **Denoising apart from drift:** a gyro denoiser judged on attitude error alone; drift judged with and without it.
7. **Ablations:** inputs (IMU only / + thrust), window length, model size, data amount (learning curve: does more
   data still help?).
8. **Live only if:** LOFO p90 of OF-EKF + NN beats OF-EKF alone on the same held-out flights, and the NN's
   covariance is consistent (NIS inside its bounds). Until then: shadow only.

### Prompt B (paste as is; offline, any time)

> Before you start, the operator has run, in D:\Projects\UAV_lab\FreeRTOS-adaptive-controller-codex:
> `git worktree add ../fc-driftnn -b drift-nn-lofo main`. Work ONLY in D:\Projects\UAV_lab\fc-driftnn.
> Read AGENTS.md, docs/research/2026-10-13-sota-extpos-nn-plot.md and
> docs/design/2026-10-24-state-estimation-upgrades.md Part B, then do steps 1 to 8 in order. Offline only: no
> firmware, no 8081, no drone, no flash. Flight logs are not in git: read them (read-only) from
> D:\Projects\UAV_lab\FreeRTOS-adaptive-controller-codex\logs. Training is heavy and the laptop's power is fragile: run
> it after the lab or on the Kaggle lane (tools/kaggle_nnio). Find the existing drift NN code and logs from that research doc; new code
> goes under ground_station/analysis/drift_nn_lofo/ with tests. Verify every citation in the table (title, venue,
> year, key result, quoted) before using it. No invented numbers. Commit per step, stage by path, do not push.
> Finish with docs/research/2026-10-24-drift-nn-lofo-report.md: LOFO table, ablations, the go / no-go for the live
> loop against step 8, and a 10-line summary.
