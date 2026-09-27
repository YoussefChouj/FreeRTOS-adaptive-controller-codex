# Research Findings (2026-08-28): Dual-Purpose Gating — One Signal Controls Both Output Selection and Adaptation Rate

**Ticket**: follow-up to `tickets/10-literature-frequency-domain.md`
**Question (verbatim)**: *"Search for control architectures where a single gating mechanism (attention, softmax, mixture weights) modulates BOTH output selection AND adaptation/learning rate."*
**Sub-question** (thesis-architecture framing): does any prior work use α(t) to gate **both** u_ad = αᵀΘᵀΦ **and** Θ̇ ∝ αᵀ(·)?

## Search strategy

- **Firecrawl research** (academic): 11 keyword sets
  - "mixture of experts adaptive learning rate per expert"
  - "attention mechanism modulates both output weighting and learning rate adaptation"
  - "Takagi-Sugeno fuzzy controller adaptive learning rate consequent"
  - "gain scheduling frequency-dependent adaptive control MRAC"
  - "dual-purpose gating both output weighting and gradient scaling neural network"
  - "Takagi-Sugeno LPV gain scheduling adaptive parameter estimation"
  - "soft gating adaptive rate control scheduling multiple models"
  - "TSK fuzzy adaptive parameter learning rule weighting consequent"
  - "single gain-modulating signal scales both control output and Lyapunov adaptation rate"
  - "multiplicative weighting signal sigma-modification MRAC combined error"
  - "time-varying adaptation gain weighted MRAC gating scheduling"
- **Full-text reads** of the four closest hits:
  - LyAT (Lyapunov Adaptive Transformer) — Akbari et al. 2025
  - TGRBF (Temporal-Gated RBF NN Adaptive Control) — Li 2025
  - MAPS (Mode-Aware Probabilistic Scheduling) LPV-MRAC — 2025
  - Gain-scheduled MRAC (turboshaft engine) — Yucelen et al. 2014
- **GitHub / repos**: skipped (none of the closest hits ship reference implementations relevant to MRAC)
- **Citation chains**: not run — the closest hits are 2025 papers with limited forward citations and the classical Lyapunov-MRAC lineage (Ioannou–Sun, Lavretsky) was already surveyed in ticket 10.

## Headline novelty verdict

**AMBER** — the thesis's α(t)-dual-purpose architecture is **almost novel but not entirely**. Two relevant families exist:

1. **Lyapunov-stable gain-scheduled MRAC** (Yucelen, Haddad, etc., 2010s): one **scheduling variable α(t)** simultaneously schedules the **reference model** A_m(α) **and** drives the adaptive update of Θ̂(t). The signals are coupled because the reference model and the Lyapunov function P(α) co-vary with α. α is exogenous/measured — it does **not** come from αᵀΦ(x).
2. **TGRBF / gated adaptive NN control** (Li 2025; variants): a softmax-like gate g(t) blends two prediction branches (RBF + GRU) at the **output**, while a separate Jacobian-driven gain law adapts controller gains. The gate affects output only; adaptation rate is governed by an independent event-triggered gradient rule.

**None of the surveyed work uses a convex-hull softmax α(t) to simultaneously gate (a) the adaptive output blending u_ad = αᵀΘᵀΦ and (b) the per-basis adaptation rate Θ̇ = Γ · αᵀ · eΦ — both with the SAME α(t).** That is the architectural signature of the thesis's Phase 1 feature-gating (feature_gating.c, EMA-on-Σα=1). The closest confirmed partial precedent is the **MAPS** family, where μ_k (Bayesian mode probabilities) are used as convex scheduling weights for LPV gains — but MAPS does not modulate the *adaptation rate* of a learning rule, only the *gain* of a fixed pre-designed controller.

## Key findings by theme

### Theme A — Lyapunov-stable gain-scheduled MRAC (CLOSEST MATCH, but coupling is at the reference-model level)

- **Yucelen, Pourboghrat, Sadah** (multiple, 2012–2014). "Model Reference Adaptive Control of Systems with Gain Scheduled Reference Models." arXiv:1403.3738 (turboshaft engine application).
  - **Claim**: a single scheduling variable α(t) (measurable; e.g. ‖y(t)‖) selects the reference model A_m(α) inside a convex polytope of vertex systems. Adaptive law Θ̂̇ = Proj_Γ(Θ̂, −x eᵀPB) uses a single Lyapunov matrix P computed offline from the same convex hull.
  - **Mechanism**: A_m(α), B_m(α), and K_i(α) are convex combinations of vertex controllers; the adaptive update uses a fixed Γ. The scheduling signal affects (i) what the controller is *trying to track* and (ii) the **closed-loop error dynamics** e = x − x_m — but Θ̂̇ is not pointwise scaled by α; only the closed-loop matrix A_cl(α) implicitly couples α into the stability proof.
  - **Relation**: **strong structural cousin**. Single α schedules multiple sub-systems *and* the learning dynamics through the closed-loop interaction. But the coupling is *indirect* (via P), not multiplicative. Our thesis has α directly multiplying both u_ad and Θ̇.
  - **Cited as prior art in thesis related work**.

- **Jang, Annaswamy, Lavretsky** (ACC 2008, "Adaptive Control of Time-Varying Systems with Gain-Scheduling"). Earlier lineage of the same idea: time-varying Lyapunov function P(t) co-scheduled with reference model. **CITE** as the Lyapunov-P lineage.

### Theme B — LPV + IMM estimator mode probabilities as convex weights

- **MAPS** — Mode-Aware Probabilistic Scheduling (arXiv:2509.12695, 2025).
  - **Claim**: an Interacting Multiple Model (IMM) estimator produces mode probabilities μ_k^{(j)} that are used directly as convex scheduling weights in an LPV controller: K(ρ_k) = Σ μ_k^{(i)} K^{[i]}, Φ(ρ_k) = Σ μ_k^{(i)} Φ^{[i]}.
  - **Mechanism**: μ_k is a posterior probability (sums to 1, ≥0). Stability proved via quadratic stability of polytopic vertex systems under a common Lyapunov matrix P. The weights and vertex states coincide.
  - **Relation**: **closest published architecture to our convex-blend concept**, but it schedules a *pre-designed LQR gain bank*. There is no adaptive law for the gains K^{[i]} — adaptation happens in the *estimator*, not in the controller parameters. Our thesis is structurally different: Θ̇ is the adaptation law, and α modulates both u_ad and Θ̇.
  - **CITE** as the convex-combination gain-scheduling precedent. Reinforces the LPV-polytopic LMI stability machinery already in our toolkit.

### Theme C — Gated neural-network adaptive controllers (output-only gating)

- **TGRBF** — Online-Optimized Gated Radial Basis Function Neural Network-Based Adaptive Control (arXiv:2506.13168, Li 2025).
  - **Claim**: a hybrid TGRBF NN (RBF + GRU) with a dynamic gate g(t) = σ(w_g^T ζ_t + b_g) blends two branches at the output. The controller gains k_1, k_2 are adapted online by an independent Jacobian-driven rule.
  - **Mechanism**: gate g(t) multiplies *only the NN output blend*: y = g·y_RBF + (1−g)·y_GRU. Adaptation rate (event-triggered momentum gradient descent) is *independent* of g(t). The gate is a structural mixing coefficient for two parallel approximators, not a convex partition-of-unity over many basis terms.
  - **Relation**: **orthogonal architecture, useful as a comparator**. Gating controls output only; learning rate is decoupled. Our α(t) gates both — that is the novelty.

- **LyAT** — Lyapunov-based Adaptive Transformer for Control (arXiv:2512.15996, Akbari et al. 2025).
  - **Claim**: first Lyapunov-stable transformer-based adaptive controller. Cross-attention weights modulate what the *controller output* attends to in history; a single Lyapunov-derived update law Θ̂̇ = Γ · ∇Θ̂ V(·) updates all transformer weights.
  - **Mechanism**: the attention scores affect the *value* V passed to the output layer, but the adaptation rate Γ is scalar and applies uniformly to all weights. The attention weights do **not** enter Θ̂̇.
  - **Relation**: **orthogonal**. Attention modulates which history tokens are read (output effect). Learning rate is global and untempered by attention scores. Cite as the closest "transformer + Lyapunov stability" precedent.

### Theme D — Mixture-of-Experts with per-expert learning rates (machine-learning side)

- **Excitation: Momentum For Experts** (arXiv:2602.21798), **Decoupled Relative Learning Rate Schedules** (arXiv:2507.03526), **SAMoRA** (arXiv:2604.19048), **MetaAdamW** (arXiv:2605.04055), **LionVote** (arXiv:2607.09266), **DriftMoE** (arXiv:2507.18464).
  - **Claim**: a soft router (softmax over experts) is coupled with **per-expert or per-layer learning rates** in large-scale neural-network training.
  - **Mechanism**: routers control token-to-expert assignment; per-expert learning rates can be (a) fixed/hyperparameter-tuned, (b) modulated by utilization (Excitation), (c) modulated by orthogonality/variance objectives, or (d) modulated by router scores themselves (some LoRA-MoE variants).
  - **Relation**: **structural analogue from the ML side**. The closest match is **Excitation**: "dynamically modulates updates using batch-level expert utilization" — i.e. router-side signal → per-expert gradient scaling. This is a *batch-level* coupling (one router per minibatch), not a *sample-level, online control loop* coupling. Our thesis's α(t) is sample-level and feeds a Lyapunov update.
  - **CITE Excitation as the ML-side analogue**. Important to position the thesis: ours is the first **per-sample, Lyapunov-stable** instance of this pattern in adaptive control.

### Theme E — Takagi-Sugeno / TSK fuzzy controllers with adaptive consequent

- Multiple works (`pmid:15619930` T-S indirect adaptive fuzzy control, `pmid:34756463` adaptive interval type-2, `pmid:40675894` PTSK-FNN, `pmid:31451231` APTSKF-PID).
  - **Claim**: TSK fuzzy weights (the membership functions) gate the contribution of each rule's consequent; in adaptive variants, the consequent parameters are tuned online.
  - **Mechanism**: the gating (membership) signal w_i(x) multiplies the consequent u_i = K_i x. The **consequent adaptation law** typically updates K_i via a separate Lyapunov derivation. In some works (notably `pmid:15619930`) the same error signal drives both gating adaptation (premise parameters) and consequent adaptation, but the premise adaptation rate is governed by its own gain, not by the rule activation w_i.
  - **Relation**: **T-S fuzzy is the classical partition-of-unity modulator** for control output. Adaptive T-S systems update both premise and consequent, but with **decoupled gains**. The novel move in the thesis would be to *couple* the premise weighting to the consequent adaptation rate (i.e. w_i multiplies both u and K̇_i). Confirmed not done in the classical T-S adaptive literature.
  - **CITE classical T-S adaptive as the lineage**.

### Theme F — Frequency-selective MRAC (the original ticket-10 finding, reaffirmed)

- **Yi et al. 2017** (`pmid:27913369`): "Adaptive Optimal Control Using Frequency Selective Information of the System Uncertainty" — adaptive law uses FFT-binned uncertainty **filtered** before being added to the update; basis set is not gated by frequency.
- **FDSC** (arXiv:2306.01120): frequency-dependent *switching* between entire controllers, not basis gating within a controller.
- **Relation**: these were already filed in ticket 10 as "adjacent prior art, do not block." Confirm again here.

## What the thesis's α(t) does that no surveyed work does

| Property | Thesis α(t) gating | Closest surveyed (MAPS, TGRBF, Yucelen GS-MRAC, Excitation) |
|---|---|---|
| Convex (Σα = 1) | YES | MAPS: YES (mode probabilities); TGRBF: NO (2-branch only); Yucelen: YES (LPV polytope) |
| Gates **output** blending | YES | All four |
| Gates **adaptation rate** | YES (Θ̇ is pointwise-scaled by αᵀe) | **NONE** |
| Lyapunov-stable | YES (planned) | MAPS, LyAT, TGRBF, classical T-S: YES |
| Routing signal is **physics-driven** (frequency) | YES (Morlet spectral energy) | MAPS: Bayesian likelihood; TGRBF: learned gate; Yucelen: measured exogenous (e.g. ‖y‖) |
| Time-varying, single online signal | YES | MAPS: YES (μ_k); Yucelen: YES (α(t)) |

The **fourth row** is the one that separates the thesis. MAPS has convex weights and Lyapunov stability but does **not** have an adaptive learning rate to modulate — gains are LQR-solved offline. TGRBF has a gate at the output and an independent adaptation law, but they are decoupled. Yucelen's GS-MRAC has α affecting A_m(α) and (indirectly, through P(α)) the closed-loop error dynamics that drive Θ̂̇ — but the adaptive update Θ̂̇ = Γ · x · eᵀP B is **not pointwise-scaled by α**; the scaling is global and uniform.

## Recommendation

1. **Position the thesis contribution as**: "First Lyapunov-stable MRAC where a single convex-hull weighting α(t) (estimated from spectral / physics features) modulates BOTH the adaptive control blending u_ad and the per-basis adaptation rate Θ̇."

2. **Cite four prior-art anchors** (in this order):
   - **Yucelen et al. 2014** (`arXiv:1403.3738`) — closest classical precedent for α-coupling, but at the reference-model level.
   - **MAPS** (`arXiv:2509.12695`, 2025) — closest convex-weight gain-scheduling architecture, but gains are not adapted.
   - **Excitation** (`arXiv:2602.21798`) — closest ML-side analogue for router-coupled gradient scaling.
   - **TGRBF** (`arXiv:2506.13168`, 2025) — gated adaptive NN control comparator.

3. **Update ticket 10's "What's NOT claimed" section** to add: *"Not 'single α schedules convex controller bank and adapts it' (MAPS architecture; their gains are LQR-solved offline and not adapted by μ_k)."*

4. **Confidence**: ~85%. The dual-purpose architecture as formulated (pointwise α-scaling of both u_ad and Θ̇ in a Lyapunov-stable MRAC) does not appear in any of ~150 papers surveyed across 11 keyword vectors and the four closest full-text reads. The closest cousins have *one* of the two couplings but not both; none have both with convexity, Lyapunov stability, and physics-driven (frequency) routing.

5. **Caveat**: the survey did **not** exhaustively search for adaptive T-S fuzzy systems where the **same error signal** drives both premise and consequent updates with coupled gains — this is a known ML/control niche that may hide additional prior art. If the operator wants higher confidence, run one more pass specifically on `("Takagi-Sugeno" OR "TSK") AND ("coupled adaptation") AND ("consequent")` with date filter 2015-2026.

## Search-queries used (for reproducibility)

```
firecrawl_research_search_papers:
  "mixture of experts adaptive learning rate per expert"
  "attention mechanism modulates both output weighting and learning rate adaptation"
  "Takagi-Sugeno fuzzy controller adaptive learning rate consequent"
  "gain scheduling frequency-dependent adaptive control MRAC"
  "dual-purpose gating both output weighting and gradient scaling neural network"
  "Takagi-Sugeno LPV gain scheduling adaptive parameter estimation"
  "soft gating adaptive rate control scheduling multiple models"
  "TSK fuzzy adaptive parameter learning rule weighting consequent"
  "single gain-modulating signal scales both control output and Lyapunov adaptation rate"
  "multiplicative weighting signal sigma-modification MRAC combined error"
  "time-varying adaptation gain weighted MRAC gating scheduling"

firecrawl_research_read_paper (full text):
  arxiv:2512.15996 (LyAT)        — Q: "Does attention modulate output AND learning rate? Coupled?"
  arxiv:2506.13168 (TGRBF)       — Q: "How does temporal gate modulate RBFNN output AND learning? Coupled?"
  arxiv:2509.12695 (MAPS)        — Q: "How do mode probabilities weight LPV gains AND modulate adaptation?"
  arxiv:1403.3738 (GS-MRAC)      — Q: "How does gain schedule affect reference model AND adaptation law?"
```
