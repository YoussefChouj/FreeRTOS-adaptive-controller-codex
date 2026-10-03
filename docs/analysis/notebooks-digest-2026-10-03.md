# MRAC notebooks digest (WP-30, 2026-10-03)

Source: the operator's adaptive-control notebooks. Read the greppable extracts in `docs/analysis/notebooks/`, not the
notebooks. "c N" means the 0-based cell index, the same as the `# %% [cell N]` header in the extract.
Numbers come from saved notebook outputs (marked *printed*) or from my own runs of the port (marked *measured
WP-30*). P3 has no saved outputs. The v1 copy (S3) has the same code apart from the barrier hooks, so its outputs
stand in for P3's design cells.

| Tag | Notebook | Extract |
|---|---|---|
| P1 | Adaptive_Control_Tutorial | `p1_adaptive_tutorial.py` |
| P2 | Adaptive_Control_Tutorial_2 | `p2_adaptive_tutorial_2.py` |
| P3 | RPY Direct MRAC FF Projection v2 (the aggregate notebook) | `p3_rpy_mrac_ff_proj_v2.py` |
| S1 | RPY FF Sigma_modification | `s1_rpy_ff_sigma_mod.py` |
| S2 | RPY FF Heuristic auto-tune | `s2_rpy_ff_heuristic_autotune.py` |
| S3 | RPY Direct MRAC FF Projection (v1, has outputs) | `s3_rpy_mrac_ff_proj_v1.py` |

## P1: scalar tutorials (dt 5 ms, 10-20 s, results are plots only)
- Plant (c0-c6): x' = w(t) x + u, with an unknown weight w(t) = 1.5 + 0.25 cos(0.5t) in the c4 example.
- Nominal control u = -α(x - c) with α = 2. Reference model x_r' = -α(x_r - c). Adaptive term u_a = -ŵ x.
- c1 law: ŵ' = γ x² (γ 25). c2: ŵ' = γ x e (point reference). c3: ŵ' = γ x (x - x_r) (γ 50).
- c4 projection: PR = x(x - x_r). If ŵ > w̄ - ε and PR > 0, then PR ← (w̄ - ŵ)/ε · PR, and the mirror case at the
  lower bound. ŵ' = γ PR, with w̄ = 5, ε = 0.2, γ = 50.
- c5 σ-mod: ŵ' = γ(x e - σ ŵ), σ 0.5. c6 e-mod: ŵ' = γ(x e - σ|e| ŵ), σ 2.
- c7-c10 neuro-adaptive: δ(x) = 1 + x² + sin(x)x³ + cos(x)x⁴. Gaussian RBF φ_i = exp(-0.1 (x - c_i)²) with
  centres every 2 from -5 to 5 (6, 12 and 21 neurons, plus a bias). Ŵ' = γ(φ e - σ Ŵ), γ 50, σ 0.2.
- c11 performance recovery (the scalar L1-like predictor): ψ' = -λ(ψ - e), v = λψ - (α + λ) e. v is added to u
  and to x_r' (λ = 5). An ideal x_ri is kept for comparison.
- c12-c17: 2-state mass-spring-damper. Same laws with Ŵ' = γ φ eᵀPB, RBF per state and joint RBF (γ 1-10).
  c17 adds performance recovery (λ = 1) and a command prefilter c' = -4(c - c_cmd).

## P2: 2-state tutorials
- c1, c2: integral-augmented MRAC on the mass-spring-damper (α 2.5), Ŵ' = γ φ e_ξᵀ P B_a (γ 10). c2 adds a
  PID-filtered noisy measurement (λ 50). Its saved output shows overflow warnings: the run diverged.
- c4 derivative-free MRAC (DF-MRAC): Ŵ(t) = γ1 Ŵ(t - τ) + γ2 β eᵀPB with γ1 0.9, γ2 10, τ 0.1 s, and
  β = [x1, x2, |x1|x2, |x2|x2, x1³]. Control u = -K1x + K2 r - Ŵᵀβ. *Printed* (uncertainty switched on at 20 s):
  RMS position error 0.1238°, max 0.4520°, final 0.0215°.
- c6 set-theoretic barrier: φ_d = (ε - ½√(eᵀPe)) / (ε - √(eᵀPe))², v = tanh(φ_d BᵀPe) q̂,
  u = -K1x + K2c - v - Ŵᵀφ. Ŵ' = γ1 Proj(φ_d φ eᵀPB) with ε 0.005, γ1 0.25, bounds ±10, tol 0.1, 11 RBF.
  *Printed*: max ‖e‖ 0.0034 < ε, no bound violation, max |u| 2.4326.
- c7 low-frequency learning: Ŵ' = γ(φ eᵀPB - σ(Ŵ - Ŵ_f)), Ŵ_f' = γ_f(Ŵ - Ŵ_f), with γ 250, σ 0.1, γ_f 0.5.
  *Printed*: RMS position error 0.0728, max 0.2221, max |u| 1.6191.

## P3: 3-axis attitude MRAC (the aggregate notebook)
- **Plant** (c1, c4): three decoupled double integrators, state [θ, θ'] per axis (pitch, roll, yaw).
  x' = A x + B Λ (u + Δ) with Λ = diag(1/J), J = 0.015 / 0.015 / 0.025 kg m². The vehicle is 0.65 kg, arm 0.225 m,
  thrust-to-weight 4.3. *Printed* (S3 c1): 6.85 N per motor, U_MAX = 2.18 N·m on pitch/roll and 1.50 N·m on yaw.
  Motor lag τ 50 ms, hover throttle 0.23, dt 6 ms, run length 100 s.
- **Reference model** (c3): θ_m'' = ωn²(r - θ_m) - 2ζωn θ_m' with ωn 3.5 rad/s and ζ 0.707, on the *angle*.
  c0 and c2 justify one unified [θ, θ'] model instead of a cascade.
- **Nominal and feedforward** (c4): model matching gives K1 = J [ωn², 2ζωn] and K2 = J ωn², so
  u_n = -K1 x + K2 r_f + v. *Printed* (S3 c4): K1 is [0.1837, 0.0742] on pitch/roll and [0.3063, 0.1237] on yaw.
  K2 is 0.1837 and 0.3063. *Printed* (S3 c5): phase margin 65.52° at 5.44 rad/s, delay margin 210 ms.
  P comes from Amᵀ P + P Am = -I (python-control `ct.lyap(Am.T, I)`). PB = P[:,1] = [0.0408, 0.1093] (computed
  here from that equation, not printed). The command prefilter is r_f' = 4(r - r_f).
- **Regressor** (c9, c12): 6 Gaussian RBFs on the angle (centres in ±π) plus 6 on the rate (±π/3), each with width
  1/(range/6)², extended with [u_n(k-1), v(k-1)]. That is 14 weights per axis. A structured option (c12) uses
  [1, θ, θ', θ' tanh θ', sin θ, gyro cross term].
- **Adaptive law** (c24 `update_weight`, `Projection_operator`): u_a = sat(-Ŵᵀφ, 0.3 U_MAX). Then
  g = φ eᵀPB / (1 + φᵀφ) and Ŵ' = Γ [Proj(g) - σ_LF(Ŵ - Ŵ_f)/(1 + φᵀφ) - σ Ŵ/(1 + φᵀφ)] with Ŵ_f' = γ_f(Ŵ - Ŵ_f).
  Γ = 350 / 350 / 300 (c9), σ_LF = 0.8 / 0.8 / 1.0, γ_f = 16. σ = 0.8 / 0.8 / 1.0 exists but
  `SIGMA_MODIFICATION = False` (c8). Projection: bounds ±100, tol 5, scale (lim - Ŵ)/tol on outward gradients.
  Past the bound this factor turns negative and pulls Ŵ back. c15 settles the ordering: project the
  *normalised gradient first*, then subtract σ terms (the old order made projection plus σ worse than either alone).
- **Performance recovery** (c10, c24): f' = -λ(f - e), g = λ f + (Am - λI) e, v_raw = B⁺ g, then
  v' = (v_raw - v)/τ_v. Here λ = 100 and τ_v = 2 / 2 / 1.5 s. v enters u_n and the reference model:
  x_m' = Am x_m + Bm r_f + B v + B(u_applied - u). The last term is pseudo-control hedging, which absorbs motor lag.
  c56-c58 (λ* from L1-norm analysis): theory allowed λ* ≈ 300-500 rad/s. In simulation λ ≥ 240 overflowed and λ = 100
  worked. The rule they settled on: λ ≤ 0.15 ω_Nyquist ≈ 78 rad/s at dt 6 ms.
- **Actuators** (c16, c24): torque is normalised as u/U_MAX · hover. Quad-X mixer, clip to [0, 1], first-order
  lag, inverse mixer /(4·hover). c16 lists the three mixer bugs that were fixed (normalisation, /2 → /4, roll sign).
- **Uncertainty and command** (c13): the 'hard' level uses RBF weights 0.1-0.3, a 15 rad/s vibration and a stall
  bump when |θ'| > 0.35. The disturbance is capped at 60 % of hover authority. Reference: 15° at 0.3 Hz + 8° at 0.05 Hz
  on pitch, 18° at 0.25 Hz AM on roll, a 30 ± 20 °/s yaw spin, and a pitch "flip" every 15 s.
- Flags that are off or unused: barrier (c11, c19, c22; log barrier with α 0.75 and smoothing 0.15°), deadzone
  0.05°, γ scheduling (τ 25 s), joint RBF. `USE_CONTROL_RATE_LIMITING` is True, but the c24 loop never applies
  `MAX_CONTROL_RATE`.
- **Results**: P3 shows only plots. Port `sim/bench/notebook_rpy_mrac.py`, 100 s 'hard', RMS angle error in °
  for pitch / roll / yaw (*measured WP-30*):

| config | vs pure ref model x_r | vs MRAC ref x_m (notebook metric, c33) |
|---|---|---|
| MRAC + perf recovery (notebook default) | 0.470 / 0.489 / 0.904 | 0.558 / 0.573 / 0.923 |
| perf recovery only (`ADAPTATION_ON=False`) | 0.377 / 0.374 / 1.408 | 1.464 / 1.463 / 1.671 |
| MRAC, no perf recovery | 0.893 / 0.890 / 2.981 | 0.889 / 0.886 / 2.953 |
| nominal K1/K2 only | 93.66 / 93.66 / 38.59 | same |

  Without integral action the K1/K2 nominal drifts under the bias-like RBF disturbance: 0.3 N·m / 0.18 N·m/rad
  ≈ 1.6 rad. MRAC alone brings that down to under 1° on pitch/roll. With the L1-like v on, most of that bias is
  already cancelled. MRAC then wins on x - x_m and on yaw, but loses about 25 % on pitch/roll against the pure x_r.

## Secondary notebooks
- S1 σ-mod (structured 6 + [u_n, v], J 0.01/0.01/0.02, ωn 2.0, ζ 0.8; c8): Γ 350, σ 0.8/0.8/1.0, γ_f 4,
  forgetting τ 30 s. Law (c14): with LF learning on, Ŵ' = Γ(g - σ(Ŵ - Ŵ_f))/norm. Otherwise
  Ŵ' = Γ(g - σŴ)/norm. *Printed* (c16): pitch max 2.573°, RMS 1.792, final 0.077°, max torque 0.449 N·m, settling
  4.42 s. Roll max 1.863°.
- S2 heuristic auto-tune (c3 `compute_robust_adaptive_parameters`): the auto-tune is a **preset lookup**, not a
  search. low = λ 0.15, γ 5, σ 0.5, γ_f 2, τ_γ 10, τ_v 0.1. medium = 120, 200, 0.1, 1, 35, 1.0.
  high = 150, 250, 0.5, 5, 40, 2. All three force LF learning, γ scheduling and a filtered v on. The run used LOW
  (*printed* c4). *Printed* c8: final error pitch -0.011°, roll 0.007°, yaw -0.883°. *Printed* c25: RMS 0.434.
  The c0 lessons: bounded basis, λ chosen against reference dynamics, σ-mod as a drift guarantee.
- S3 = P3 v1 (no barrier gradient in the law). Its design outputs are the ones quoted above.

## Notebook law vs firmware
Firmware default build: `DEFAULT_REF_MODEL_TYPE 0` and shadow injection.

| Item | Notebook (cell) | Firmware (file:line) | Difference |
|---|---|---|---|
| Loop and state | attitude, x = [θ, θ'], u = torque (P3 c4) | rate loop, x = gyro rate (API/mrac.c:803-808), u_ad added to the PID | different plant layer |
| Inertia | J 0.015/0.025 (P3 c1) | J 0.0023/0.0015 (API/mrac.c:649) | notebook J is a cuboid guess, about 6x larger |
| Ref model | 2nd order on angle, ωn 3.5, ζ 0.707 (P3 c3) | type 0 passthrough by default (API/mrac.c:372-378); type 2 on rate, ωn 44/44/30, ζ 0.8 (API/mrac.c:352-357, 654-655) | firmware bandwidth comes from SysID |
| P matrix | `lyap`, Q = I (P3 c4) | closed form Pe = Q1/2a0, Pedot = (Q1/a0+Q2)/2a1 (API/mrac.c:444-455) | same math for type 2; scalar P for types 0/1 |
| Regressor | 12 RBF + [u_n, v] (P3 c9, c12) | 6 structured [1, ω, ω tanh ω, cross, u_nom, x_m] (API/mrac.c:242-274, API/mrac_variant.h:10-16) | RBF helper exists but is unused (API/mrac_math.c:49) |
| Sign | u_a = -Ŵᵀφ, g = +φ eᵀPB (P3 c24) | u_ad = +Θᵀφ, g = -sφ/denom (API/mrac.c:470, 528-531) | equivalent with Θ = -Ŵ |
| Normalisation | gradient **and** leakage / (1+φᵀφ) (P3 c24) | gradient only; leakage not normalised, `FIX_LEAKAGE_NORMALIZATION 1` (API/mrac.h:66, API/mrac.c:491-495) | firmware leakage is stronger at large φ |
| Projection | symmetric ±100, tol 5, restoring past the bound (P3 c24) | per-feature [lower, limit], mostly [0, 0.02-0.2]; zero gradient past the bound (API/mrac.c:189-239, 664-687) | firmware bounds are 500-5000x tighter; lower bound 0 stops sign learning |
| Γ | 350/350/300 per axis (P3 c9) | per feature 0.05-1.5 (API/mrac.c:664-681) | units differ (rate error, smaller φ) |
| σ / e-mod | σ off (P3 c8) | σ 0.01 + e-mod k_e 0.05·\|e\| on (API/mrac.c:642, 653, 462-466, 734) | firmware has leakage, the notebook does not |
| LF learning | σ_LF 0.8/0.8/1.0, γ_f 16, **on** (P3 c8-c9) | same numbers (API/mrac.c:641, 643) but `l1_filtering_on = 0` (API/mrac.c:735) | **off in firmware** |
| Perf recovery | L1-like predictor f, λ 100, τ_v, v into u and x_m (P3 c10, c24) | first-order LPF on u_ad, ω_u 4/5/4 (API/mrac.c:533-539, 644); λ/τ_v stored as 0 and unused (API/mrac.c:645-646, API/mrac.h:75) | **not implemented**: firmware only filters u_ad |
| Feedforward | K2 r_f model matching (P3 c4, c24) | `MRAC_L3_Feedforward` returns 0 (API/mrac.c:294-299) | feedforward stays in the PID |
| Hedging (PCH) | B(u_applied - u) into x_m (P3 c24) | none (grep: no `hedge` in API/mrac*) | missing |
| u_a limit | 30 % of U_MAX (P3 c9) | u_max 6.74/6.74/2.03 (API/mrac.c:541-547, 647) | different units and scale |
| Deadzone | off, 0.05° (P3 c8-c9) | on, 0.05 rad/s on \|e\| (API/mrac.c:417, 650) | |
| γ scheduling | off, τ 25 s (P3 c8); S2 says always on | none (grep: no `sched` in API/mrac*) | |
| Firmware-only guards | none | hard freeze, tanh e_sat, simplex trip, shadow injection (API/mrac.c:419-438, 741) | |

## Inputs for WP-27 (firmware MRAC variants)
1. **L1-like performance recovery** as a variant: the predictor f, λ ≤ 0.15 ω_Nyquist (the P3 c58 rule; it
   would need re-deriving for the firmware loop rate), and a filtered v added to u and to x_m (P3 c24). This is the
   piece that beat MRAC alone in the port.
2. **PCH hedge** of x_m with (u_applied - u_cmd). It needs an applied-torque estimate (the inverse mixer exists at
   API/mrac.c:160).
3. **LF learning on** (`l1_filtering_on`) with the notebook's σ_LF 0.8/0.8/1.0 and γ_f 16. The firmware already has
   the code.
4. Normalise the leakage terms the way the notebook does, or document why `FIX_LEAKAGE_NORMALIZATION` departs from it.
   Keep the P3 c15 order: project the normalised gradient, then subtract σ.
5. A restoring projection past the bound (the notebook's negative scale), and symmetric bounds on features whose
   true sign is unknown.
6. An RBF variant: 6 + 6 Gaussians with width 1/(range/6)² plus [u_n, v] (P3 c12). The helper already exists.
7. Model-matching feedforward K2 r_f in `MRAC_L3_Feedforward` (rate-loop analogue: J·ωn²·r).
8. Treat S2's "auto-tune" presets as starting points only (PROPOSED). No search method exists to port.
9. Regression check: `python sim/bench/notebook_rpy_mrac.py --ablation`: four 100 s runs. I timed two of them at 11-13 s
   each on this laptop.
