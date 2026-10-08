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

(pending: R1-R4)
