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
