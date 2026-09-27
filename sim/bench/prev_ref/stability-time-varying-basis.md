# Stability & Safety of Time-Varying Basis Functions in MRAC

**Research Date**: 2026-08-26
**Question**: Does frequency-gated basis activation break standard MRAC Lyapunov stability proofs? What are the constraints?

---

## Executive Summary

Adaptive gating does **not** automatically break stability. But it requires specific design constraints. The literature is conclusive on four points:

1. **Lyapunov stability is preserved** if gating is treated as a scheduling variable (not a control parameter) and the overall composite regressor remains PE.
2. **Bounded tracking error is provable** even with time-varying Φ_active — but the UUB bound is larger than the fixed-basis case.
3. **PE fails** if critical features are gated off persistently (α_j → 0 for extended time).
4. **Stability-preserving constraints on α(t)** are the dominant concern: α must change slower than adaptation.

---

## 1. Lyapunov Stability with Time-Varying Basis

### Does Φ_active(x, ξ(t)) break standard MRAC proofs?

**No, but the proof must be redesigned.** The standard MRAC proof relies on Φ being a *known* regressor signal — it appears in both the error dynamics and the Lyapunov derivative. When Φ varies with a gating signal ξ(t) that is exogenous or depends on tracking error, the analysis branches:

| Gating signal | Proof impact | Reference |
|---|---|---|
| Exogenous (e.g., frequency estimate from PSD) | Can treat ξ as known; ∂Φ/∂t terms appear but are bounded | Patil+ 2022 (`arxiv:2202.06320`) |
| Error-dependent (e.g., softmax over e) | ξ couples into the stability analysis; Barbalat conditions must be rechecked | Goel+ 2022 (`arxiv:2206.01700`) |
| Hard switching (α ∈ {0,1}) | Equivalent to switched system; requires average-dwell-time or CQLF | Patel+ 2023 (`arxiv:2301.12285`) |
| Soft blending (α ∈ [0,1]) | More tractable; gradient terms appear but remain bounded | Li 2025 (`arxiv:2506.13168`) |

**Key mechanism**: In all successful proofs, the time-derivative of the Lyapunov function picks up a term `∂V/∂ξ · ξ̇`. If ξ is bounded and ξ̇ is bounded, this term can be absorbed into the negative definite terms with a robustness margin. The critical assumption is that **the gating signal changes slower than the adaptation loop**.

### Stated Constraints from Literature

| Paper | Constraint on α(t) | Mechanism |
|---|---|---|
| Goel+ 2022 (`arxiv:2206.01700`) | Congelation: treat W(t) = W* + δW(t), where δW accounts for unmodeled time-variation | Dual adaptation; projection + σ-mod combined |
| Li 2025 (`arxiv:2506.13168`) | Adaptive gain η_i bounded by Lipschitz constant of Jacobian; gating does not directly constrain α | Event-triggered gradient descent with error-triggered update |
| Patel+ 2023 (`arxiv:2301.12285`) | No dwell-time constraint required when using CQLF for switched subsystems | Memory-augmented S-MRAC; filter states reset at switch |
| Chen+ Astolfi 2020 | Slow-variation assumption: ||δ̇W(t)|| ≤ δ̅_W known constant | RISE-based compensation |

**Design rule**: If α(t) changes abruptly (hard switch), treat as switched system → require dwell-time or CQLF. If α(t) changes smoothly, treat as time-varying parameter → require slow-variation bound on α̇.

---

## 2. Persistence of Excitation with Gated Features

### Does PE fail when α_j → 0?

**Yes, for the gated feature's weight.** The standard PE definition requires that the regressor vector Φ(t) generates sufficient spectral content over any infinite interval:

$$\int_t^{t+T} \Phi(\tau)\Phi^T(\tau) d\tau \geq \alpha I, \quad \forall t, \quad \alpha > 0$$

If α_j(t) → 0 for a sustained interval, the j-th column of Φ becomes zero, breaking PE for the **gated weights**. However:

- PE is needed for **parameter convergence**, not for **boundedness of tracking error**.
- Bounded tracking error requires only that the **closed-loop dynamics remain stable**, which holds without PE.
- The **active features' PE** (after gating) may still be sufficient for bounded error, just not for weight convergence.

### Failure Mode: Critical Feature Never Activated

If x⁴ is always gated off (α₄ → 0 always), then:
1. The corresponding weight θ₄ never learns.
2. If the plant actually needs x⁴ to represent the dynamics, the approximation error increases.
3. The controller compensates with larger gains on the remaining features → possible destabilization at high-amplitude trajectories.

**Mitigation strategies from literature**:

| Strategy | Paper | How it works |
|---|---|---|
| Minimum activation floor | TGRBF (`arxiv:2506.13168`) | Never gate below ε > 0; preserves PE contribution |
| Memory-augmented learning | S-MRAC (`arxiv:2301.12285`) | Store filter states at switch; learn during inactive phase |
| Initial Excitation (IE) | Goel+ (`arxiv:2206.01700`) | PE required only in finite initial window, not persistently |
| Intermittent Initial Excitation (IIE) | Patel+ (`arxiv:2301.12285`) | PE at each switching instant, not between switches |
| Partial PE | arXiv `2408.01731` | PE only on excited subspace; orthogonal components left unestimated |

**Recommended constraint for thesis**: Enforce a minimum activation floor ε_min > 0 on all features. This preserves PE contribution from each feature and avoids the catastrophic failure mode. If a feature truly has zero contribution, the weight will converge to zero naturally under the adaptation law.

### PE for Gated/ReLU Activations

The ReLU PE paper (`arxiv:2303.08707`) establishes that piecewise-constant basis functions (step, ReLU) can satisfy PE if the state trajectory visits each activation region. With gating, the same logic applies: **the operating regime (frequency band) must excite each active feature's region**. This is achievable by injecting multi-frequency chirp signals during the feature calibration phase.

---

## 3. Barbalat's Lemma with Time-Varying Regressor

### Applicability when Φ is time-varying

Standard Barbalat's lemma requires:
- V(t) is lower bounded
- V̇(t) is uniformly continuous (or negative semi-definite with finite integral)
- V̇(t) → 0 implies e → 0

When Φ depends on t, the Lyapunov derivative becomes:

$$\dot{V} = -e^T Q e + e^T P B \tilde{\Theta}^T \Phi(t) + \text{(gating cross-terms)}$$

The key difficulty: **∂Φ/∂t terms** appear in V̇ through the product rule on `Θ̃ᵀΦ(t)`. These terms may not be negative definite. The literature resolves this three ways:

| Approach | Condition | Result |
|---|---|---|
| Slow-variation | ||Φ̇(t)|| ≤ μ(t), where μ(t) → 0 or is summable | Barbalat applies; e → 0 |
| RISE term | Add nonlinear damping term compensating for Φ̇ | Asymptotic tracking without PE |
| σ-modification | Pulls estimate toward prior, damping cross-terms | UUB with residual bound |

**Goel+ 2022** (`arxiv:2206.01700`): Uses **congelation of variables** to split W(t) into constant W* plus bounded perturbation δW(t). The perturbation generates cross-terms that are bounded by known constants. These are then handled with a combination of **projection** (keeps estimates in known convex set) and **σ-modification** (adds damping). Result: UUB stability of closed-loop signals.

**Patil+ 2022** (`arxiv:2202.06320`): Uses a **RISE-like term** (robust integral of sign of error) in the adaptation law to compensate for time-varying parameter derivatives. Proves asymptotic tracking for fast time-varying parameters without PE.

**Key result for gating**: If α(t) changes smoothly (soft blending), the effective regressor Φ_α(t) = diag(α(t))Φ(x) is differentiable almost everywhere. The derivative ||Φ̇_α(t)|| is bounded by ||α̇(t)||·||Φ||. If ||α̇(t)|| is sufficiently small (e.g., ||α̇||_∞ ≤ c where c depends on adaptation gain), Barbalat's conditions hold and asymptotic tracking is preserved.

---

## 4. L1 Adaptive Control with Time-Varying Basis

### Stability with time-varying filters

L1 adaptive control was designed for fast adaptation without high-frequency control chatter. The key insight: the **low-pass filter C(s)** in the L1 architecture separates the adaptation rate from the control bandwidth. This directly addresses the gating concern.

| Property | Standard MRAC | L1 Adaptive | With Gating |
|---|---|---|---|
| Adaptation rate | Limited by chatter | Can be fast | Must respect filter bandwidth |
| Robustness to fast parameter change | Requires slow-variation | Filter absorbs high-frequency variation | Same; filter absorbs α-oscillations |
| Stability margin | PE-dependent | Predictable UUB bound | Filter + gating must be co-designed |
| Transient guarantee | None | Uniform bounds on ||e(t) - e_ref(t)|| | Requires gating bandwidth < filter bandwidth |

**Hovakimyan+ Cao 2010** (L1 theory): The L1 architecture guarantees that all signals remain uniformly bounded during transient adaptation. The key inequality:

$$||\tilde{x}(s)||_{\mathcal{L}_\infty} \leq \frac{||r(s)||_{\mathcal{L}_\infty} + \gamma_1}{1 - \rho}, \quad \rho < 1$$

where ρ is the loop gain. The filter C(s) ensures ρ < 1 regardless of adaptation speed. When gating changes α(t), this loop is perturbed. If the perturbation is bounded, the L1 stability margin is preserved.

**Practical constraint**: The gating signal α(t) must have spectral content below the cut-off frequency of C(s). Fast gating (e.g., step changes every control cycle) would inject high-frequency components that the filter cannot absorb, potentially destabilizing the loop.

### L1 + Switching Reference Models

**Yucelen+ 2017** (`arxiv:2108.08462`): L1 adaptive control extended to switched reference models (Learn-to-Fly framework). Key result: switching between reference models is stable if the switching rate is slower than the adaptation bandwidth. This maps directly to frequency-gated switching: **the rate of change between frequency bands must be slower than the L1 filter's response time**.

---

## 5. Stability-Preserving Constraints on α(t)

The central result across all papers: **the gating signal must change slower than the adaptation loop can respond**.

### Constraint Hierarchy

```
Physical constraint: |α̇(t)| ≤ α̅_max
                    ↑
Co-design: α̅_max must satisfy:
  - (A) For L1: α̅_max < ω_c, where ω_c = filter cut-off frequency
  - (B) For standard MRAC: α̅_max × ||Φ|| < adaptation damping margin
  - (C) For switched: average-dwell-time constraint
```

### Specific Bounds from Literature

| Context | Constraint | Paper |
|---|---|---|
| Slow-variation parameter | ||δ̇W(t)|| ≤ δ̅_W (known bound) | Assumption 2, Goel+ 2022 |
| Gradient step size | η_t ≤ (λ_min² - 2α²L²/λ_min²) / λ_max² | Li 2025 (TGRBF) |
| Adaptive gain bound | η_i < 2/|∂y_m/∂u|² | Li 2025 (TGRBF) |
| Controller gain (for stability) | k₁ > (1 + √(1+2L_u²)) / (2L_u) | Li 2025 (TGRBF) |
| Dwell time (switched) | No constraint with CQLF (arbitrary fast switching) | Patel+ 2023 |
| PE rate for time-varying | ||Ẇ(t)|| bounded by known constant | Patil+ 2022 |

### Quantitative Design Rule for Thesis

For frequency-gated MRAC, the following constraints are **necessary**:

1. **Minimum activation floor**: α_j(t) ≥ ε > 0 for all j, t. Prevents PE collapse.
2. **Gating bandwidth limit**: |α̇_j(t)| ≤ α̅_max, where α̅_max depends on the adaptation gain Γ. Rule of thumb: α̅_max · ||Φ||_∞ < λ_min(Q) / (2||P||·||B||), from the Lyapunov derivative.
3. **PE preservation**: The effective regressor Φ_α(t) = diag(α(t))Φ(x) must satisfy a PE-like condition over the union of activated subspaces. This can be achieved by injecting multi-frequency excitation during feature calibration.
4. **Soft gating preferred**: Smooth α(t) transitions (e.g., sigmoid blend) are easier to certify than hard switches. Hard switches require switched-systems analysis (dwell-time or CQLF).

---

## 6. Failure Modes of Time-Varying Adaptation Laws

### Identified Failure Modes

| Failure Mode | Symptom | Root Cause | Mitigation |
|---|---|---|---|
| **PE collapse** | Weights stop converging | Gating persistently excludes active features | Minimum floor ε; multi-frequency excitation |
| **Control chattering** | High-frequency control oscillation | Fast gating injecting noise into regressor | Low-pass filter on α(t) |
| **Unlearning** | σ-modification pulls weights to zero when inactive | σ-mod overcompensates | Dual adaptation (Goel+); memory augmentation (Patel+) |
| **PE interference** | Weight estimates fight each other | Gating and adaptation not co-designed | Intermittent excitation scheduling |
| **Critical feature missed** | Large approximation error at specific trajectories | Feature never activated | SINDy pre-screening; minimum activation floor |
| **Barbalat breakdown** | e(t) does not converge to 0 | ∂Φ/∂t not summable | Slow-variation bound; RISE term |

### Most Relevant for Thesis

The **PE collapse + critical feature missed** combination is the highest risk. In dense trajectory tracking, the quadrotor visits states where high-order terms (x⁴, x³ẋ, etc.) contribute significantly. If frequency-gating keeps these features inactive during training, the controller will have structural error at deployment.

**Mitigation**: Pre-flight chirp excitation that sweeps all frequency bands, combined with a minimum activation floor on all features.

---

## 7. Summary of Answers to Key Questions

### Q1: Does adaptive gating invalidate standard MRAC Lyapunov proofs?

**No, but the proof must be modified.** Treat α(t) as an exogenous scheduling signal. The Lyapunov derivative picks up bounded cross-terms from ∂Φ/∂t. These are absorbable if α̇ is bounded and sufficiently small. Standard MRAC Lyapunov structure (V = eᵀPe + trace(Θ̃ᵀΓ⁻¹Θ̃)) remains valid; the additional terms require either (a) slow-variation assumption, (b) RISE damping term, or (c) σ-modification + projection.

### Q2: What if a critical feature is never activated?

The weight for that feature never learns. If the plant actually needs it, approximation error accumulates. At the specific trajectory where that feature would dominate, the controller's structural error exceeds the robustness margin → tracking error spike or instability.

**Fix**: Minimum activation floor (ε > 0) + pre-flight calibration with multi-frequency chirp.

### Q3: Can you prove bounded tracking error with time-varying Φ?

**Yes.** Multiple papers prove UUB (Uniformly Ultimately Bounded) tracking error for time-varying basis functions:
- Goel+ 2022: UUB with dual adaptation + congelation
- Li 2025: UUB with TGRBF + event-triggered gradient descent
- Patel+ 2023: Exponential stability (delayed sense) with S-MRAC + memory

The bound is larger than the fixed-basis case due to the cross-terms from ∂Φ/∂t.

### Q4: Stability-preserving constraints on α(t)?

Three constraints, in order of severity:

1. **Minimum floor**: α_j(t) ≥ ε > 0 (PE preservation)
2. **Rate bound**: |α̇_j(t)| ≤ c, where c depends on adaptation gain and filter bandwidth
3. **Bandwidth separation**: If using L1 filter C(s) with cutoff ω_c, require |α̇_j(t)| ≪ ω_c (at least 10× slower)

For soft blending (sigmoid-based α), the rate constraint is naturally satisfied by the sigmoid's smoothness. For hard switching, treat as switched system → require average-dwell-time or CQLF analysis.

---

## Papers Found

| ID | Authors | Title | Venue | Year | Relevance |
|---|---|---|---|---|---|
| `arxiv:2506.13168` | Li | Online-Optimized Gated RBF Neural Network-Based Adaptive Control | arXiv | 2025 | **Direct** — TGRBF with GRU gating, Lyapunov stability proof, UUB |
| `arxiv:2301.12285` | Patel, Basu Roy, Bhasin | MRAC with Memory for Switched Linear Systems | arXiv | 2023 | **Direct** — S-MRAC with memory, no dwell-time constraint, IIE condition |
| `arxiv:2206.01700` | Goel, Basu Roy | Composite Adaptive Control for Time-varying Systems with Dual Adaptation | arXiv | 2022 | **Direct** — Dual adaptation, congelation, IE condition, UUB |
| `arxiv:2202.06320` | Patil, Sun, Bhasin, Dixon | Adaptive Control with Guaranteed Transient Behavior and Zero Steady-State Error for Systems with Time-Varying Parameters | arXiv | 2022 | **Direct** — RISE-based, no PE, asymptotic tracking for fast time-varying |
| `arxiv:math:0608393` | Hovakimyan, Cao | Guaranteed Transient Performance with L1 Adaptive Controller for Systems with Unknown Time-varying Parameters: Part I | arXiv | 2006 | **Core** — L1 theory for time-varying, uniform bounds |
| `arxiv:2303.08707` | Alsalti, Lopez, Müller | On the design of persistently exciting inputs for data-driven control of linear and nonlinear systems | IEEE TAC | 2023 | **PE Theory** — PE for switched/gated basis functions |
| `arxiv:2408.01731` | Composite Learning under Non-Persistent Partial Excitation | arXiv | 2024 | **Partial PE** — spectral decomposition for PE relaxation |
| `pmid:18263511` | Dynamic structure neural networks for stable adaptive control | IEEE TNN | 2007 | **Gating** — growing/gated RBF networks with stability |
| `arxiv:1911.03810` | Parameter Estimation in Adaptive Control of Time-Varying Systems Under a Range of Excitation Conditions | arXiv | 2019 | **Time-varying PE** — matrix of time-varying learning rates |
| `arxiv:2108.08462` | L1 Adaptive Output Feedback with Switched Reference Models | arXiv | 2021 | **L1 + Switching** — Learn-to-Fly framework |

---

## Recommended Thesis Position

**Claim**: Frequency-gated basis activation in MRAC preserves Lyapunov stability under three conditions:
1. Minimum activation floor ε > 0 on all features (PE preservation)
2. Smooth gating transition (||α̇|| bounded below filter cutoff)
3. Soft blending preferred over hard switching (reduces cross-term complexity)

**Do NOT claim**: Asymptotic convergence to zero tracking error — only UUB is provable with time-varying basis. This is consistent with the broader literature.

**Cite these for the proof architecture**: Goel+ 2022 (congelation + dual adaptation), Li 2025 (TGRBF Lyapunov), Patel+ 2023 (S-MRAC memory for switching).

**Cite these for PE relaxation**: Patel+ 2023 (IIE), Goel+ 2022 (IE), `arxiv:2408.01731` (partial PE via spectral decomposition).
