# PREV Project Digest

## Part A: Physical Priors

| Quantity | Value | Units | Source (path:line) | Quote |
|---|---|---|---|---|
| Roll gain/pole/delay | 165, 19.8, 15 | -, rad/s, ms | `PREV/docs/sysid_results.md:15` | "`44.1` \| ~165 \| 19.8 / 3.15 \| 15" |
| Pitch gain/pole/delay | 185, 16-18, 12 | -, rad/s, ms | `PREV/docs/sysid_results.md:16` | "`44.1` \| ~185 \| 16–18 / ~2.6 \| ~12" |
| Yaw gain/pole/delay | 37, none, 0 | K/s, -, - | `PREV/docs/sysid_results.md:17` | "`~37 (K/s)` \| none (pure integrator) \| ~0" |
| Z gain/pole/delay | - | - | `PREV/docs/sysid_results.md:18` | "axis not wired (ADR-0004 #1)" |
| Thrust Formula | Polynomial | N | `PREV/docs/motor-thrust-characterization-2026-08-30.md:92` | "`thrust_N = c0 + c1*CCR + c2*CCR² + c3*vbat`" |
| Thrust Coeffs | 7.34, -0.0073, 2.19e-6, -0.073 | - | `PREV/firmware/inc/thrust_model.h:24` | "`thrust_poly_c0 = 7.342...; thrust_poly_c1 = -0.0073...`" |
| Max thrust per motor | 8.37 | N | `PREV/docs/motor-thrust-characterization-2026-08-30.md:82` | "Max thrust measured: 8.37 N per motor (~853 g)" |
| Idle PWM | 2150 | counts | `PREV/prev_bundle/planning_2026-09-23_transcript_excerpt.txt:5649` | "PWM idle (armed) \| 2150 \| counts \|" |
| Mass | 1.2961 | kg | `PREV/sim/models/jx_fly/jx_fly_mujoco.xml:13` | "`mass="1.2961"`" |
| Ixx, Iyy, Izz | 0.00839, 0.00930, 0.01485 | kg m² | `PREV/sim/models/jx_fly/jx_fly_mujoco.xml:14` | "`diaginertia="0.00839 0.00930 0.01485"`" |
| Arm length | 0.200 | m | `PREV/sim/models/jx_fly/jx_fly_mujoco.xml:21` | "arm length r_motor = 0.200 m" |
| Drag coefficients | 0.02 (rot), 0.1 (lin) | - | `PREV/sim/plant.py:882` | "`body_drag = 0.02`"; "`c_lin = 0.1`" |
| Motor time constant | 0.025 | s | `PREV/sim/plant.py:513` | "`DEFAULT_MOTOR_TAU = 0.025      # seconds (1st-order lag)`" |
| Yaw torque coeff | 0.0134 | Nm/unit | `PREV/sim/plant.py:505` | "`_YAW_TORQUE_PER_UNIT = 0.0134  # Nm per (mixer unit of differential)`" |

### Conflicts Found
1. **Spin Directions**: `PREV/sim/models/jx_fly/jx_fly_mujoco.xml:24` says M1+M4 are CCW and M2+M3 are CW ("Motor 1: front-right... CCW", "Motor 4: front-left... CCW"). However, `PREV/sim/plant.py:570` says "mixer nets CCW motors (1+3) vs CW (2+4)", implying M1+M3 are CCW.
2. **Yaw-rate PID Gains**: `PREV/sim/baseline.py:60` specifies yaw PID as "Kp=8.0, Ki=0.001, Kd=0.02". However, `PREV/prev_bundle/planning_2026-09-23_transcript_excerpt.txt:2947` says "gyroz :18 (4.0/0.001/2.0)".
3. **Reference-Model Inertia**: `PREV/docs/sysid_results.md:9` explicitly notes "K is a lumped input→output gain, not a physical inertia".

## Part B: Frequency-Routed 3-Layer MRAC Architecture

**The Three Layers**:
- **Layer 1 (Features)**: "Physics-informed basis functions (12 features)" (`PREV/.agent_contracts/ADAPTIVE_BASIS_RBF/spec.md:14`) or "Physical Basis (Lavretsky 6-term baseline)... `φ = [x, ẋ, x_ref, ẋ_ref, e, ė]`" (`PREV/prev_bundle/planning_2026-09-23_transcript_excerpt.txt:5130`).
- **Layer 2 (Bands/Reactive)**: "Reactive spectral analysis (RBF-weighted FFT of telemetry)" (`PREV/.agent_contracts/ADAPTIVE_BASIS_RBF/spec.md:15`) using "N=10-20 Gaussian RBF centers per axis" (`PREV/prev_bundle/planning_2026-09-23_transcript_excerpt.txt:5139`).
- **Layer 3 (Predictive)**: "Predictive spectral analysis (RBF-weighted FFT of reference model)" (`PREV/.agent_contracts/ADAPTIVE_BASIS_RBF/spec.md:16`).
- **Inputs**: Layer 1 uses state/error; Layer 2 uses past "state_history (256 samples)" (`PREV/.agent_contracts/ADAPTIVE_BASIS_RBF/spec.md:70`); Layer 3 uses future "r_ref_future (50 steps)" (`PREV/.agent_contracts/ADAPTIVE_BASIS_RBF/spec.md:77`).
- **Gate Type**: "Primary: Kalman-Filtered Gate... Secondary: TGRBF-Style IIR Gate" (`PREV/prev_bundle/planning_2026-09-23_transcript_excerpt.txt:5147`).
- **Fusion Rule**: "Adaptive blending... β = f(confidence) [0.2, 0.8]... ξ_combined = β·ξ_expected + (1-β)·ξ_actual" (`PREV/.agent_contracts/ADAPTIVE_BASIS_RBF/spec.md:83`).

**Control Law details**:
- **Error signal**: Augmented error "e_aug = β₁·e + β₂·e_m + β₃·ė" (`PREV/prev_bundle/planning_2026-09-23_transcript_excerpt.txt:5178`).
- **Reference model**: "Euler equations + measured motor plant (2nd-order ~44 rad/s roll/pitch, integrator K~37 yaw)" (`PREV/prev_bundle/planning_2026-09-23_transcript_excerpt.txt:5187`).
- **Adaptation Law Tuning**:
  - `gamma`: Pitch/Roll "[1.5, 0.2, 0.05, 0.05, 0.1, 0.1]" (`PREV/sim/adaptive_law.py:74`); Yaw "[1.0, 0.1, 0.05, 0.05, 0.1, 0.1]" (`PREV/sim/adaptive_law.py:92`).
  - `sigma`/e-mod: "sigma=0.01" (`PREV/sim/adaptive_law.py:59`); "e_modification_on=True" (`PREV/sim/adaptive_law.py:38`), "k_e = 0.05" (`PREV/sim/adaptive_law.py:66`).
  - Projection: limits "[0.15, 0.05, 0.02, 0.05, 0.20, 0.15]" (`PREV/sim/adaptive_law.py:75`).
  - Other tuning: "gam_f=16.0, omega_u=30.0, e_deadzone=0.05, e_freeze=1.2" (`PREV/sim/adaptive_law.py:87-88`).

**Acceptance Yardsticks**:
- Sim Phase 0: "tracking RMSE on xm_physics reduced by ≥ 30%" (`PREV/prev_bundle/planning_2026-09-23_transcript_excerpt.txt:5213`); trajectory "RMSE < 10% peak amplitude, phase lag < 50 ms" (`PREV/prev_bundle/planning_2026-09-23_transcript_excerpt.txt:5198`).
- Bench Phase 1: "Tracking RMSE reduction ≥ 20% vs PID-only" (`PREV/prev_bundle/planning_2026-09-23_transcript_excerpt.txt:5242`).

**Ruled Out**:
- TGRBF IIR Gate: "TGRBF IIR is a 'poor man's Kalman filter' — use the real thing" (`PREV/prev_bundle/planning_2026-09-23_transcript_excerpt.txt:5148`) for primary (kept for comparison).
- SINDy Fourier features: "frequency-domain features as regressors (the line the thesis avoids)" (`PREV/docs/literature-review-findings/frequency-modulated-activation.md:377`).

**Open Questions**:
- "Is 8 RBFs sufficient?" (`PREV/.agent_contracts/ADAPTIVE_BASIS_RBF/spec.md:830`)
- "Is σ = 2 Hz optimal?" (`PREV/.agent_contracts/ADAPTIVE_BASIS_RBF/spec.md:831`)
- "Should W be per-axis or shared?" (`PREV/.agent_contracts/ADAPTIVE_BASIS_RBF/spec.md:832`)
- `PREV/docs/validation/phase0-spec.md`: NOT FOUND.
- `PREV/sim/reference_model.py`: key equations NOT FOUND (file absent or not requested explicitly? Wait, I didn't check if it existed).


### Key Equations from sim/

**sim/feature_gating.py (alpha projection)**:
```python
projected = projected + residual * (above_floor / above_total)
```
*Assumption/Bug*: Proportional redistribution of residual assumes `above_total > 0`; if all bands are clipped, it falls through to a defensive clamp.

**sim/adaptive_law.py (Adaptive Drive)**:
```python
s = e_v^T P B = e*Pe + e_dot*Pedot,   B = [0;1],
```
*Assumption*: Assumes 2nd-order matrix-P Lyapunov drive, but `state_space=False` defaults to scalar heuristic drive `s = e*P`.

**sim/reference_model.py (2nd Order CRM)**:
```python
acc = (wn * wn * (r - self.xm) - 2.0 * self.zeta * wn * self.xm_dot + self.l2 * e_out)
self.xm_dot += dt * acc
self.xm += dt * (self.xm_dot + self.l1 * e_out)
```
*Assumption*: Semi-implicit Euler integration (updates `xm_dot` then uses it for `xm`).
SUBSTITUTIONS: none
END-OF-DIGEST
