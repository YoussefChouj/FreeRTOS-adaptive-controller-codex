# Adaptive Basis Architecture with RBF Spectral Gating

**Version**: 1.0  
**Date**: 2026-08-26  
**Status**: Design Review  
**Reviewer**: Adversarial Agent (TBD)

---

## Executive Summary

This specification defines a **three-layer adaptive control architecture** that dynamically activates physics-informed basis functions based on **continuous spectral content** represented as **Radial Basis Functions (RBFs)**. The architecture combines:

1. **Layer 1**: Physics-informed basis functions (12 features)
2. **Layer 2**: Reactive spectral analysis (RBF-weighted FFT of telemetry)
3. **Layer 3**: Predictive spectral analysis (RBF-weighted FFT of reference model)
4. **Adaptive fusion**: Confidence-weighted blend of predicted and actual spectral content

**Key innovation**: First adaptive control system to use **continuous RBF spectral representation** for basis function activation, enabling smooth frequency-dependent feature gating without hard band boundaries.

---

## 1. Motivation & Problem Statement

### 1.1 Problem

Standard MRAC uses a **fixed basis** `Φ(x)` that is active at all times:

```
θ̇ = Γ·Φ(x)·e·sgn(kp)
```

**Limitations**:
1. All basis functions contribute equally regardless of maneuver type
2. Low-frequency features (e.g., `x`) dominate during high-frequency maneuvers
3. High-frequency features (e.g., `x·tanh(x)`) add noise during slow maneuvers
4. No exploitation of **known reference trajectory** (feedforward opportunity)

### 1.2 Proposed Solution

**Adaptive basis activation** via spectral gating:

```
Φ_active(x, ξ) = α(ξ) ⊙ Φ(x)
```

Where:
- `ξ`: Spectral content (RBF activations)
- `α(ξ)`: Activation weights (learned mapping from spectral content to features)
- `⊙`: Element-wise (Hadamard) product

**Key insight**: Different frequency regimes require different basis functions. Activate features based on **what frequencies are present** in the tracking error dynamics.

---

## 2. Architecture Overview

### 2.1 Three-Layer Structure

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: Physics Basis                                      │
│ Input: x, u, xm                                             │
│ Output: Φ_physics = [x, x², x³, x·tanh(x), ...]  (12 feat) │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: Reactive Spectral (RBF)                            │
│ Input: state_history (256 samples)                          │
│ Output: ξ_actual = [ξ₁, ξ₂, ..., ξ₈]  (8 RBFs)             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: Predictive Spectral (RBF)                          │
│ Input: r_ref_future (50 steps), xm_current                  │
│ Output: ξ_expected = [ξ₁, ξ₂, ..., ξ₈]  (8 RBFs)           │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ Fusion: Adaptive Blending                                   │
│ β = f(confidence)  [0.2, 0.8]                               │
│ ξ_combined = β·ξ_expected + (1-β)·ξ_actual                  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ W Matrix: Spectral → Feature Mapping                        │
│ a(t) = W @ ξ_combined    (8 RBFs → 12 features)            │
│ W ∈ ℝ^{12×8}, learned or hand-designed                     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ Activation: Gate Physics Features                           │
│ Φ_active = a(t) ⊙ Φ_physics                                 │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 Data Flow

1. **Layer 1** computes physics features from current state
2. **Layer 2** analyzes past 256 samples → `ξ_actual` (reactive)
3. **Layer 3** simulates reference model 50 steps forward → `ξ_expected` (predictive)
4. **Fusion** blends predicted/actual based on confidence → `ξ_combined`
5. **W matrix** maps spectral content to feature activations → `a(t)`
6. **Activation** gates physics features → `Φ_active`
7. **MRAC** uses `Φ_active` for adaptation: `θ̇ = Γ·Φ_active·e·sgn(kp)`

---

## 3. Layer 1: Physics-Informed Basis Functions

### 3.1 Feature Set (12 Features)

**Polynomial basis** (4 features):
```
Φ₁ = x          # Linear term
Φ₂ = x²         # Quadratic
Φ₃ = x³         # Cubic
Φ₄ = x⁴         # Quartic
```

**Bounded nonlinear** (4 features):
```
Φ₅ = x·tanh(x)           # Soft saturation
Φ₆ = x·tanh(x²)          # Sharp saturation
Φ₇ = x/(1 + |x|)         # Rational saturation
Φ₈ = x²·tanh(x)          # Quadratic with saturation
```

**Cross-coupling** (2 features):
```
Φ₉ = x·u                 # State-control interaction
Φ₁₀ = (x - xm)²          # Squared tracking error
```

**Derivative-based** (2 features):
```
Φ₁₁ = ẋ                  # Velocity-dependent
Φ₁₂ = ẋ·tanh(x)          # Mixed velocity-position
```

### 3.2 Rationale

- **Polynomial**: Capture smooth nonlinearities (Taylor expansion)
- **Bounded**: Prevent unbounded growth at large states (actuator saturation)
- **Cross-coupling**: Model state-control and tracking error dynamics
- **Derivative**: Capture velocity-dependent effects (damping, friction)

### 3.3 Normalization

All features are normalized to `[-1, 1]` range:

```
Φ_norm[i] = Φ[i] / max(|Φ[i]|_over_training_data)
```

**Normalization constants** (example for roll axis):
```c
const float norm_constants[12] = {
    1.0,      // Φ₁: x (rad, already ≈1)
    1.0,      // Φ₂: x² (rad²)
    1.0,      // Φ₃: x³ (rad³)
    1.0,      // Φ₄: x⁴ (rad⁴)
    1.0,      // Φ₅: x·tanh(x)
    1.0,      // Φ₆: x·tanh(x²)
    1.0,      // Φ₇: x/(1+|x|)
    1.0,      // Φ₈: x²·tanh(x)
    10.0,     // Φ₉: x·u (rad·N·m, ≈10)
    0.1,      // Φ₁₀: (x-xm)² (rad², ≈0.1 during tracking)
    5.0,      // Φ₁₁: ẋ (rad/s, ≈5)
    5.0,      // Φ₁₂: ẋ·tanh(x) (rad/s, ≈5)
};
```

**These must be tuned** from logged flight data before Phase 1.

---

## 4. Layer 2: Reactive Spectral Analysis (RBF)

### 4.1 Radial Basis Function (RBF) Formulation

**Goal**: Represent spectral content as a **continuous distribution** over frequency, not discrete bands.

**RBF kernel**:
```
φᵢ(f) = exp(-(f - cᵢ)² / (2σ²))
```

Where:
- `cᵢ`: Center frequency of RBF `i` (Hz)
- `σ`: Bandwidth parameter (Hz), controls overlap
- `f`: Frequency (Hz)

**RBF activation** for center `cᵢ`:
```
ξᵢ = ∫ PSD(f)·φᵢ(f) df
```

In discrete form (FFT bins):
```
ξᵢ = Σₖ PSD[k]·exp(-(fₖ - cᵢ)² / (2σ²))
```

### 4.2 RBF Center Frequencies

**Phase 1** (hand-chosen, 8 centers):
```
centers = [0.5, 1, 2, 5, 10, 15, 20, 25]  # Hz
```

**Rationale**:
- **0.5 Hz**: Slow drift, wind gusts
- **1 Hz**: Low-frequency maneuvers
- **2 Hz**: Transition between slow/mid dynamics
- **5 Hz**: Typical UAV attitude bandwidth
- **10 Hz**: Fast attitude response
- **15 Hz**: Motor dynamics onset
- **20 Hz**: Motor response peak
- **25 Hz**: High-frequency structural modes

**Phase 2+**: Centers can be **learned** via gradient descent on RMSE.

### 4.3 RBF Bandwidth

**σ = 2 Hz** (moderate overlap)

**Justification**:
- Too small (σ < 1 Hz): RBFs become disjoint → loses composability
- Too large (σ > 5 Hz): RBFs merge → loses frequency resolution

**Overlap**: Each RBF overlaps ~50% with neighbors at σ = 2 Hz.

### 4.4 Implementation

**Precompute RBF kernels** (one-time, at init):
```c
// n_rbfs = 8, n_fft_bins = 129 (256-sample FFT)
float rbf_kernels[8][129];

void precompute_rbf_kernels(void) {
    for (int i = 0; i < 8; i++) {
        float c_i = centers[i];
        for (int k = 0; k < 129; k++) {
            float f_k = k * (fs / 256.0f);  // fs = 200 Hz
            rbf_kernels[i][k] = expf(-powf(f_k - c_i, 2) / (2 * sigma * sigma));
        }
    }
}
```

**At runtime** (every 5 ms):
```c
// Compute FFT of state_history (256 samples)
arm_rfft_fast_f32(&rfft_instance, state_history, fft_output, 0);

// Compute PSD
for (int k = 0; k < 129; k++) {
    float re = fft_output[2*k];
    float im = fft_output[2*k+1];
    psd[k] = re*re + im*im;
}

// Apply RBF kernels
for (int i = 0; i < 8; i++) {
    xi_actual[i] = 0.0f;
    for (int k = 0; k < 129; k++) {
        xi_actual[i] += psd[k] * rbf_kernels[i][k];
    }
}

// Normalize
float sum = 0.0f;
for (int i = 0; i < 8; i++) sum += xi_actual[i];
for (int i = 0; i < 8; i++) xi_actual[i] /= (sum + 1e-8f);
```

### 4.5 Computational Cost

**FFT**: 256-point real FFT via ARM CMSIS-DSP → **~0.3 ms**  
**RBF kernels**: 8 RBFs × 129 bins × 2 FLOPs = **2064 FLOPs** → **~0.01 ms**  
**Normalization**: 8 adds + 8 divides → **~0.001 ms**  
**Total per axis**: **~0.31 ms**  
**Total for 3 axes**: **~0.93 ms** (19% of 5 ms control period)

**Acceptable** ✓

---

## 5. Layer 3: Predictive Spectral Analysis (RBF)

### 5.1 Reference Model Simulation

**Goal**: Predict expected spectral content by simulating the reference model forward.

**Reference model** (per axis):
```
ẋm = Am·xm + Bm·r(t)
```

Where:
- `xm`: Reference model state (rad or m)
- `r(t)`: Reference trajectory (rad or m)
- `Am = -ωn`: Reference model pole (rad/s)
- `Bm = ωn`: Reference model gain

**Sliding window predictor**:
1. Maintain a circular buffer of 50 simulated samples
2. At each timestep, simulate one step forward: `xm(t+1) = xm(t) + ẋm·dt`
3. Append to buffer
4. Compute FFT of buffer → PSD
5. Apply RBF kernels → `ξ_expected`

### 5.2 Implementation

**Sliding window buffer** (50 samples per axis):
```c
float xm_lookahead_buffer[50];
int buffer_idx = 0;
```

**At runtime** (every 5 ms):
```c
// Simulate reference model one step
float xm_dot = ref_model_Am * xm_current + ref_model_Bm * r_ref_next;
float xm_next = xm_current + xm_dot * dt;

// Append to buffer
xm_lookahead_buffer[buffer_idx] = xm_next;
buffer_idx = (buffer_idx + 1) % 50;

// Compute FFT of buffer (once full)
if (buffer_filled) {
    arm_rfft_fast_f32(&rfft_instance_50, xm_lookahead_buffer, fft_output_50, 0);
    
    // Compute PSD
    for (int k = 0; k < 26; k++) {  // 50-sample FFT → 26 bins
        float re = fft_output_50[2*k];
        float im = fft_output_50[2*k+1];
        psd_50[k] = re*re + im*im;
    }
    
    // Apply RBF kernels (precomputed for 50-sample FFT)
    for (int i = 0; i < 8; i++) {
        xi_expected[i] = 0.0f;
        for (int k = 0; k < 26; k++) {
            xi_expected[i] += psd_50[k] * rbf_kernels_50[i][k];
        }
    }
    
    // Normalize
    float sum = 0.0f;
    for (int i = 0; i < 8; i++) sum += xi_expected[i];
    for (int i = 0; i < 8; i++) xi_expected[i] /= (sum + 1e-8f);
}
```

### 5.3 Computational Cost

**Ref model simulation**: 1 multiply-add → **~0.001 ms**  
**FFT (50 samples)**: **~0.05 ms**  
**RBF kernels**: 8 × 26 × 2 FLOPs = 416 FLOPs → **~0.005 ms**  
**Total per axis**: **~0.056 ms**  
**Total for 3 axes**: **~0.17 ms** (3.4% of 5 ms control period)

**Negligible** ✓

### 5.4 Lookahead Window Size

**50 steps @ 200 Hz = 0.25 seconds**

**Rationale**:
- Too short (<25 steps): Not enough samples for meaningful FFT (frequency resolution = fs/N = 200/25 = 8 Hz)
- Too long (>100 steps): Memory cost increases, older predictions become stale

**50 steps gives frequency resolution of 200/50 = 4 Hz** — sufficient to distinguish RBF centers.

---

## 6. Fusion: Adaptive Blending

### 6.1 Confidence Metric

**Goal**: Quantify how much to trust `ξ_expected` vs. `ξ_actual`.

**Metric**: Exponential moving average (EMA) of prediction error:

```c
float ema_error = 0.0f;  // Initialized to 0

void update_confidence(float xi_expected[8], float xi_actual[8]) {
    // Compute L2 error
    float error = 0.0f;
    for (int i = 0; i < 8; i++) {
        float diff = xi_expected[i] - xi_actual[i];
        error += diff * diff;
    }
    error = sqrtf(error);
    
    // Update EMA
    const float alpha = 0.1f;  // Smoothing factor
    ema_error = alpha * error + (1.0f - alpha) * ema_error;
}

float compute_confidence(void) {
    // Confidence inversely proportional to error
    return expf(-ema_error);  // Returns [0, 1]
}
```

**High confidence** (→ 1): `ξ_expected` closely matches `ξ_actual` → trust prediction  
**Low confidence** (→ 0): Large mismatch → trust telemetry

### 6.2 Adaptive β

**Map confidence to blending weight**:

```c
float adaptive_beta(void) {
    float confidence = compute_confidence();
    
    // Map [0, 1] → [0.2, 0.8]
    float beta = 0.2f + 0.6f * confidence;
    
    return beta;
}
```

**Bounds**: `β ∈ [0.2, 0.8]`
- Never fully trust one source (prevents catastrophic failures)
- Always blend feedforward + feedback

### 6.3 Blending

```c
float xi_combined[8];

void blend_spectral_content(void) {
    float beta = adaptive_beta();
    
    for (int i = 0; i < 8; i++) {
        xi_combined[i] = beta * xi_expected[i] + (1.0f - beta) * xi_actual[i];
    }
}
```

---

## 7. W Matrix: Spectral → Feature Mapping

### 7.1 Structure

**W** maps 8 RBF activations to 12 physics feature activations:

```
a(t) = W @ ξ_combined

W ∈ ℝ^{12×8}  (12 features × 8 RBFs)
```

Each entry `W[j,i]` represents: **"How much does RBF i activate feature j?"**

### 7.2 Initialization

**Phase 1** (hand-designed):

**Hypothesis**:
- Low-freq RBFs (0.5, 1, 2 Hz) → activate smooth features (x, x², cross-coupling)
- Mid-freq RBFs (5, 10 Hz) → activate moderate nonlinearities (x³, tanh)
- High-freq RBFs (15, 20, 25 Hz) → activate sharp nonlinearities (x⁴, x·tanh(x²))

**Example W** (row = feature, col = RBF):

```
         RBF: 0.5  1   2   5   10  15  20  25
Φ₁ (x):      1.0 1.0 0.8 0.5 0.2 0.1 0.0 0.0
Φ₂ (x²):     0.8 0.9 1.0 0.7 0.4 0.2 0.0 0.0
Φ₃ (x³):     0.3 0.5 0.7 1.0 0.8 0.5 0.2 0.0
Φ₄ (x⁴):     0.0 0.0 0.2 0.5 0.8 1.0 0.8 0.5
Φ₅ (tanh):   0.2 0.4 0.6 0.9 1.0 0.8 0.5 0.2
Φ₆ (tanh²):  0.0 0.1 0.3 0.6 0.9 1.0 1.0 0.8
Φ₇ (rat):    0.5 0.6 0.7 0.8 0.7 0.5 0.3 0.1
Φ₈ (x²tanh): 0.1 0.2 0.4 0.7 1.0 0.9 0.7 0.4
Φ₉ (x·u):    0.6 0.7 0.8 1.0 0.8 0.5 0.3 0.1
Φ₁₀ (e²):    0.8 0.9 1.0 0.8 0.5 0.3 0.1 0.0
Φ₁₁ (ẋ):     0.3 0.5 0.7 1.0 1.0 0.8 0.5 0.3
Φ₁₂ (ẋ·tanh): 0.2 0.4 0.6 0.9 1.0 1.0 0.8 0.5
```

**These are initial guesses** — will be refined via learning.

### 7.3 Learning (Phase 2)

**Gradient descent** on W to minimize tracking RMSE:

```
Loss = RMSE(x, xm)
W ← W - η·∇_W(Loss) - λ·W  (L1 regularization)
```

**L1 regularization** encourages sparsity:
- `λ = 0.01`: Moderate sparsity
- Entries with `|W[j,i]| < threshold` are pruned to zero

**Expectation**: Sparse W emerges naturally (≤20% nonzero entries).

### 7.4 SINDy Thresholding (Phase 2)

After learning dense W, apply **SINDy sparse regression**:

1. Learn dense W via gradient descent
2. Sort entries by magnitude: `|W[j,i]|`
3. Iteratively prune smallest entries, re-fit, check RMSE
4. Stop when RMSE degrades by >5%

**Output**: Sparse W matrix revealing **which RBF frequencies activate which physics features**.

---

## 8. Activation: Gate Physics Features

### 8.1 Gating Operation

```
Φ_active[j] = a[j] · Φ_physics[j]

where a[j] = Σᵢ W[j,i] · ξ_combined[i]
```

**Element-wise multiplication** (Hadamard product):
```c
for (int j = 0; j < 12; j++) {
    float a_j = 0.0f;
    for (int i = 0; i < 8; i++) {
        a_j += W[j][i] * xi_combined[i];
    }
    Phi_active[j] = a_j * Phi_physics[j];
}
```

### 8.2 Integration with MRAC

**Standard MRAC update law**:
```
θ̇ = Γ·Φ(x)·e·sgn(kp)
```

**With adaptive basis**:
```
θ̇ = Γ·Φ_active(x, ξ)·e·sgn(kp)
```

**Implementation** (replace `Phi` with `Phi_active` in existing MRAC code):
```c
// Before (fixed basis)
for (int j = 0; j < 12; j++) {
    theta_dot[j] = Gamma * Phi[j] * e * sgn_kp;
}

// After (adaptive basis)
for (int j = 0; j < 12; j++) {
    theta_dot[j] = Gamma * Phi_active[j] * e * sgn_kp;
}
```

**No other changes to MRAC** — architecture is modular.

---

## 9. Memory Budget

### 9.1 Per-Axis Breakdown

```
Component                        Size        Location
─────────────────────────────────────────────────────
state_history (256 samples)      1024 B      RAM
xm_lookahead_buffer (50 samples)  200 B      RAM
rbf_kernels (8×129, precomputed) 4128 B      Flash (.rodata)
rbf_kernels_50 (8×26, precomp)    832 B      Flash (.rodata)
W matrix (12×8)                   384 B      RAM or Flash
xi_actual (8 floats)               32 B      RAM
xi_expected (8 floats)             32 B      RAM
xi_combined (8 floats)             32 B      RAM
Phi_physics (12 floats)            48 B      RAM
Phi_active (12 floats)             48 B      RAM
FFT scratch buffers                512 B      RAM
EMA state (confidence)              4 B      RAM
─────────────────────────────────────────────────────
Total RAM per axis:              ~2.3 kB
Total Flash per axis:            ~5.0 kB
```

### 9.2 Three-Axis Total

```
RAM:   3 axes × 2.3 kB = 6.9 kB  (3.6% of 192 kB)
Flash: 3 axes × 5.0 kB = 15 kB   (1.5% of 1 MB)
```

**Plenty of headroom** ✓

### 9.3 Scalability

Can expand to **12 RBFs** or **16 physics features** without constraint:
```
12 RBFs × 16 features × 4 B = 768 B per axis
3 axes × 768 B = 2.3 kB (still <2% of RAM)
```

---

## 10. Computational Budget

### 10.1 Per-Axis Timing (@ 168 MHz STM32F4)

```
Operation                              Time (ms)
────────────────────────────────────────────────
Layer 1: Compute Phi_physics            0.02
Layer 2: FFT (256) + RBF kernels        0.31
Layer 3: Ref model sim + FFT (50)       0.06
Fusion: Confidence + blend              0.01
W matrix: 8×12 matmul                   0.01
Activation: 12 multiplies               0.001
────────────────────────────────────────────────
Total per axis:                         0.41 ms
```

### 10.2 Three-Axis Total

```
3 axes × 0.41 ms = 1.23 ms  (25% of 5 ms control period)
```

**Leaves 3.77 ms for**:
- MRAC updates (~0.5 ms)
- Control law (~0.2 ms)
- Communication (~1 ms)
- Overhead (~2 ms)

**Feasible** ✓

---

## 11. Phase Roadmap

### Phase 1 (Weeks 1-2): Validation

**Goal**: Prove hypothesis — does RBF-gated basis improve RMSE?

**Configuration**:
- 6 physics features: `[x, x², x³, x·tanh(x), x·u, (x-xm)²]`
- 8 RBFs, σ = 2 Hz, hand-chosen centers
- Fixed β = 0.5
- Hand-designed W (6×8 = 48 entries)

**Test trajectory**: Chirp (0.1 → 25 Hz)

**Success criterion**: RMSE improves by ≥10% vs. fixed basis

**Deliverables**:
- C firmware implementation
- Python simulation (validation)
- Logged data: `ξ_actual`, `ξ_expected`, `β`, `a(t)`, RMSE
- Plots: RBF activation heatmap, tracking error over time

---

### Phase 2 (Weeks 3-4): Expansion + Discovery

**Goal**: Discover fine-grained frequency→feature mappings.

**Configuration**:
- 12 physics features (full set)
- 8 or 12 RBFs (if memory allows)
- Adaptive β (confidence-based)
- Learned W (dense, L1-regularized)

**Test trajectories**: Chirp, sine, circle, figure-8

**Deliverables**:
- Dense W matrix (learned via gradient descent)
- Sparse W matrix (SINDy-thresholded)
- Visualization: W heatmap ("Smith chart for adaptive control")
- Analysis: Which RBFs activate which features?

---

### Phase 3 (Weeks 5-8): Meta-Learning

**Goal**: Learn compositional sub-patterns, test generalization.

**Configuration**:
- All of Phase 2, plus:
- Learned RBF centers (gradient descent on centers)
- Pattern library (Option B): cluster activation patterns from Phase 2 data
- Policy network: context → pattern composition

**Test trajectory**: Lemniscate (novel, unseen during training)

**Success criterion**: Sparse W generalizes to novel trajectory with <10% RMSE degradation

**Deliverables**:
- Optimized RBF centers
- Pattern library (sub-patterns discovered via clustering)
- Transfer learning results: chirp → figure-8 → lemniscate
- Final sparse W matrix for thesis

---

## 12. Validation Metrics

### 12.1 Primary Metrics

**Tracking RMSE**:
```
RMSE = sqrt(mean((x - xm)²))
```

**Target**: ≥10% improvement over fixed basis

**Sparsity**:
```
sparsity = (# zero entries in W) / (total entries in W)
```

**Target**: ≥80% sparsity (≤20% nonzero)

### 12.2 Secondary Metrics

**Adaptation rate**:
```
||θ̇|| over time
```

**Expect**: Faster convergence with adaptive basis

**Feature activation diversity**:
```
H(a) = -Σⱼ p(aⱼ)·log(p(aⱼ))  (entropy)
```

**Expect**: High entropy during chirp (all features active), low entropy during cruise (few features active)

**Confidence tracking**:
```
Plot: β(t), ema_error(t), ||ξ_expected - ξ_actual||(t)
```

**Expect**: β increases as flight stabilizes (reference model becomes more predictive)

---

## 13. Known Risks & Failure Modes

### 13.1 Risk: RBF Overlap Too High

**Symptom**: All RBFs activate equally regardless of frequency content.

**Cause**: σ too large (e.g., σ > 5 Hz)

**Mitigation**: Reduce σ to 1-2 Hz, visualize RBF kernels before deployment.

**Test**: Plot `φᵢ(f)` for all RBFs, verify <70% overlap between neighbors.

---

### 13.2 Risk: W Matrix Not Sparse

**Symptom**: SINDy produces dense W (>50% nonzero), no clear frequency→feature patterns.

**Cause**: L1 regularization too weak, or hypothesis is wrong (all features needed at all frequencies).

**Mitigation**: Increase λ (e.g., 0.01 → 0.1), try L0 regularization (hard thresholding).

**Falsifiability**: If dense W consistently outperforms sparse W by >20% RMSE, hypothesis is invalid.

---

### 13.3 Risk: Layer 3 Prediction Diverges

**Symptom**: `||ξ_expected - ξ_actual|| > 0.5` consistently, β → 0.2 (no trust in prediction).

**Cause**: Reference model mismatch (Am, Bm incorrect), or disturbances dominate dynamics.

**Mitigation**: Re-tune Am, Bm from system ID. If persistent, fall back to Layer 2 only (β = 0).

**Test**: Simulate in low-disturbance conditions (fixture, no wind) — if prediction still diverges, model is wrong.

---

### 13.4 Risk: 50-Step Lookahead Insufficient

**Symptom**: Frequency resolution of Layer 3 too coarse (200 Hz / 50 = 4 Hz), RBFs not resolved.

**Cause**: Window too short for low-frequency RBFs (0.5, 1 Hz).

**Mitigation**: Increase to 100 steps (0.5s), or use zero-padding FFT to 128 samples.

**Trade-off**: Longer window → higher memory, older predictions.

---

### 13.5 Risk: Compute Overruns Control Period

**Symptom**: Control loop misses deadlines, jitter in telemetry.

**Cause**: FFT takes longer than expected (cache misses, interrupts).

**Mitigation**: Profile with RTOS tracer, move FFT to lower-priority task, or reduce FFT size (256 → 128).

**Hard limit**: If total time >4 ms per axis, architecture is infeasible on STM32F4.

---

### 13.6 Risk: Normalization Constants Wrong

**Symptom**: Some features dominate (e.g., Φ₉ = x·u >> 1), others vanish (Φ₁ = x ≈ 0.01).

**Cause**: Normalization constants not tuned to actual flight data.

**Mitigation**: Log all Φ values during chirp flight, compute max per feature, update constants.

**Test**: Plot `Phi_active[j]` for all j — all should be O(1) during maneuvers.

---

## 14. Deliverables Summary

### 14.1 Code Artifacts

1. **Firmware (C)**: `API/adaptive_basis_rbf.c`, `API/adaptive_basis_rbf.h`
2. **Simulation (Python)**: `.agent_contracts/ADAPTIVE_BASIS_RBF/simulation/adaptive_basis.py`
3. **Data logging**: Schema in `data_logging/schema.yaml`, telemetry frames for all ξ, a, β
4. **Analysis scripts**: `validate_architecture.py`, `visualize_w_matrix.py`

### 14.2 Documentation

1. **This spec** (`spec.md`)
2. **Firmware implementation plan** (`firmware_plan.md`)
3. **Validation metrics** (`validation/metrics.md`)
4. **Known risks** (`risks.md`)

### 14.3 Experimental Data

1. **Phase 1**: Chirp RMSE comparison (fixed vs. adaptive)
2. **Phase 2**: Learned W matrix (dense + sparse), heatmaps
3. **Phase 3**: Transfer learning results, pattern library

---

## 15. Open Questions for Adversarial Review

### 15.1 Architecture

1. **Is 8 RBFs sufficient?** Could important frequencies fall between centers?
2. **Is σ = 2 Hz optimal?** Should it be learned per-RBF or global?
3. **Should W be per-axis or shared?** (Currently per-axis, could share across roll/pitch)

### 15.2 Implementation

4. **Is 256-sample FFT window too short for 0.5 Hz RBF?** (Nyquist satisfied but resolution = 0.78 Hz)
5. **Should FFT use Hanning window?** (Currently rectangular, may leak)
6. **Is 50-step lookahead enough for low-freq prediction?** (Only 0.25s, might miss slow trends)

### 15.3 Learning

7. **Can W be learned online (on-drone)?** Or only offline (ground post-processing)?
8. **Should RBF centers be trajectory-dependent?** (E.g., different centers for chirp vs. cruise)
9. **Is L1 regularization enough for sparsity?** Or should we use L0 (hard thresholding)?

### 15.4 Validation

10. **What's the minimum RMSE improvement to justify complexity?** (Currently 10%, is that enough?)
11. **How to validate on real hardware without risking crash?** (Fixture only? Shadow mode first?)
12. **What if sparse W doesn't emerge?** Is the hypothesis falsifiable?

---

## 16. Conclusion

This specification defines a **novel three-layer adaptive control architecture** that dynamically gates physics-informed basis functions using **continuous RBF spectral representations**. The architecture is:

- **Theoretically grounded**: Spectral gating matches basis functions to frequency content
- **Computationally feasible**: 1.23 ms per control cycle on STM32F4
- **Memory-efficient**: 6.9 kB RAM, 15 kB flash
- **Falsifiable**: Clear success criteria (RMSE, sparsity, generalization)
- **Modular**: Integrates with existing MRAC code via simple substitution

**Next steps**:
1. **Adversarial review** of this spec (identify blind spots, challenge assumptions)
2. **Firmware implementation** (Phase 1 infrastructure)
3. **Python simulation** (validate before deployment)
4. **Hardware validation** (4-DOF fixture, chirp trajectory)

---

**Adversarial reviewer**: Please probe assumptions, challenge design decisions, and identify failure modes I missed.
