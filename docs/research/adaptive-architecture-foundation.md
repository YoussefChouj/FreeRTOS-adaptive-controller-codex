# Layered adaptive controller: firmware foundation

Status: exploration phase (2026-09-29). This doc fixes the **contracts** between layers so that every
variant the thesis will try (structured, RBF, SINDy library, hybrid, deep, gated, feedforward) is a
configuration of one architecture. It does not fix which variant wins. Numbers marked *measured* come from
the file named next to them; everything else is arithmetic or a hypothesis.

## 1. The idea (operator, 2026-09-29)

Different physics dominate at different temporal and spatial scales. The controller should know which
scales are active right now and weight its model accordingly.

```
                +---------------------------------------------------------------+
 signal bus --> | L1 features: Phi = [struct | rbf | sindy-lib | deep | ...]    |  (what physics could matter)
 (x, xm, e,     +---------------------------------------------------------------+
  u_nom, ref,               | Phi (N)                        ^ group tags
  preview, rpm,             v                                |
  thrust, V...) +---------------------------------------------------------------+
            --> | L2 scale detector / gate: band energies over a rolling window  |  (which scales are active)
                |   -> per group: g_gamma (learn), g_sigma (forget), g_phi (use) |
                +---------------------------------------------------------------+
                            | gates
                            v
                +---------------------------------------------------------------+
                | Output layer = today's adaptive law (unchanged math):          |
                |   Theta' = -gamma*g_gamma * s*Phi/(1+|Phi|^2) - sigma*g_sigma..|
                |   u_ad   = Theta . (g_phi * Phi)   (projection, L1 filter, clamp)
                +---------------------------------------------------------------+
                            | u_ad
 signal bus --> +-----------v---------------------------------------------------+
            --> | L3 predictive feedforward: u_ff from known physics, sensors,   |  (what we already know)
                |   trajectory preview (local/global), thrust, rpm               |
                +---------------------------------------------------------------+
                            -> u = u_nom (PID) + u_ff +/- u_ad (existing sign convention)
```

## 2. Evidence we already have (do not repeat it)

The same idea was simulated on `sim/bench` (task `night-w3`, code `sim/bench/ctrl_mrac3l.py`, verdict
`.agent-ops/out/night/REPORT.md` section "3-layer MRAC"). That version gated only the adaptation gain,
with `softmax(log band energy / T)` over 4 IIR bands of the tracking error, and a predictive gate from
the reference preview. Result, pre-registered, equal tuning budget:

- Unrouted 3L (gates constant) was the best adaptive variant: -0.0046 [-0.0106, -0.0005] vs retuned PID.
- Every routed mode was **worse** than unrouted on tune and test (H6 killed). Predictive over reactive:
  not detectable (H6b killed).
- Diagnosed causes: (1) **gain concentration**: softmax puts the whole budget on 1-2 bands, so tunes chose
  gamma 2.2-3.0 vs 0.36 and u_ad/u_nom rose from 0.085 to 0.23-0.28; (2) the gate read the tracking error,
  which was mostly **reference-model mismatch** (18-27% RMSE), not disturbance; (3) the predictive gate
  had **nothing to act on** for steps (zero preview acceleration).

This is useful: it tells the foundation which knobs must stay open (gate target, gate normalisation, gate
input signal) and it is a clean negative result the proposal can build on ("v1 failed for three named
reasons; v2 addresses each").

## 3. Bedrock decisions

**D1. Today's MRAC is the degenerate case.** Gates identically 1, u_ff = 0, L1 = the 6 structured
features. Every experiment is a point in one design space, and each ablation is "set a layer to its
identity". The degenerate case must reproduce today's flight code bit-exactly (Theta and u_ad equal in
float32 on recorded inputs). This is the regression test for every later stage.

**D2. One adaptive law, one flat Phi.** All variety lives *before* Phi. The output layer stays linear in
Theta with the existing Lyapunov-backed law (normalised gradient, projection, sigma-mod, L1 filter). A deep
variant is inner layers that *produce* Phi, fixed (trained offline) or adapted slowly at a lower rate with
their own bounds. This is the Deep-MRAC / Neural-Fly split (`docs/research/phase2_lit_review.md`) and it
keeps the stability argument in one place.

**D3. Phi is a concatenation of feature blocks, and every feature carries metadata.** Block = {kind,
count, generator, parameters}. Hybrid = two blocks. Per feature: name, block, **group**, per-axis gamma,
projection bounds (upper, tol, lower), optional prior. The group tag (physics / time-scale class, e.g.
quasi-static, aero, coupling, rotor, control-effectiveness) is what L2 acts on, so the gate dimension is
the number of groups, not N.

**D4. One signal bus.** Every layer reads one struct: x, xm, e, e_dot, u_nom, cross terms, reference and
its derivatives, preview samples, rpm, thrust estimate, battery voltage, height. A new feature or gate
input never changes a function signature, and the bus is the place to add new sensors.

**D5. L2 has three outputs per group, each identity = 1.** `g_gamma` scales learning ("what to learn
now"), `g_sigma` scales leakage ("how fast to forget": slow physics keeps long memory), `g_phi` scales the
feature's use in u_ad ("what to use now"). The sim only had `g_gamma` + softmax. The gate law is a
selectable module, and the firmware must support normalisations that do **not** concentrate gain (mean-
normalised gates so total gain is conserved, or independent gates bounded in [g_min, 1]). Note: gating Phi
rescales the ideal weights (Theta*/g), so a feature with g_phi -> 0 must also have learning frozen
(g_gamma -> 0) or its weight drifts. Time-varying gamma needs the argument in
`sim/bench/prev_ref/time-varying-adaptation-gain-mrac.md`; projection keeps Theta bounded either way.

**D6. L2 input signal is selectable, and the rolling window is an IIR bank.** Candidates: tracking error,
a state-predictor / disturbance-observer residual (isolates the unmodelled part; addresses cause 2),
gyro, u_ad, reference. Default implementation: band-pass IIR bank + low-passed energy, O(1) per sample,
no history buffer. An optional ring buffer (for Goertzel, sliding DFT, wavelet or SINDy-style windows)
runs at a lower rate.

**D7. L3 has two outputs.** (a) `u_ff`, added to the command: known physics (inertia x reference angular
acceleration, thrust / rpm model, trajectory preview, local and global). (b) Optional predicted band
energies fed to L2 (the sim's predictive gate). Known physics can also enter as an L1 feature with a
sigma-prior toward its physical value (the existing `MRAC_ENABLE_SIGMA_PRIOR` / `Theta_prior` path):
known structure, adaptively corrected. Which form wins is a research question, so both stay possible.

**D8. Multi-rate.** Output law at the control rate (200 Hz, `MRAC_DT`). L2 analysis, inner-layer
adaptation and block methods get integer rate dividers. Nothing heavier than O(N) per sample runs at the
control rate.

**D9. Memory: the adaptive arrays move to CCM.** *Measured* (`OBJ/JX_FLY.build_log.htm`, current tree):
RW 2760 + ZI 123016 = 125776 B of the 131072 B main SRAM, so **5296 B free**. The 64 KB CCM at
0x10000000 is declared in `USER/JX_FLY.uvprojx` but *measured* unused: the generated `OBJ/JX_FLY.sct` has
only `RW_IRAM1`. CCM is CPU-only (no DMA), which suits MRAC state. A custom scatter file places only
explicitly tagged objects there. From the struct layout, one feature costs 8 floats per axis (Phi, Theta,
Whatf, gamma, limit, tol, lower, prior) = 128 B for 4 axes; the CCM budget is shared with L2 buffers and
inner layers. The capacity is chosen after CCM is enabled and measured, not before.

**D10. Selection: compile-time capacity, one variant switch, runtime ablations when disarmed.** Arrays are
sized by a compile-time capacity. `MRAC_VARIANT` picks a preset (block list, counts, gate law, L3 mode)
in one place. Layer enables (L2 on/off, L3 on/off, per-block on/off) are runtime flags changeable only
when disarmed, like `g_ctrl_select`, so ablations can run on the same battery and day without a reflash.

**D11. Observability scales with N.** A const descriptor table (feature name, block, group) lives in
flash; the ground station reads it from the ELF by symbol, so labels never go over the wire. The full
Theta / Phi / gate vectors go through the subscribe streams (2032 B payload), not Frame B, which keeps a
fixed window. A DWT cycle counter per layer measures CPU cost; the maximum N is decided by measurement.

**D12. One implementation, three test beds.** The firmware C files (not a Python rewrite) compile on the
host for (a) log replay, (b) closed-loop sim, (c) the equivalence test of D1. The v1 sim was a Python
re-implementation; results only transfer to flight if the same code runs in both.

## 4. Stages (each verified and committed; flight-critical, operator permission granted 2026-09-29)

| Stage | Content | Behaviour change |
|---|---|---|
| S1 | Signal bus, feature-block table with metadata and groups, `MRAC_VARIANT` preset header, identity L2 / zero L3 hooks, static `grad`, config tables in the `pid.c` table pattern, compile-time asserts (frame fits buffer, N <= capacity, element index < 16), host equivalence test | none (bit-exact) |
| S2 | CCM scatter file, capacity decoupled from the telemetry header, descriptor table, 8-bit element param command, Frame B fixed window, ground-station parsers, DWT counters | none in control |
| S3 | RBF, SINDy-library and hybrid blocks | new variants, default unchanged |
| S4 | L2 gate module (IIR bank, selectable input and normalisation, 3 outputs), L3 `u_ff` module | new variants, default unchanged |
| S5 | Deep inner layers (fixed, then slow-adapted), host replay / sim harness on the same C | new variants |

## 5. Open questions for the proposal

1. Gate target: learning (`g_gamma`), forgetting (`g_sigma`), use (`g_phi`), or a combination?
2. Gate normalisation that exploits scale information without concentrating gain.
3. Gate input: which signal isolates "physics currently unexplained" (predictor residual vs error)?
4. Feature library: curated physics, SINDy on flight logs, RBF, or learned; how to pick groups.
5. Time-scale separation between inner-layer learning and the output layer, and its stability argument.
6. L3: which known quantities (rpm, thrust, preview) give feedforward that the adaptive layer then
   only has to correct?
7. Does v2 beat unrouted on the bench and in flight at equal tuning budget (the H6 test, repeated)?

8. Sign of the weights. Read from code 2026-09-29: `MRAC_Init` (mrac.c:491-493) sets a negative lower
   bound only for weight 0 of pitch/roll/yaw; every other weight (and z weight 0) keeps lower bound 0,
   and `MRAC_ProjectGradient` stops any gradient that would push a weight below it. So today the damping,
   cross-coupling, u_nom and xm weights can only be >= 0. S1 keeps this (bit-exact) but makes the lower
   bound a visible column of the per-feature table; whether sign-free weights help is an early
   experiment, and every new block must choose its bounds explicitly.

Not decided: the variant that flies by default after S1, gate law, feature groups, L3 sources.
