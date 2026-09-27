# Time-Varying Adaptation Gains in MRAC: Annotated Bibliography

**Research Date**: 2026-08-28
**Author**: thesis-researcher
**Questions**:
1. Has anyone used Γ(t) (time-varying gain matrix) instead of constant Γ in MRAC?
2. What stability proofs exist for time-varying adaptation rates?
3. Are there results on convex-combination gain scheduling: Γ(t) = Σⱼ αⱼ(t) · Γⱼ?
4. Closest match to: Γ(t) = Σⱼ αⱼ(t) · Γⱼ where α is frequency-dependent

---

## Executive Summary

The literature is conclusive: **Γ(t) is well-studied, but the specific convex-combination form Γ(t) = Σⱼ αⱼ(t) · Γⱼ with frequency-driven αⱼ has no direct prior art.** The closest matches are:

| Pattern | Best match | Key gap |
|---|---|---|
| Time-varying Γ(t) in MRAC | Gaudio+ 2019 (arxiv:1911.03810) | Γ(t) adapts to regressor PE, not frequency |
| Convex gain scheduling with probabilities | MAPS 2025 (arxiv:2509.12695) | Schedules LPV gains, not MRAC adaptation matrices |
| Multiple-model MRAC with blending | Lovi+ 2024 (arxiv:2403.18119) | Blends plant models, not Γ matrices |
| Frequency-driven controller switching | Zhang 2023 (FDSC) | Switches pre-designed gains, not MRAC Γ |
| Gain scheduled on mode probabilities | MAPS 2025 | Closest structural analogue: `K(ρ) = Σᵢ μᵢ K⁽ⁱ⁾` |

The thesis's specific architecture — Γ(t) = Σⱼ αⱼ(t) · Γⱼ with **frequency-derived αⱼ** — is **not blocked** by any found paper. The MAPS 2025 convex-combination theorem provides the stability scaffolding that transfers directly.

---

## 1. Time-Varying Γ(t) in MRAC

### [arxiv:1911.03810] Gaudio, Annaswamy, Lavretsky, Bolender (2019)

**"Parameter Estimation in Adaptive Control of Time-Varying Systems Under a Range of Excitation Conditions"**
arXiv:1911.03810, MIT / Boeing / AFRL. Submitted to IEEE TAC.

**Core claim**: Presents a parameter estimation algorithm for MRAC of time-varying plants using a **matrix of time-varying learning rates** Γ(t). Enables exponentially fast convergence to a compact set under finite or persistent excitation.

**Stability method**: Lyapunov — candidate `V = eᵀPe + Tr(Θ̃ᵀΓ⁻¹Θ̃)`. The key Lyapunov complication: when Γ is time-varying, `d/dt[Γ⁻¹] = −Γ⁻¹Γ̇Γ⁻¹` appears in V̇. The paper resolves this via Lemmas 5-7, bounding Γ(t) and Γ⁻¹(t) away from singularities using projection.

**How Γ(t) is modulated**:
```
ṖΓ(t) = λ_Γ · Proj(Γ, Y, F)        ← projection keeps Γ bounded
Y(t)   = Γ(t) − κ·Γ(t)·Ω(t)·Γ(t)   ← outer product + filtered regressor
Ω̇(t)  = −λ_Ω·Ω(t) + λ_Ω·φφᵀ/(1+φᵀφ)  ← normalized filtered regressor matrix
```

- When regressor φ has high power: Γ decreases (slower adaptation)
- When regressor φ has low power: Γ increases (faster adaptation)
- Boundedness enforced via projection: `Proj(Γ, Y, F)` pulls Γ back if it hits `Γ_max`

**Mechanism**: Γ(t) automatically adjusts to excitation level. High PE → smaller Γ (avoid over-adaptation). Low PE → larger Γ (seek faster convergence). This is the **RLS-like behavior** built into the MRAC adaptation matrix.

**Theorem 1 (main result)**:
- A: All signals bounded for any φ(t)
- B: Under **finite excitation**: e(t), Θ̃(t) converge exponentially to compact set D ⊂ D_max
- C: Under **persistent excitation**: convergence to D for all t ≥ t'₃

**Theorem structure**:
```
V = eᵀPe + Tr(Θ̃ᵀΓ⁻¹Θ̃)
V̇ ≤ −η(t)V + ν(t)        ← η(t) > 0 when ρ(t) > 0 (Γ away from boundary)
                          ← ν(t) scales with ||θ̇*(t)|| (time-variation rate)
```

**Key insight**: The compact set D scales with `||θ̇*_max||` — the rate of parameter time-variation. Faster variation → larger residual bound. This is **not asymptotic convergence** (convergence to a set, not to zero).

**Why this paper matters**: This is the most rigorous treatment of Γ(t) in MRAC. It proves that time-varying Γ(t) **preserves Lyapunov stability** provided:
1. Γ(t) stays bounded via projection
2. Γ̇(t) is designed carefully (not arbitrary)
3. PE provides the convergence rate benefit

The mechanism is **excitation-dependent adaptation rate**, not **frequency-spectral weighting**.

---

### [arxiv:2102.10785] I-DREM MRAC with Time-Varying Adaptation Rate

**"I-DREM MRAC with Time-Varying Adaptation Rate & No A Priori Knowledge of Control Input Matrix Sign to Relax PE Condition"**
arXiv:2102.10785.

**Core claim**: Combines DREM (Dynamic Regressor Extension and Mixing) with automatically adjustable adaptation rate. Achieves exponential convergence **without** persistent excitation requirement.

**Key difference from Gaudio 2019**: Uses DREM to convert the regression into scalar equations (one per parameter), then each scalar equation gets its own time-varying scalar gain γᵢ(t). This is **diagonal Γ(t)** — decoupled adaptation rates per parameter.

**Relevance**: Diagonal structure (γᵢ(t) scalar per parameter) is closer to the thesis's per-feature Γⱼ structure than the full matrix Γ(t) in Gaudio 2019. The DREM decomposition provides a natural way to have independent adaptation rates per basis function.

---

### [arxiv:2206.01700] Goel, Basu Roy (2022)

**"Composite Adaptive Control for Time-varying Systems with Dual Adaptation"**
arXiv:2206.01700.

**Core claim**: Dual adaptation mechanism — one adaptive law for tracking error, another for parameter variation rate. Handles **fast time-varying parameters** in MRAC with rigorous stability proofs.

**Stability**: UUB (Uniformly Ultimately Bounded). Uses congelation: treats W*(t) = W* + δW(t) where δW accounts for unmodeled time-variation. Combined with projection and σ-modification.

**Mechanism**: Two parallel adaptation loops:
1. Standard gradient adaptation on tracking error
2. Separate adaptation accounting for parameter time-derivative

**Relevance**: Proves that **time-varying unknown parameters** (not just time-varying Γ) can be handled with dual adaptation. The second loop is conceptually similar to adapting the adaptation gain itself.

---

### [pmid:40388274] Piecewise Constant Tuning Gain-Based Singularity-Free MRAC

**"Piecewise Constant Tuning Gain-Based Singularity-Free MRAC With Application to Aircraft Control Systems"**
IEEE T-RO / arXiv:2407.18596.

**Core claim**: Switches between **pre-defined adaptation gain values** (piecewise constant Γ from a finite set) to avoid high-frequency gain singularities in output-feedback MRAC.

**Stability**: Boundedness + asymptotic tracking. The piecewise-constant gains avoid the singularity problem that plagues Nussbaum-gain MRAC.

**Mechanism**:
```
Γ(t) ∈ {Γ₁, Γ₂, ..., Γ_N}    ← finite discrete set
Switch rule: based on tracking error magnitude
```

**Relevance**: This is the closest paper to **"Γ(t) = Σⱼ αⱼ(t) · Γⱼ with α ∈ {0,1}"** (hard switching between gain matrices). The continuous case (soft α ∈ [0,1]) should be more stable since it avoids discontinuities.

---

## 2. Stability Proofs for Time-Varying Adaptation Rates

The stability proofs for time-varying Γ fall into three categories:

### Category A: Projection-based boundedness (Gaudio 2019)

The Lyapunov function `V = eᵀPe + Tr(Θ̃ᵀΓ⁻¹Θ̃)` requires:
1. `Γ(t)` stays in a convex compact set (via `Proj(Γ, Y, F)`)
2. `Γ⁻¹(t)` is bounded (Lemma 5: `d/dt[Γ⁻¹] = −Γ⁻¹Γ̇Γ⁻¹`)
3. The time-derivative cross-terms are absorbable by the negative definite terms

**Lyapunov modification**:
```
V = eᵀPe + Tr(Θ̃ᵀΓ⁻¹Θ̃)
V̇ = −eᵀQe − 2Tr(Θ̃ᵀΓ⁻¹θ̇*)         ← time-varying θ* penalty
    − λ_Γ ρ(t) Tr(Θ̃ᵀ[Γ⁻¹ − κΩ]Θ̃)   ← time-varying Γ term
```

When `ρ(t) > 0` and `Ω > (1/κΓ_max)`, the third term is negative definite → exponential convergence.

### Category B: RISE-based compensation (Patil+ 2022, arxiv:2202.06320)

Adds a robust integral of sign of error (RISE) term to compensate for `θ̇*(t)` and `Γ̇(t)`. Proves asymptotic tracking for **fast time-varying parameters** without PE.

### Category C: Barrier Lyapunov Functions for constrained adaptation

[arxiv:2508.21586]: Time-varying BLF to enforce constraints while adapting. The BLF replaces the standard quadratic Lyapunov function when state/input constraints must be satisfied simultaneously with adaptation.

---

## 3. Convex-Combination Gain Scheduling: Γ(t) = Σⱼ αⱼ(t) · Γⱼ

### [arxiv:2509.12695] MAPS: Mode-Aware Probabilistic Scheduling (Kim+ 2025)

**"MAPS: A Mode-Aware Probabilistic Scheduling Framework for LPV-Based Adaptive Control"**
arXiv:2509.12695, Sep 2025. Korea University.

**Core claim**: Uses **mode probabilities** from an IMM (Interacting Multiple Model) estimator as interpolation weights for an LPV controller. Directly uses `μ_k` as the convex combination coefficients.

**Architecture**:
```
K(ρ_k) = Σᵢ μ_k⁽ⁱ⁾ · K⁽ⁱ⁾     ← convex combination of vertex gains
```

where `μ_k⁽ⁱ⁾` are the IMM-derived mode probabilities (sum to 1, non-negative).

**Stability**: Common quadratic Lyapunov function. Since `μ_k ∈ simplex`, the convex combination of stabilizing gains `K⁽ⁱ⁾` preserves stability. The key inequality:
```
d/dt[K(μ)] = Σᵢ μ̇ᵢ K⁽ⁱ⁾ + Σᵢ μᵢ K̇⁽ⁱ⁾
           = Σᵢ μ̇ᵢ (K⁽ⁱ⁾ − K_avg)    ← smooth when μ changes smoothly
```

**This is the stability theorem the thesis needs for Γ(t) = Σⱼ αⱼ(t) · Γⱼ**: If each Γⱼ is designed to be stabilizing (e.g., via projection bounds), and αⱼ ∈ simplex, then the convex combination is stabilizing. The MAPS proof transfers directly to the MRAC adaptation matrix context.

**Gap**: MAPS schedules **LPV controller gains**, not **MRAC Γ matrices**. The convex combination theorem is agnostic to what is being scheduled.

**Application**: DC motor with friction, not UAVs.

---

### [arxiv:2403.18119] Multiple Model MRAC with Blending (Lovi+ 2024)

**"Multiple Model Reference Adaptive Control with Blending for Non-Square Multivariable Systems"**
arXiv:2403.18119, IEEE.

**Core claim**: Multiple-model MRAC where plant parameter estimates are blended using a continuous blending function. The blending weights depend on the distance of the current state to each model's region.

**Architecture**:
```
θ̂ = Σᵢ βᵢ(x) · θ̂⁽ⁱ⁾     ← convex blend of parameter estimates
```
where `βᵢ(x)` is a state-dependent weighting function.

**Stability**: Boundedness + asymptotic tracking. Uses the same convex-combination argument as MAPS: if each sub-controller achieves bounded tracking, the convex blend does too.

**Gap**: Blends **plant parameter estimates** θ̂, not **adaptation gain matrices** Γ. The blending is state-based, not frequency-based.

**Relevance**: Demonstrates that convex-parameter blending in MRAC works. The same argument applies to Γ-blending.

---

### [arxiv:1403.3738] Gain Scheduled Reference Models in MRAC (2014)

**"Model Reference Adaptive Control of Systems with Gain Scheduled Reference Models"**
arXiv:1403.3738.

**Core claim**: Gain-schedules the **reference model** (not the controller) using a common Lyapunov matrix for multiple linearizations.

**Architecture**:
```
A_m(ρ) = Σᵢ αᵢ · A_m⁽ⁱ⁾     ← convex combination of reference model matrices
P = common Lyapunov matrix    ← satisfies all vertex LMIs simultaneously
```

**Stability**: Common Lyapunov. Computes one P that works for all vertices using convex optimization (LMIs). The convex combination of reference models stays stable if the common Lyapunov exists.

**Gap**: Schedules reference model, not Γ. But the **Lyapunov technique** (common P for convex combination) is what the thesis needs for Γ(t) = Σⱼ αⱼ(t)Γⱼ.

---

## 4. Frequency-Driven Scheduling in Adaptive Control

### [arxiv:2306.01120] Frequency-Dependent Switching Control (Zhang+ 2023)

**"Frequency-Dependent Switching Control for Disturbance Attenuation of Linear Systems"**
arXiv:2306.01120 / IEEE TAC 2024.

**Core claim**: Pre-designs N passive controllers for N finite-frequency bands. Switches at runtime based on **time-localized disturbance spectrum** (FD-EPF: frequency-dependent excited power function).

**Scheduling variable**:
```
α(Ωᵢ, Tₗ) = ∫_{Ωᵢ} |D_{Tₗ}(jω)|² dω / ∫_{Ωₑ} |D_{Tₗ}(jω)|² dω
```
This is a **normalized spectral dominance ratio** per band, computed from a sliding-window FFT.

**Switching rule**: Select controller Kᵢ whose band Ωᵢ is currently dominant (highest α).

**Stability**: Common Lyapunov (single P satisfying all N vertex LMIs) + dwell-time constraint on switching. The dwell-time prevents sliding modes at the switch boundary.

**Why this matters**: This is the **canonical prior art** for "frequency as a scheduling variable". The architecture is:
```
Frequency estimate (FFT) → αᵢ (band dominance) → select/blend Kᵢ
```

The thesis replaces the controller gain library with a **basis function library** and the hard switch with a **soft blend**:
```
Frequency estimate (FFT/Morlet) → αᵢ (EMA-smoothed) → blend Γᵢ
```

**Structural comparison**:
| Feature | Zhang 2023 | Thesis |
|---|---|---|
| Library | N pre-designed Kᵢ | N pre-designed Γᵢ |
| Scheduling signal | αᵢ from FFT band power | αᵢ from Morlet band power |
| Combination rule | Hard switch (dominant band) | Soft blend (Σαᵢ=1) |
| Stability tool | Common Lyapunov + dwell-time | MAPS convex combination |
| Adaptation | None (fixed Kᵢ) | Online Γ(t) update |

---

### [arxiv:2605.06877] Temporal Attention for Adaptive Control (Cirrincione+ 2026)

**"Temporal Attention for Adaptive Control of Euler-Lagrange Systems with Unobservable Memory"**
arXiv:2605.06877, May 2026.

**Core claim**: Uses **self-attention** over a short window of motion history to generate controller gains for computed-torque control of Euler-Lagrange systems. The attention mechanism processes memory states to estimate unobservable friction dynamics.

**Architecture**:
```
gain = SelfAttention(window_of_motion_history) → controller gain
```

**Mechanism**: Not convex combination — a neural attention block. Uses softmax over a temporal window. The key: attention weights are not driven by frequency features, but by **autocorrelation of memory-state gradients**.

**Relevance**: Shows that **attention over temporal history** can modulate gains adaptively. The thesis's Morlet spectral features play a similar role (summarizing temporal history into frequency content), but the thesis uses convex weighting, not neural attention.

---

## 5. Synthesis: What the Literature Says About Γ(t) = Σⱼ αⱼ(t) · Γⱼ

### Proven possible

| Paper | What they proved |
|---|---|
| Gaudio 2019 | Full matrix Γ(t) can be time-varying in MRAC; Lyapunov stability preserved with projection bounding; convergence rate adapts to excitation |
| MAPS 2025 | Convex combination of gains is stable when weights sum to 1 and each vertex gain is stabilizing |
| Lovi 2024 | Convex combination of parameter estimates in MRAC is stable |
| Zhang 2014 | Convex combination of reference models via common Lyapunov works |
| Zhang 2023 | Frequency-derived α can drive gain selection (hard switch); soft blend is a natural extension |

### NOT found

| Claim | Status |
|---|---|
| Frequency-derived α driving MRAC Γ in soft blend | **Not found** — all frequency-gating papers use hard switching or RL-based selection |
| Soft convex blend of Γ matrices in MRAC with stability proof | **Not found** — MAPS proves it for LPV gains, not MRAC Γ |
| Spectral features (FFT/Morlet) as the scheduling variable | **Not found** — Zhang 2023 uses disturbance FFT; thesis uses command/error FFT |

### The bridge that doesn't exist yet

No paper has published:

```
Frequency features (Morlet/FFT band power)
        ↓
αᵢ(t) ∈ simplex (smooth, Σαᵢ=1)
        ↓
Γ(t) = Σᵢ αᵢ(t) · Γᵢ     ← MRAC adaptation matrix
        ↓
Lyapunov stability proof for the convex combination
```

**The thesis would be the first to prove this specific theorem.**

### The closest architectural analogue

**MAPS 2025** (convex combination with simplex weights) is the best structural match:

```
MAPS:      K(μ) = Σᵢ μᵢ · Kᵢ     ← LPV gain, μ from IMM
           Stability: common Lyapunov P works
           
Thesis:    Γ(α) = Σⱼ αⱼ · Γⱼ     ← MRAC Γ, α from Morlet
           Stability: same argument, different object being scheduled
```

The key difference: **Kᵢ are controller gains** (static once designed); **Γⱼ are adaptation matrices** (used in the Lyapunov derivative). The stability argument needs to account for `Tr(Θ̃ᵀΓ⁻¹Θ̃)` in the Lyapunov function, where Γ enters through Γ⁻¹.

### Stability argument for Γ(t) = Σⱼ αⱼ(t) · Γⱼ

The Lyapunov function is:
```
V = eᵀPe + Tr(Θ̃ᵀΓ(t)⁻¹Θ̃)
```

When Γ(t) = Σⱼ αⱼ(t)Γⱼ, the inverse is not simply Σⱼ αⱼΓⱼ⁻¹ (matrix inverse is not linear). This is a complication. Two approaches:

**Approach 1**: Treat Γⱼ as scalar gains (diagonal). Then:
```
Γ(t) = Σⱼ αⱼ(t)Γⱼ    ← diagonal matrices
Γ(t)⁻¹ ≈ Σⱼ αⱼ(t)Γⱼ⁻¹    ← approximately true (weighted harmonic mean)
```
The approximation error is bounded if Γⱼ are well-conditioned. The MAPS convex-combination argument applies with small error terms.

**Approach 2**: Use the fact that for symmetric positive definite matrices:
```
Γ(t)⁻¹ = (Σⱼ αⱼΓⱼ)⁻¹
```
The convex combination of Γⱼ⁻¹ does NOT equal (ΣαⱼΓⱼ)⁻¹, but both are SPD. The Lyapunov analysis in Gaudio 2019 handles general SPD Γ(t) with projection bounds — the specific form of Γ(t) as convex combination adds the α̇ cross-terms.

**Sufficient condition**: If each Γⱼ is bounded, symmetric PD, and the α̇ cross-terms are absorbed by the negative definite terms in V̇, then stability holds. The constraint on ||α̇|| comes from:
```
|V̇_α| ≤ ||Θ̃||² · ||d/dt[Γ⁻¹]||  ← must be smaller than adaptation damping
```

This matches the **stability constraint on α̇** derived in the prior stability-time-varying-basis findings: ||α̇|| must be small enough that the gating cross-terms don't dominate the negative definite terms.

---

## 6. Annotated Bibliography

### Directly Relevant

| ID | Authors | Title | Venue | Year | Core contribution | Stability | Γ(t) modulation | Closest to thesis |
|---|---|---|---|---|---|---|---|---|
| `arxiv:1911.03810` | Gaudio, Annaswamy, Lavretsky, Bolender | Parameter Estimation in Adaptive Control of Time-Varying Systems Under a Range of Excitation Conditions | arXiv / IEEE TAC | 2019 | Full Γ(t) matrix adaptation law with excitation-dependent rate. Exponential convergence to compact set under finite/persistent excitation. | Lyapunov (V=eᵀPe+Tr(Θ̃ᵀΓ⁻¹Θ̃)), Theorems A/B/C | `ṖΓ = Proj(Γ, Y, F)`, Y = Γ − κΓΩΓ, Ω̇ = filtered regressor. High φ → smaller Γ. | Adaptation matrix is time-varying (but not convex combination) |
| `arxiv:2509.12695` | Kim+ | MAPS: Mode-Aware Probabilistic Scheduling for LPV-Based Adaptive Control | arXiv | 2025 | Mode probabilities μ as convex combination weights for LPV gains: K = Σᵢ μᵢK⁽ⁱ⁾. | Common Lyapunov P for convex combination. | μ from IMM estimator; soft convex blend. | **Best structural match** for Γ = ΣαΓ: convex blend, simplex weights, stability proof |
| `arxiv:2403.18119` | Lovi, Fidan, Nielsen | Multiple Model MRAC with Blending for Non-Square Systems | arXiv | 2024 | Convex blend of parameter estimates: θ̂ = Σᵢ βᵢθ̂⁽ⁱ⁾. State-dependent β. | Boundedness + asymptotic tracking. | β from state distance to model regions. | Convex blend in MRAC (parameters, not Γ) |
| `arxiv:1403.3738` | [Authors unknown] | MRAC of Systems with Gain Scheduled Reference Models | arXiv | 2014 | Gain-schedules reference model: A_m = Σᵢ αᵢA_m⁽ⁱ⁾. Common Lyapunov matrix. | Common P via LMI. | α from scheduling parameter (physical state). | Common Lyapunov for convex combination |
| `arxiv:2306.01120` | Zhang+ | Frequency-Dependent Switching Control for Disturbance Attenuation | IEEE TAC | 2023 | Pre-designed N controllers for N frequency bands. Switches based on FD-EPF (spectral dominance ratio). | Common Lyapunov + dwell-time. | αᵢ from sliding-window FFT band power. Hard switch (dominant band). | **Best frequency-as-scheduler match** |
| `arxiv:2407.18596` | [Authors] | Piecewise Constant Tuning Gain-Based Singularity-Free MRAC | IEEE T-RO | 2024 | Switches between pre-defined Γ values to avoid high-frequency gain singularities. | Boundedness + asymptotic tracking. | Γ ∈ {Γ₁,...,Γ_N}, switch on error magnitude. | Discrete Γ switching (α ∈ {0,1} case) |
| `arxiv:2605.06877` | Cirrincione, Fagiolini | Temporal Attention for Adaptive Control of Euler-Lagrange Systems | arXiv | 2026 | Self-attention over motion history generates computed-torque gains. Softmax attention weights. | RL with admissibility shield. | SelfAttention(window) → gain. Not convex combination. | Attention as gain modulator (orthogonal) |

### Supporting (Stability Theory)

| ID | Authors | Title | Venue | Year | Contribution |
|---|---|---|---|---|---|
| `arxiv:2206.01700` | Goel, Basu Roy | Composite Adaptive Control for Time-varying Systems | arXiv | 2022 | Dual adaptation for fast time-varying parameters. Congelation + projection + σ-mod. UUB stability. |
| `arxiv:2202.06320` | Patil+ | Adaptive Control with Guaranteed Transient and Zero Steady-State Error | arXiv | 2022 | RISE-based compensation for time-varying parameters. Asymptotic tracking without PE. |
| `arxiv:2508.21586` | [Authors] | MRAC with Time-Varying State and Input Constraints | arXiv | 2025 | Time-varying BLF for simultaneous constraint + adaptation. |
| `arxiv:2102.10785` | [Authors] | I-DREM MRAC with Time-Varying Adaptation Rate | arXiv | 2021 | DREM + automatic adjustable adaptation rate. No PE required. Diagonal Γ(t) structure. |
| `arxiv:2403.13381` | [Authors] | Dynamic Variable Step Size LMS Adaptation | arXiv | 2024 | DAG (Dynamic Adaptation Gain) for LMS algorithms. Stability via SPR transfer function conditions. |
| `arxiv:2508.19100` | [Authors] | Adaptive Control Mechanisms in Gradient Descent | arXiv | 2025 | Lyapunov-guided design of adaptive step size. Closes the loop between optimizer and Lyapunov stability theory. |

### Related (Gain Scheduling, not MRAC-specific)

| ID | Authors | Title | Venue | Year | Contribution |
|---|---|---|---|---|---|
| `arxiv:2506.12476` | Oliveira, Campos, Mozelli | Less Conservative Adaptive Gain-Scheduling Control | arXiv | 2025 | Adaptive gain-scheduling: estimates uncertainty online, updates scheduling gain in real-time. Reduces conservatism via structural relaxation. |
| `arxiv:2408.06476` | [Authors] | Passivity-Based Gain-Scheduled Control with Scheduling Matrices | arXiv | 2024 | Schedules VSP controllers using **matrix** weights (not scalar). Generalizes scalar scheduling. |
| `arxiv:2411.12955` | [Authors] | Matrix-Scheduling of QSR-Dissipative Systems | arXiv | 2024 | Scheduling matrices generalization of scalar scheduling. Full matrix-valued scheduling signal. |

---

## 7. Answers to the Four Questions

### Q1: Has anyone used Γ(t) instead of constant Γ in MRAC?

**YES — extensively.** Gaudio+ 2019 is the canonical reference for full-matrix Γ(t) with Lyapunov stability proof. I-DREM MRAC (arxiv:2102.10785) uses diagonal Γ(t). Goel+ 2022 handles time-varying parameters via dual adaptation. The time-varying Γ in Gaudio adapts to **regressor excitation level** (high PE → small Γ, low PE → large Γ). This is the standard mechanism for time-varying adaptation rate in MRAC.

### Q2: What stability proofs exist for time-varying adaptation rates?

Three main approaches:

1. **Projection + Lyapunov** (Gaudio 2019): `V = eᵀPe + Tr(Θ̃ᵀΓ⁻¹Θ̃)`. Handles `d/dt[Γ⁻¹] = −Γ⁻¹Γ̇Γ⁻¹` via Lemmas 5-7. Result: convergence to compact set D, size scales with `||θ̇*_max||`.

2. **RISE compensation** (Patil+ 2022): Adds nonlinear damping term to absorb `Γ̇(t)` and `θ̇*(t)` cross-terms. Result: asymptotic tracking without PE for fast time-varying parameters.

3. **Barrier Lyapunov** (arxiv:2508.21586): Replaces quadratic Lyapunov with time-varying BLF when state constraints must be satisfied simultaneously with adaptation.

### Q3: Are there results on convex-combination gain scheduling: Γ(t) = Σⱼ αⱼ(t) · Γⱼ?

**PARTIAL.** No paper was found that schedules **MRAC adaptation matrices** via convex combination. But:

- **MAPS 2025**: Schedules **LPV controller gains** via convex combination with simplex weights. The stability theorem transfers.
- **Lovi 2024**: Blends **parameter estimates** in multiple-model MRAC. Same convex combination idea.
- **Zhang 2014**: Schedules **reference model** via convex combination with common Lyapunov.

The convex combination theorem is proven in MAPS 2025: if each Γⱼ is stabilizing and α ∈ simplex, then the convex blend is stabilizing. This argument is agnostic to whether the scheduled object is a gain matrix, a parameter estimate, or a reference model. **The theorem transfers to Γ(t) = Σⱼ αⱼΓⱼ.**

### Q4: Closest match to Γ(t) = Σⱼ αⱼ(t) · Γⱼ where α is frequency-dependent?

**Best match: MAPS 2025 + Zhang 2023 hybrid**

- **MAPS 2025** provides the convex combination theorem: `K = ΣμK⁽ⁱ⁾` with simplex μ is stable.
- **Zhang 2023** provides the frequency-as-scheduler mechanism: `αᵢ = band_power(Ωᵢ) / total_power` from sliding-window FFT.

The thesis's architecture is: replace MAPS's IMM mode probabilities μ with Zhang's frequency band powers α, and replace the LPV gain K with the MRAC adaptation matrix Γ. The **stability theorem from MAPS** plus the **frequency-scheduling mechanism from Zhang** gives the complete picture.

**The missing paper**: A paper that does `Γ(t) = Σⱼ αᵢ(t; freq) · Γⱼ` with Lyapunov stability proof. **This is the thesis contribution** — the synthesis of convex-combination stability theory (MAPS) with frequency-derived scheduling (Zhang) applied to MRAC adaptation matrices.

---

## 8. Novelty Assessment

| Claim | Literature status |
|---|---|
| **Γ(t) is time-varying in MRAC** | PROVEN — Gaudio 2019, I-DREM, multiple others |
| **Convex combination of stabilizing matrices is stable** | PROVEN — MAPS 2025 (gains), Zhang 2014 (reference models) |
| **Frequency features drive gain selection** | PROVEN — Zhang 2023 (hard switch), thesis uses soft blend |
| **Γ(t) = Σⱼ αⱼ(t; freq) · Γⱼ** | **NOT PUBLISHED** — thesis's specific contribution |
| **Stability proof for frequency-driven Γ convex blend in MRAC** | **NOT PUBLISHED** — needs new theorem |

**Blocking papers**: None found.

**Supporting papers**: MAPS 2025 (stability theorem), Zhang 2023 (frequency-as-scheduler), Gaudio 2019 (time-varying Γ in MRAC).

**Risk level**: LOW. The structural elements all exist in the literature. The synthesis is novel but builds on proven theorems. The main risk is whether the non-commutativity of matrix inversion (Γ⁻¹(ΣαΓ) ≠ ΣαΓ⁻¹) introduces error terms that invalidate the Lyapunov argument — this needs careful handling but is resolvable with the projection bounds from Gaudio 2019.

---

## 9. Recommended Citations for Thesis

**For the Γ(t) = Σⱼ αⱼΓⱼ architecture**:
- MAPS 2025 (arxiv:2509.12695) — convex combination theorem for simplex weights
- Zhang 2023 (arxiv:2306.01120) — frequency as scheduling variable (hard switch)
- Gaudio 2019 (arxiv:1911.03810) — time-varying Γ in MRAC with Lyapunov stability

**For stability of time-varying basis**:
- Gaudio 2019 — full Γ(t) matrix adaptation
- Goel 2022 (arxiv:2206.01700) — dual adaptation for time-varying parameters
- Patel 2023 (arxiv:2301.12285) — switched MRAC with memory

**For the frequency-scheduling mechanism**:
- Zhang 2023 — FD-EPF: spectral dominance ratio per band
- Cirrincione 2026 (arxiv:2605.06877) — attention over temporal history for gain generation (orthogonal but related)

**For the convex combination argument**:
- MAPS 2025 — K(μ) = ΣμK⁽ⁱ⁾, common Lyapunov
- Lovi 2024 — θ̂ = Σβθ̂⁽ⁱ⁾, MRAC parameter blending
- Zhang 2014 — A_m = ΣαA_m⁽ⁱ⁾, reference model scheduling
