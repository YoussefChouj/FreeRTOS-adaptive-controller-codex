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

## Flown adaptive control on payload multirotors (R8 + inline check, 2026-10-09)

R8 (VPS worker) returned 5 of 12 rows and **no URLs**, so it failed the source gate. Its rows (Invernizzi, Nascimento,
"Crazyflie L1", PCAC) and its history notes (X-15 MH-96, AirSTAR, X-36) are dropped until a source is found. Hanover
is kept, but R8 called it a suspended load and the abstract does not say that. The rows below were checked inline against
the abstract or metadata at the link. "Not stated" means the abstract does not say.

| Source | Load | Law | Rig | Payload change in flight | Result (abstract) |
|---|---|---|---|---|---|
| Maki et al., ICRA 2020, [doi:10.1109/icra40945.2020.9196861](https://doi.org/10.1109/icra40945.2020.9196861) | payloads that move CG and inertia | nonlinear MIMO MRAC, attitude loop | quadrotor + transformable multirotor, flown | yes (title) | stability and robustness confirmed in experiments; no % given |
| Hanover et al. 2021, [arXiv:2109.04210](https://arxiv.org/abs/2109.04210) | unknown payloads, wind | L1 adaptive + NMPC | quadrotor, flown | not stated | >90% tracking-error reduction vs non-adaptive NMPC, no gain retuning |
| Mohammadi & Shahri, ICROM 2013, [doi:10.1109/icrom.2013.6510121](https://doi.org/10.1109/icrom.2013.6510121) | variable payload, battery sag | PD + Lyapunov MRAC auxiliary term, decentralized | quadrotor, experiments | not stated | "efficiency and robustness" validated; no % given |
| Xian, Wang & Yang, Nonlinear Dyn 2019, [doi:10.1007/s11071-019-05283-0](https://doi.org/10.1007/s11071-019-05283-0) | aerial payload transport | nonlinear adaptive | experimental validation (title) | not stated | abstract not retrieved |
| Chen et al., RCAE 2025, [doi:10.1109/rcae66389.2025.11355352](https://doi.org/10.1109/rcae66389.2025.11355352) | payload handling | MRAC vs PID on Pixhawk | 1-DOF gyroscope rig, HIL then hardware | not stated | PID vs MRAC compared experimentally; no % in abstract |
| Sierra-Garcia & Santos 2019, [doi:10.1155/2019/6460156](https://doi.org/10.1155/2019/6460156) | mass tripled, wind | PID + neural mass/disturbance estimators | **simulation only** | yes | not flown, pattern reference only |
| Chen, Li & Meng 2025, [arXiv:2507.15261](https://arxiv.org/abs/2507.15261) | object capture, impact + payload | dual-channel adaptive NMPC | not verified from snippet | yes (capture) | not verified |

Reviews for the swinging-load (rope) case: Estevez et al. 2024, [doi:10.3390/drones8020035](https://doi.org/10.3390/drones8020035);
Omar et al. 2022, [doi:10.1016/j.aej.2022.08.001](https://doi.org/10.1016/j.aej.2022.08.001).

What this means for us (PROPOSED):
- Our layout matches the flown pattern: a fixed baseline (PID/PD) plus an adaptive auxiliary term on the attitude loop
  (Mohammadi 2013, Chen 2025).
- The 293 g asymmetric arm is a CG-offset load, the case Maki 2020 flew. Their MRAC compensates CG position and inertia,
  which supports keeping the bias row and the structured features for that load.
- None of the checked abstracts flew MRAC against a swinging rope load with a % gain over PID. A rope-570 result is
  new ground, which also means there is no published number to aim at.
- Still open: the 5 rows R8 could not source. Respawn only with a "URL per row or drop it" rule.

## R9 (VPS agy, 2026-10-09 night): swing damping, SINDy, composite, PCH, datasets, after the link gate

| question | source (opened) | what the abstract / page says | for us | gate |
|---|---|---|---|---|
| swing damping with no load sensor | [arXiv:2608.18625](https://arxiv.org/abs/2608.18625) Taki, Umemoto 2026 | IMU + throttle only; an EKF estimates the swing with the unknown pendulum frequency as a state; an attitude correction angle dissipates the swing energy; flown across cable lengths and masses | the missing piece in rows 17/19 (sag fixed, swing not damped): a separate swing observer + damping angle, not more MRAC features | PASS |
| SINDy on UAV flight data | [arXiv:2410.11791](https://arxiv.org/abs/2410.11791) Guevara et al. 2024 | SINDy model from experimental UAV data, used inside MPC | supports feature_id's SINDy ranking as a method; no payload | PASS (sample counts R9 quoted not checked, dropped) |
| concurrent learning, flown | Chowdhary & Johnson 2011 (AIAA), R9 gave no link | - | - | DROPPED (no URL) |
| PCH / freeze on saturation | R9 cited arXiv:2404.09217 | that ID is a maths-physics paper (CKP tau function), not flight control | - | FAILED (wrong ID) |
| public dataset | [neural-fly README](https://github.com/aerorobotics/neural-fly) | data "for personal and educational use only", written permission for further use | our use is offline research; only derived tables committed ([nf.md](../flights/plots/2026-10-09-nf/nf.md)), data kept out | PASS |

Bottom line: one new design lead, the IMU-only swing observer (Taki 2026). It is a separate layer from row 17, so it
needs its own design, sim and host tests before any flight: not in tomorrow's pack.
