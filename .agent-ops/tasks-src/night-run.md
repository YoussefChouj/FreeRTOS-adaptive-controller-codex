# Night run 2026-09-27/28: adaptive trajectory-tracking research (simulation only)

Resume protocol (also after every compaction): read this file, then `.agent-ops/out/night/STATE.md`,
then `tail -n 20 .agent-ops/out/night/ledger.jsonl`. Nothing else is needed. Numbers live in files,
never in your memory.

## Objective

Find the controller that gives this quadrotor the best dense-trajectory tracking across the conditions
it will meet in real flight, in a form I can port, flash and fly next. MRAC is my preferred family
because it is modular and interpretable. You are free to find that a variant, a hybrid or something
else entirely is better. Be ambitious in what you try and strict in what you claim.

"State of the art" here means faithful reimplementations of the strongest published methods, run in
the same simulator on the same frozen benchmark and scored on held-out draws. Published numbers from
other platforms are context only, never a comparison.

Losing is an acceptable result. A win that is not real is not. Depth beats breadth: a few correct,
verified comparisons are worth more than many unverified ones.

## Hard rules

Safety and repo:
- Simulation only. No firmware edits (`API/ TASK/ BSP/ USER/ Global_file/ FreeRTOS/ stm32_lib/ OBJ/`),
  no builds, no flashing, no probe writes. Never arm or spin motors. Never POST to port 8081.
- New code goes in `sim/bench/`. Import from `sim/adaptive_compare/`; don't change its results. If you
  must fix a bug there, show the existing results are unchanged or explain the difference.
- First action: commit this brief unchanged, so the morning review can diff the mandate against the work.
- Commit at each verified milestone with explicit paths (`git add sim/bench .agent-ops/out/night ...`).
  Never stage `OBJ/` or files you didn't write (the tree has unrelated modifications). No remote.
  End commit messages with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

No cheating. Each of these invalidates a result:
1. Frozen benchmark. Scenarios, metrics, scoring, seeds and splits are committed as `bench_v1` before
   any controller is tuned. A later change is allowed only to fix a modelling bug: bump the version,
   log why in the ledger, rerun every controller, keep the old results.
2. Splits. Use tune seeds, test seeds, and at least one disturbance family plus one unseen combination
   that no controller is tuned on. Only pre-registered candidates touch the test split. Report the
   total number of test-split evaluations.
3. Equal effort. Every controller, including PID and every baseline, gets the same tuning optimizer
   and evaluation budget. Also report the firmware PID gains as they are today (what actually flies).
4. Controllers see only what the drone sees. They get estimated states at the firmware loop rates,
   with realistic noise, bias, delay and quantisation, through the firmware filter chain. Commands go
   through the real mixer, PWM limits and saturation. No truth states, no disturbance values, no
   future reference beyond what an onboard trajectory generator would have. Score against true
   states; control from estimated ones.
5. Strong baselines. Implement each baseline to its paper and show one sanity case where the paper's
   claimed property appears before comparing. A weak baseline is cheating.
6. Diverged runs count as failures in every statistic; never drop them. Report medians with bootstrap
   95% CIs, paired on the same draws.
7. Pre-register. Before a hypothesis is tested, write its prediction and kill criterion to the ledger.
   Log every experiment, including failures and abandoned ideas. The report lists all of them.
8. Every number in the report comes from a committed script and config and is reproduced by a clean
   rerun before it goes in. Never write a number you did not measure.

## Phase 0: plant and simulator (do this first, about 1.5 h)

The current sims (`sim/adaptive_compare/sim_axes.py`, `sim_coupled.py`) cover attitude and z only, and
rest on assumed constants (for example yaw K_EFF). Dense trajectory following needs more. Extend
what is right; don't rewrite for its own sake.
- Full 6-DOF rigid body with translational dynamics and the firmware's real control cascade. Inventory
  the firmware read-only (`TASK/StabilizerTask.c`, `TASK/AutoflyTask.c`, `API/pid.c`, `API/mrac.c`,
  `API/controller.h`, `API/ekf*.c`, `API/gyro_filter.c`, `API/sysid.c`, `API/thrust_estimators.c`).
  Mirror loop rates, filter chain, mixer and limits. If the firmware has no x/y position loop, model
  the one you need and flag it as a firmware gap.
- Actuators: motor lag, the 15 ms delay (verify it), PWM 2000–4000 with hover about 2950, battery sag
  (flight 8 sagged from 16.8 V to 14.0 V), per-motor thrust and torque mismatch (flight 8's yaw
  imbalance held yaw U at 450–650 and pinned M3/M4 near 4000).
- Sensors: IMU noise and bias, optical-flow velocity and ToF height with the noise, rate and dropouts
  seen in the logs, through the EKF or a faithful approximation.
- Calibrate against the flight logs in `logs/vofa/` (flights 1–8). Identify what you can (delay, motor
  constants, yaw imbalance, inertia ratios), replay logged commands through the sim, and report the
  fit. If the fit is poor, say so and treat every result as relative only.
- Priors from the previous project (`PREV`, defined in the 3-layer section; read-only). These are
  references too: use them as starting values and verify each one before use:
  - `PREV/docs/sysid_results.md`: per-axis gain, pole and delay.
  - `PREV/docs/motor-thrust-characterization-2026-08-30.md` and `PREV/firmware/inc/thrust_model.h`:
    thrust versus PWM and battery voltage.
  - `PREV/sim/plant.py` and `PREV/sim/models/jx_fly/jx_fly_mujoco.xml`: mass, inertia, arm length and
    drag.
  - Known conflicts, to resolve against this repo's firmware and flights rather than copy: idle PWM,
    yaw-rate PID gains (current values are in `.claude_state.md`, flights 7–8), reference-model
    inertia versus airframe Ixx, and motor spin directions. Record every value you adopt with its
    source.

Deliverable: `sim/bench/`, with `bench_v1` frozen and committed, and a calibration section for the report.

## Benchmark (propose it, then freeze it in Phase 0)

- Trajectories must be feasible indoors with optical-flow positioning. Derive speed and size limits
  from the firmware and logs, and state your assumptions. Include hover, steps, circle and lemniscate
  at increasing speeds, random minimum-snap polynomials, and aggressive yaw during translation.
  Include a dense-waypoint zigzag (my planning used 5 cm spacing at 0.5–1 m/s) and score its
  cross-track RMSE.
- Disturbance families: steady wind, Dryden turbulence and gusts; payload, mass and CoG offset;
  single-motor efficiency loss and yaw imbalance; battery sag; sensor noise, dropouts and delay jitter;
  ground effect near the floor; and combinations. Hold some out as described in rule 2.
- Primary metric: median position RMSE on the test split. Declare the aggregation before tuning.
- Constraints: divergence rate no worse than tuned PID, a saturation budget, and no disturbance family
  worse than tuned PID by more than a declared margin.
- Secondary metrics: attitude and yaw RMSE, max error, control effort, motor-command roughness,
  compute cost.

## Controllers

Tier A (must do):
- Firmware PID as it is today, and PID retuned on the tune split.
- Current MRAC variants (S6, S10, RBF6/12/24) from `sim/adaptive_compare/`.
- The 3-layer idea below: the best version you can find, with ablations of the components it keeps.
- The strongest published baselines that could run on an STM32F407 at the firmware loop rate:
  - INDI, with angular acceleration derived from the noisy gyro.
  - L1 adaptive control.
  - A geometric (SE(3)) tracking controller with a disturbance observer (ESO/ADRC).

Tier B targets MRAC's measured failure modes: coupled yaw got worse under adaptation, and saturation
rose to 5–9%. Candidates:
- Closed-loop reference model MRAC.
- Composite or concurrent-learning MRAC.
- Saturation-aware adaptation (hedging, positive-mu modification).
- Projection-bounded or gain-scheduled variants.

Tier C (only if A and B are done): learned-basis adaptive control (Neural-Fly style), MPC within the
compute budget, and your own ideas.

A new idea counts only if it beats the best Tier A controller on held-out data, and an ablation shows
the new component is the reason.

## The 3-layer idea (a reference, not a spec)

I want you to explore an MRAC whose regressors are routed by frequency content. My earlier planning
sessions sketch this, but they are a starting point and a source of ideas, not a specification.
Nothing in them is locked. The feature counts, the bands, the gate type, the fusion rule, the error
signal and every tuning value are all open: change them, drop them or replace them when the evidence
says so. Find the best version of the idea. If the idea itself doesn't pay off, say so plainly; that
is a useful result.

The core idea, per axis, on top of the firmware PID:
- Layer 1, physics basis: regressors built from the state, the command and the reference model.
- Layer 2, reactive spectral: the frequency content of recently measured telemetry.
- Layer 3, predictive spectral: the frequency content the reference model expects on the upcoming
  path, so adaptation can get ready before the error appears.
- Layers 2 and 3 decide which Layer 1 features adapt, by gating, blending or weighting them. MRAC
  adapts on the result.

Reference material. `PREV` is
`C:\Users\Acer\Desktop\UAV_lab\FreeRTOS---Six_Degrees_of_Freedom _Adaptive_controller\` (note the
space before `_Adaptive`). Read it; never write to it.
- `PREV/.agent_contracts/ADAPTIVE_BASIS_RBF/`: the first design (`spec.md` v1.0 from 2026-08-26,
  `firmware_plan.md`, `risks.md`).
- A planning session on 2026-09-23, "frequency-routed adaptive control", has later ideas. Its summary
  is not at `PREV/sessions_summary/2026-09-23-frequency-routed-mrac-planning.md`. Search for it
  (`frequency`, `routed`, `2026-09-23`), and also check `PREV/.agent_contracts/ROADMAP.md`,
  `PREV/docs/validation/phase0-spec.md` and `PREV/docs/literature-review-findings/`.
- Ideas from that session. They are options, not requirements:
  - Lavretsky-style bases on the states, references and errors; RBF bases.
  - A softmax gate over frequency bands, smoothed by a Kalman filter or an IIR filter.
  - An augmented error that mixes the tracking error with the reference-model error.
  - A physics-based reference model driven through the PID gains.
  - A convex gate in place of the projection clamp.
- That session also ruled out an NN inside MRAC, a concurrent-learning history stack and online gate
  retuning. You may test them anyway if you label them.
- Prior code in `PREV/sim/` (`feature_gating.py`, `adaptive_law.py`, `reference_model.py`, and
  others). Reuse its ideas; verify it before trusting it.
- The firmware's own basis, in `API/mrac.h`, is what flies today.

Yardsticks from the planning. They are not pass/fail gates. Report where your best version lands on
each:
- The reference model tracks the plant within about 10% RMSE and 50 ms lag.
- The gate finds known tones in a synthetic signal.
- RMSE at least 30% lower than PID.
- A dense zigzag (waypoints 5 cm apart, 0.5–1 m/s): cross-track RMSE ≤ 7 cm, and at least 15% better
  than PID alone.

How to explore:
1. Build a simple working version first, then answer the questions that decide everything else:
   - Does frequency routing beat the same MRAC without routing, at equal tuning effort?
   - Does the predictive layer add anything beyond the reactive one?
2. If routing helps, spend the time on the design choices with the largest measured effect. If it
   doesn't, find out why with a short diagnosis, then decide whether to keep going or move the time
   to other controllers.
3. Pre-register every variant in the ledger, like any other hypothesis. At the end, ablate each
   component your best version keeps, to show it earns its place.

## Deployability (every finalist)

Report for each finalist:
- float32 and C89-implementable, with no dynamic allocation.
- FLOPs and memory per step, with an estimated time at 168 MHz against the loop period.
- Number of tuning knobs.
- Stability argument (Lyapunov, projection bounds, or none).
- Behaviour at saturation, and the fallback to PID.
- Whether it fits the `Controller_Update` slot in `API/controller.h`, or which interface change it needs.

Stretch goal: a host-compiled C89 reference implementation under `sim/bench/c_ref/` that matches the
Python version, without touching the firmware directories.

## How to work

- Every hour, re-rank open hypotheses by expected gain × probability / cost. Kill anything that misses
  its kill criterion on the tune split.
- Your role as supervisor: design, hypotheses, reviewing results, verification, the report. Workers
  do implementation from a written spec, sweeps, and literature lookups with quoted sources.
- Worker lanes: VPS agy (default) and free oc workers. Use ark only if its quota has reset (check,
  don't assume). Never use Claude subagents; they bill this plan.
- Run at most 2 parallel workers and 2 heavy sim jobs, because laptop power is fragile.
- A worker's DONE or rc=0 is a claim. Read the raw log tail and rerun the check yourself.
- Don't idle and don't poll. Start background jobs, wait with one background command, and use the
  wait for the next design step.
- At start, request keep-awake.

## Token budget and compaction

- Scripts write full results to JSON and print a summary of at most 30 lines. Read summaries, never
  raw JSON or logs.
- Worker briefs are at most 60 lines and point to files rather than pasting them.
- `.agent-ops/out/night/STATE.md` is at most 80 lines and rewritten at every milestone. It holds the
  phase, bench version, top 5 on the tune split, open hypotheses ranked, running jobs, and next action.
- `.agent-ops/out/night/ledger.jsonl` is append-only, one line per experiment: id, time, hypothesis,
  prediction, kill criterion, commit, bench version, split, controller, config hash, key metrics,
  verdict.
- Update STATE.md and commit before context passes about 150k, so auto-compaction loses nothing.

## Schedule (CST)

| Until | Work |
|---|---|
| ~00:30 | Phase 0 |
| ~03:00 | Tier A |
| ~06:30 | Research loop |
| ~07:30 | Final held-out evaluation, reproduction, deployability |
| 08:00 | Report done |

Start no new experiments after 07:00.

## Done when (all true)

1. `bench_v1`, or a later version with logged reasons, is committed, and the calibration fit is reported.
2. All Tier A controllers are evaluated on the test split with equal tuning effort.
3. The 3-layer idea is evaluated:
   - its best version, compared with the same MRAC without routing;
   - ablations of the components that version keeps;
   - where it lands against the planning yardsticks.
4. Every ledger entry has a verdict.
5. `.agent-ops/out/night/REPORT.md` is committed and contains:
   - First line: the verdict in one sentence.
   - The held-out leaderboard with CIs.
   - A per-family table showing where each controller loses.
   - 3-layer results and ablation, including what was changed from the planning and why.
   - Every hypothesis tried, including failures.
   - Sim-to-real risks.
   - A deployability table.
   - A flight-test plan for the top 1–2 candidates: stages, gains, what to log with `stream_log`,
     and abort criteria.
   - Commands to reproduce every table.
6. REPORT.md is sent with SendUserFile (status proactive).

Or: it is past 08:00 CST, and items 5 and 6 are done with whatever was finished.
