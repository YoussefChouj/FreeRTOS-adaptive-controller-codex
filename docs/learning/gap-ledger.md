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
