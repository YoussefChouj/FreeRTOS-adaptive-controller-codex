# Task p2-a2-lit: Literature review for phase-2 adaptive controllers and sysID (quoted, verified sources)

<!-- Model: agy:gemini-3.1-pro-high on VPS (web access). Research only. -->

## Goal
`docs/research/phase2_lit_review.md` exists. Every cited work is one you opened this session: give its URL and a verbatim quote of at most 15 words. Each topic below ends in a concrete "bench candidate" spec (equations and tunable knobs) that we can implement in our Python quadrotor-attitude bench.

## Why (what our bench already found; use it to judge relevance)
- The bench is a 6-DOF quadrotor attitude/rate simulator. Controllers see estimated states and are scored on true states.
  - Every controller gets the same CMA-ES tuning budget.
  - Test families: steps, sines, chirps, disturbances, saturation. Held-out families: ground_effect, motor_loss, combo_unseen.
- Results so far:
  - A retuned cascaded PID is the baseline to beat.
  - A saturation-aware MRAC and a 3-layer MRAC (physics basis + reactive + predictive spectral layer) are ~6% better on the test split, but NOT on the held-out families.
- Diagnosed failure modes:
  - Reference-model mismatch of 18-27%: the plant cannot follow the chosen reference model.
  - Routing concentrated adaptation gain (gamma 2-3 vs 0.36) and hurt robustness.
  - The predictive layer does nothing on steps.
- Hypothesis under test: different physical features dominate at different spatio-temporal scales. Features identified per scale (e.g. by SINDy) and used as adaptive regressors or feedforward should give large gains.

## Topics (one section each)
1. **Tansel Yucelen's work** (USF, LACIS http://lacis.eng.usf.edu/).
   - Methods: set-theoretic MRAC (error restricted to a user-defined set; generalized restricted potential functions); performance recovery / low-frequency learning; command governor; derivative-free and modification-based MRAC; his papers with Arabi, Gruenwald, Haddad, Calise.
   - Also open the YouTube playlist https://www.youtube.com/playlist?list=PLW4eqbV8qk8b7WLDXM2mTFZDSbm685Rjy and list the lecture titles in order.
2. **Closed-loop reference models (CRM)** and reference-model modification (Gibson, Annaswamy, Lavretsky; Lavretsky & Wise book). These relate directly to our 18-27% mismatch.
3. **Composite / concurrent-learning MRAC** (Chowdhary; Lavretsky composite adaptation). When do they give parameter convergence without persistent excitation?
4. **Adaptive control barrier functions** as a safety filter over an adaptive controller: Taylor & Ames aCBF, robust aCBF (Lopez & Slotine), and CBF plus MRAC on quadrotors. Include the QP form and its computational cost.
5. **SINDy family**, offline and online:
   - SINDy (Brunton 2016), SINDYc, ensemble SINDy, weak SINDy, and online / recursive / "rapid model recovery" SINDy.
   - SINDy-MPC.
   - Multiscale SINDy (sampling strategies, embeddings); multi-resolution DMD / wavelet-based multiscale ID.
   - SINDy or sparse ID applied to quadrotors or aerial vehicles.
   - Give the STLSQ algorithm and the threshold/library choices used in practice.
6. **Neuroadaptive control:**
   - RBF-NN MRAC (Sanner & Slotine; Lewis; Ge): how many centres, and how they are placed.
   - Physics-plus-RBF hybrids.
   - Deep MRAC (Joshi & Chowdhary DMRAC), Neural-Lander, Neural-Fly (O'Connell 2022, domain-adversarially invariant meta-learning, trained offline and adapted online), and Lyapunov-based DNN adaptive control (Patil, Dixon).
7. **Quadrotor aerodynamic and physical features, and the timescale each dominates at:**
   - rotor drag, blade flapping, induced drag
   - ground effect (Cheeseman-Bennett)
   - motor/ESC first-order lag
   - thrust loss vs battery voltage sag
   - gyroscopic and inertia coupling
   - propeller wash and interaction
   - vibration bands
   Tabulate feature / typical timescale or frequency band / axes affected / source.
8. **Trajectory-preview feedforward** combined with adaptive control: differential-flatness FF (Mellinger & Kumar), preview control, and FF plus MRAC interactions (FF that keeps the adaptive law from learning the reference).

## Per cited work
- Authors, year, venue, and DOI or arXiv id.
- The URL you opened, and one verbatim quote of at most 15 words.
- The key equation in plain text or LaTeX (e.g. the adaptation law).
- What problem of ours it addresses (from the list above).
- Implementation complexity (S/M/L).
If you could not open a source, mark it `UNVERIFIED` and do not quote it. Never invent a DOI, a quote or a result.

## Deliverables
1. `docs/research/phase2_lit_review.md`, at most 700 lines: sections 1-8, then:
   - A **Bench candidates** table: name / law (equations) / knobs to tune / expected effect on our failure modes / risk.
   - A **Pre-registration suggestions** list: for each candidate, a falsifiable prediction on our bench.
2. `.agent-ops/out/p2-a2-lit.md`, a digest of at most 30 lines: STATUS, the number of works cited (verified vs UNVERIFIED), the top 8 candidates in priority order, and the key risks.

## Constraints
- Research and writing only. Create only the two deliverable files; edit nothing else.
- No firmware, no builds, no hardware, no port 8081.
- Foreground commands only. Do not commit (the runner collects your worktree).
- Keep the unique-line ratio high: no repeated boilerplate paragraphs.

## Verification
- `wc -l docs/research/phase2_lit_review.md` -> at most 700.
- `grep -c "http" docs/research/phase2_lit_review.md` -> at least 30.
- `grep -c UNVERIFIED docs/research/phase2_lit_review.md` -> report it in the digest.

## Out of scope
- Writing controller code or running the bench.
