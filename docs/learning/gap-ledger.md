# Knowledge gap ledger

Resume point for knowledge-limit sessions (survives context compaction).
Scale: 1 heard of it · 3 could implement it · 5 could defend the tradeoffs to a senior engineer.
Target (2026-10-15): general, research-leaning; applying to drone algorithm / control internships.

## Round log

| # | Date | Track | Question | Score | Break point |
|---|---|---|---|---|---|
| 1 | 2026-10-15 | A×D | MRAC robustness switches (projection, dead-zone, e-mod, σ-mod, normalisation) | 2/5 | No unifying concept (parameter drift without PE); e-mod and normalisation explained wrongly; P read as a physical matrix |
| 2 | 2026-10-15 | A×D | 60 s hover in steady wind: what θ does, why hover is worst, fix | 1.5/5 | Bias weight right; missed unidentifiable weights drifting (no PE at hover); no fix (answer: inject excitation). Good instinct: chattering at the dead-zone edge |

Taught in round 2 (re-test): V = eᵀPe + θ̃ᵀΓ⁻¹θ̃ and why V̇ has no θ̃ term; Q as a chosen weight; PE as "two apples on one scale";
σ-mod steady-state error e ∝ σθ̂ (door-spring); normalisation as NLMS (Γ=2 numbers: 250x vs 10x).
Firmware facts: dead-zone is a hard step (`API/mrac.c:1282`, on by default `API/mrac.h:80`); hard freeze trips on LARGE error (`API/mrac.c:1285`).

## Open gaps (re-test later)

- Parameter drift + persistent excitation (PE): the reason robust modifications exist. Rohrs counterexample (IEEE TAC 1985).
- Disturbed Lyapunov derivative V̇ = −eᵀQe + 2eᵀPB·d: negative only outside a ball, θ̃ unconstrained inside it.
- e-mod (Narendra & Annaswamy 1987): the leakage is scaled by ‖eᵀPB‖ and vanishes as e→0; it is not "updates scale with e".
- σ-mod cost: biases θ even with zero disturbance; `sigma_prior` pulls toward a prior instead of 0.
- Normalisation: divides the update by 1+‖φ‖² so large regressors cannot produce huge steps; not about float32 precision.
- P solves AmᵀP + PAm = −Q: a design choice, not physics.
- A rigid body's body-frame inertia is constant; payload changes it, attitude does not.

## Strengths seen

- Projection = bound θ inside a known physical range (correct).
- Dead-zone = stop adapting inside the noise band (correct).
- σ-mod = forgetting toward zero (mechanism correct).

## Next

- Round 3 (waiting for an answer): after the 60 s hover the rate-feature weight has drifted; predict what the first fast roll
  command does, and say which switch limits the damage most and why.

## 2026-10-17 lab-prep quiz (strategy)

- Q1 (stream bandwidth): partial. Sees the link as capacity, not integrity: dropped frames poison every flight's data.
- Q2 (fair controller comparison): good, about 2/3. Missed frozen firmware and gains, same MRAC setup and stream frame, order effects.
- Q3 (judging a shadow controller from logs): partial, 4/10. Got: replay the logged inputs through the controller offline
  and compare its output with the logged one. That checks the code, not the flying. Missed: a fitted plant model to
  simulate the counterfactual flight, and the trap that the shadow's internal states (ESO, integrator) ride the PID's
  trajectory, so its output is off-policy. Break point: replay as verification vs replay as counterfactual.
- Q4 (fitting the plant from PID-flown logs): good, 6/10. Got: the fit sees the closed loop (PID + plant), and
  the fix is an injected identification signal (chirp / SysID covering the frequency range). Missed the mechanism:
  under feedback the input u is computed FROM the noise and disturbance, so u and the noise are correlated and
  least squares is biased (it can "find" the PID's inverse). The fix works only because the dither is independent
  of the feedback: inject it at the rate setpoint (the repo's SysID_Update) and use it as the instrument (IV/PEM).
  Trajectory maneuvers (steps, circles, figure 8) are valid reference excitation but slow: they shake the position
  loops near 0.1-1 Hz, while the rate dynamics (motor lag tens of ms) need content out to about 5-10 Hz.
  Break point: knows WHAT to add, not WHY the plain fit is biased.
- Q5 (can one 2-min hover fit both c0 and c1 of u_hov = c0 + c1*(V0 - V)?): partial, 5/10. Got: the data must span
  the pack's voltage range (full to 20-30%). Missed: a 2-min flight spans only part of it, so c0 and c1 are nearly
  collinear (a short V range lets the line pivot). Misplaced "excitation" on the throttle: the regressor that must
  vary is V, steady hover is the ideal data for u_hov. Also V is not exogenous: throttle sags V through the pack's
  internal resistance (the Q4 feedback trap again), which heavy filtering breaks. Break point: identifiability =
  regressor spread, and which signal is the regressor.

## 2026-10-18 big-picture strategy

- Q1 (one lab result for the defence, and the examiner's first attack): partial, 4/10. Got: the strongest claim is
  generality, the adaptive (MRAC) layer improves several baseline controllers, not only PID. Missed: no metric
  ("improvement" in what: camera rms_cm, peak error, control effort?) and no attack. The attacks, in order:
  (1) adaptation or just more gain? A layer that adds effort lowers error like a higher PID gain does; the answer is a
  frozen-weight ablation (fly the baseline with the weights frozen at their converged values) and equal tuning effort
  for every baseline; (2) nothing to adapt to: nominal hover has no changed uncertainty, so add a known perturbation
  (payload mass, CG offset); (3) one flight per cell is an anecdote: n >= 3 repeats, interleaved order (battery sag,
  warm motors), effect bigger than tracker noise and flight-to-flight scatter; (4) the cost: margins, drift on long
  hovers (round 3), effort and saturation. Break point: "it improved" vs "adaptation caused the improvement".
- Q2 (MRAC + NN, why both?): 6/10, answered by a scope cut. Got: the NN is out of the thesis, a drift-fix tool, and
  shadow mode keeps it out of the control loop, so no thesis result depends on it (say that last part out loud: it is
  the defence). Then stated the core claim: a configurable MRAC architecture whose layers each adapt to the physics
  that dominates at their own time and space scale, composable or prunable per problem, and controller-agnostic; the
  current three-layer variant is one experiment. Missed: the claim is a philosophy, not yet a hypothesis. "Fundamental
  patterns" is undefined, and no result is named that would prove it wrong. Break point: vision vs falsifiable claim
  (which layer, which time scale, which perturbation, which metric, what counts as failure).
- Q3 (one layer, one perturbation, the result that proves you wrong): 6/10. Got: two good falsifiers, (a) augment a
  tuned and a detuned nominal controller (gain should be big on the detuned one and never negative on the tuned one),
  (b) any scenario where the adaptation does worse than the nominal controller alone; a useful lab perturbation list
  (swinging load at symmetric and offset points, hard hand-drawn trajectories, flight near a wall); multi-metric view.
  Missed: never picked one layer; "scale" not defined operationally. Corrections: scale = frequency band of the change
  relative to the loop bandwidths (vibration 50+ Hz is a filter job, not adaptation; swinging load ~1 Hz, f =
  sqrt(g/L)/2pi; wall/ground effect = space scale, fades within a few rotor diameters; battery sag/mass/CG = minutes,
  classic MRAC home). The body-frame inertia does NOT change with roll/pitch/yaw (rigid body); tilt changes the thrust
  split (cos tilt) and gyroscopic coupling. Many metrics invite cherry-picking: fix the metric set and decision rule
  before flying. Added falsifiers: layer ablation (remove the matched layer, its disturbance should get through),
  layers fighting (pair worse than best single layer), frozen weights match live, weight drift in long hovers, margin
  loss, helps PID only. Best single experiment: injected disturbance at swept frequencies, rejection vs frequency per
  layer set. Break point: "scale" as a feeling vs scale as a frequency band mapped to one layer.
- Q4 (map each MRAC layer to a band + a lab perturbation): 6/10. Got: a coherent block architecture that maps onto
  known theory: block 1 = physics regressor (dynamics terms + RBF), block 2 = gate that detects the active time
  scale and weights block 1 (a mixture of experts over time scales), block 3 = known information as an adaptive
  feedforward. Good reframe: bands live inside block 1, block 2 picks them. Agreed "deactivate" beats "remove".
  Missed: no perturbation per block; how the gate detects scale is undefined ("history buffer, patterns");
  "irreducible whole" conflicts with "configurable/prunable" (resolve: roles fixed, contents configurable) and
  irreducibility is itself the claim ablation tests; reference model (desired behaviour) conflated with feedforward
  (known r-dot-dot through an adaptive inverse model). Risks taught: a gate adapted from tracking error makes the law
  bilinear and breaks the linear-in-parameters Lyapunov proof (keep the gate a known function of measured signals);
  blocks 1 and 3 see near-collinear regressors when x ~ r, so weights can trade along a null direction (the real
  "fight"). Fact: on this branch MRAC_L2_Update is empty and MRAC_L3_Feedforward returns 0 (API/mrac.c:744,748;
  S4 spec unmerged), so only block 1 can fly today. Prior art to cite: Neural-Fly (learned basis + online linear
  weights, wind). Break point: architecture as roles vs architecture as testable mechanisms.
- Q5 (pre-register today's primary metric + decision rule + secondaries): 4/10. Got: repeats (2-3) and sensible
  secondaries (control effort, settling time, stability margin). Missed: no primary metric named; no win threshold
  (what difference counts); n=2 cannot estimate run-to-run spread (need n>=3, interleaved ABAB); "stability margin"
  has no in-flight method yet (needs plant-input injection + loop-break measurement). Model answer: primary = camera
  rms_cm over the steady segment; win = median gain >= max(15%, 2x baseline run-to-run spread), n>=3 interleaved runs
  per arm, fixed before flying; secondaries = RMS control effort, % time saturated, recovery time after a disturbance.
  Fact: API/sysid.c already makes chirp/multisine, but adds it to ONE axis's RATE setpoint (a reference = tests
  tracking, block 3), not the plant input (a disturbance = tests blocks 1-2 and gives margins). Taught: stable experts
  + switching is not stable by itself (needs one common Lyapunov function or a dwell time); a known softmax gate gives
  the common V for free, and its partition of unity keeps the ungated weights reachable. Break point: the decision
  rule as a number fixed before the data.
- Q6 (setpoint vs torque injection, what each measures): 4/10. Got: block 1 belongs at the torque command "where the
  dynamics live" (right point, right reason); honest "I don't know". Missed: what each point measures; block 2 is not
  an outer-loop test. Model answer: rate-setpoint injection = a known reference -> tracking y/r (complementary
  sensitivity T), tests block 3. Torque (plant-input) injection d with u = c + d -> sensitivity S = u/d, loop gain
  L = -c/u (broken-loop method), gain/phase margins, and the plant P = (y/d)/(u/d) (indirect closed-loop ID; d is
  uncorrelated with noise, so it is also the instrument the SINDy fit needs). Blocks 1 and 2 share the torque point;
  block 2 is tested by sweeping the injection FREQUENCY and checking the gate weights follow the band. Ref: Forssell &
  Ljung 1999, Closed-loop identification revisited (Automatica). Taught: online regressor count is bounded by
  excitation (PE rank), collinearity and adaptation speed (lambda_min of the regressor Gram matrix), not CPU; the
  library can be hundreds offline, the active set small online. "Fourier" in random Fourier features = a kernel over
  the state, not a time-frequency band, so RFF is a block-1 basis, never a gate. A small NN in block 1 keeps the proof
  only if its hidden layers are frozen offline and the last layer adapts (Neural-Fly structure). Break point: mapping
  an experiment's input point to the transfer function it reveals.
