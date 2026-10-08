# vp16 research notes (2026-10-09)

Purpose: angles missed by the vp16 grill (Q1-Q18), before building. Worker digests: `docs/research/vp16/R1..R4.md`
(R1 basis + law, R2 payload + swing, R3 saturation + outer loops + PID coexistence, R4 Chinese RBF literature).
Every claim below cites a page that was opened; abstract-only reads are marked "(abstract)".

## Inline findings (supervisor, from abstracts and metadata)

| # | Finding | Source | Relevance to vp16 |
|---|---|---|---|
| I1 | Pseudo-control hedging (PCH) hides actuator limits and inner-loop dynamics from the outer adaptive loop, so outer-loop adaptation does not learn them; the inner loop is hedged too (abstract). | Johnson & Kannan, "Adaptive trajectory control for autonomous helicopters", JGCD 28(3):524-538, 2005. https://repository.gatech.edu/entities/publication/083eab2e-42b2-474d-a441-666ffb25ef9d | X/Y u_ad feeds a 15 deg lean clamp and an inner attitude loop that is itself adaptive: without hedging the X/Y weights learn the clamp and the attitude lag. |
| I2 | Composite adaptation: weights driven by tracking error AND the prediction error of the measured residual force; flown, with lower tracking error than nonlinear, L1 and INDI baselines in strong wind (abstract). | O'Connell et al., "Neural-Fly", Science Robotics 7(66), 2022. https://arxiv.org/abs/2205.06908 | We already estimate Delta_hat offline; a prediction-error term could fix the -70..-101 deg swing-band phase of tracking-error-only learning. |
| I3 | Gaussian networks for direct adaptive control: a uniform lattice whose size depends on the region, spacing and tolerated error; irregular (wavelet) networks in later work (metadata; exact spacing-width rule NOT confirmed). | Sanner & Slotine, IEEE TNN 3(6), 1992. https://dspace.mit.edu/handle/1721.1/12711 ; Cannon & Slotine 1995 https://www.mit.edu/~nsl/abstracts/neurocomp95-abst.html | Our non-uniform "width = nearest gap" rule needs a check (R1). |

## Worker findings (merged after the URL and quote gate)

Gate (supervisor, 2026-10-09): 34 unique URLs curled. 28 open (200, some only without the proxy); 6 refuse bots
(IEEE x2, Wiley, Hindawi, MathWorks, mdpi: 403/418), 3 dead (kzyjc 502, eeworld, arxiv-vanity). Quotes not re-grepped
against the pages: treat every row as the worker's reading. R2 (gemini) is thin (40 lines); R1 (opus), R3, R4 (ark) are full.

| # | Finding | From | Proposed change to vp16 | Status |
|---|---|---|---|---|
| W1 | Width = 1.0-1.5 x gap to the nearest centre is the usual rule; aim for ~50% overlap | R1, R4 | Keep Q15 (1.0 x gap); check the overlap in the host test | confirm |
| W2 | Raw Gaussians + bias row (not normalised RBF) is right; projection is the main safeguard | R1 | none | confirm |
| W3 | A sigma leak of 0.01 on the bias row slowly unlearns the static trim | R1 | No leak (or 0.001) on bias rows; keep it on bumps | ask |
| W4 | Tracking-error learning integrates the error, so the weights lag a swing by up to 90 deg (matches exp17/exp18: -90 deg). A static map of drone state cannot see the hidden load state | R1, R2 | Add features that move with the swing (body accel, rate) so the weights stay still and Phi carries the oscillation | ask |
| W5 | The u_ad low-pass adds lag (about 16 deg at 0.45 Hz with omega_u 10) | R1, R2 | omega_u 15-20 (exp18 what-if: Re ratio 0.29 -> 0.51) | ask |
| W6 | Adapting while a motor or the lean clamp is saturated teaches the weights the limit, not the load | R3 | Per-axis freeze of Theta_dot while saturated; X/Y: clamp the accel command before the reference model (PCH-lite) | ask |
| W7 | Adaptive bias and PID integrator are two integrators that can fight | R2, R3 | Keep the bias lim at 15%; log both and check for drift | confirm |
| W8 | First flights: shadow mode, reset on arm, gamma ramp, per-axis masks | R3 | Runtime per-axis inject mask (default all on, Q18), Theta reset on arm | confirm |
| W9 | Composite (prediction-error) adaptation and concurrent learning fix the lag and excitation problems, but cost CPU and RAM | R1, R2, R4 | Defer to vp17 | defer |
| W10 | Flown STM32 RBF nets are small (about 5 neurons per axis, no cross terms); an RBF-vs-PID rope-load flight is unpublished | R4 | 288 Gaussians per cycle: measure mrac_cyc; use a shared-width table or a cheap exp if the cost is high | measure |
