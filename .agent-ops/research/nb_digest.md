# Notebook Digest

## 1. Per Notebook Analysis

### nb1_tutorial1.txt (Adaptive_Control_Tutorial)
- **purpose**: Basic tutorial introducing adaptive gain (nb1 cell 0), MRAC (nb1 cell 3), and neuro-adaptive control (nb1 cell 7).
- **plant model**: Scalar $x_{k+1} = x_k + dt(wx_k+u)$ (nb1 cell 0). Later, 2D mass-spring-damper ($\Lambda = 1/m$) (nb1 cell 12). $dt=0.005$s (nb1 cell 0).
- **reference model**: First-order tracking (nb1 cell 2), then 2D state-space $A_m, B_m$ (nb1 cell 3).
- **controllers**: Adaptive gain (nb1 cell 1), point/reference-model MRAC (nb1 cell 3), Neuro-adaptive MRAC (nb1 cell 7).
- **adaptation laws**: $\dot{\hat{W}} = \Gamma x x$ (nb1 cell 1), $\dot{\hat{W}} = \Gamma x e$ (nb1 cell 2), $\Gamma=10-50$ (nb1 cell 1), with $\sigma$-mod ($\sigma=0.1-0.5$) (nb1 cell 5), $e$-mod (nb1 cell 6), projection (nb1 cell 4), and performance recovery ($\lambda=1-5$) (nb1 cell 11).
- **regressor/basis**: State $x$ (nb1 cell 1), then RBF network (6 to 22 neurons) + bias term (nb1 cell 7, nb1 cell 10).
- **disturbance/uncertainty**: Time-varying weight $w = 1.5 + 0.25 \cos(0.5t)$ (nb1 cell 4).
- **tuning values**: $\Gamma=10-50$ (nb1 cell 1), $dt=0.005$ (nb1 cell 0), $\sigma=0.1-0.5$ (nb1 cell 5).
- **sysID/estimation**: Online weight $\hat{W}$ estimation (nb1 cell 2).
- **claimed results**: RBFs and modifications stabilize the system under time-varying nonlinear uncertainty (nb1 cell 17).

### nb2_tutorial2.txt (Adaptive_Control_Tutorial_2)
- **purpose**: Derivative-free (DF-MRAC) (nb2 cell 3) and Set-theoretic MRAC tutorial (nb2 cell 6).
- **plant model**: 2D Mass-spring-damper, $\Lambda=1/m$, $dt=0.005$s (nb2 cell 1).
- **reference model**: 2D standard form with poles defined by $\omega_n, \zeta$ (nb2 cell 4).
- **controllers**: DF-MRAC (nb2 cell 4), Set-theoretic barrier function MRAC (nb2 cell 6).
- **adaptation laws**: DF-MRAC uses filter $\tau=0.1$ (nb2 cell 4). Barrier uses prescribed performance bounds to modify gradient (nb2 cell 6). High gain $\Gamma=250$ (nb2 cell 7).
- **regressor/basis**: 11 RBF neurons per state (5 pos, 5 vel, 1 bias) (nb2 cell 6).
- **disturbance/uncertainty**: Nonlinear uncertainty added at $t=20$s, measurement noise (nb2 cell 5).
- **tuning values**: $\Gamma=250$ (nb2 cell 7), $\sigma=0.1$, $\gamma_f=0.5$ (nb2 cell 7). Filter $\tau=0.1$ (nb2 cell 4).
- **sysID/estimation**: RBF estimation of nonlinear uncertainty (nb2 cell 6).
- **claimed results**: Barrier constrains error successfully: max pos error 10.64 deg, RMS 3.19 deg (nb2 cell 5).

### nb3_rpy_pid_mrac.txt (Roll_Pitch_Yaw_PID_MRAC)
- **purpose**: 3-DOF Cascaded PID-MRAC drone attitude control tuned via Sequential LQR (nb3 cell 10).
- **plant model**: 3-DOF Pitch/Roll/Yaw (independently decoupled) (nb3 cell 0). $J_y$ mapping (nb3 cell 6). $dt=0.006$s (nb3 cell 0).
- **reference model**: Inner loop rate reference driven by outer loop PID (nb3 cell 15). LQR tuning maps to PID + Feedforward (nb3 cell 16).
- **controllers**: Outer loop PID (position) cascaded to Inner loop Rate Direct MRAC (nb3 cell 7, nb3 cell 10).
- **adaptation laws**: MRAC for rate loop $\dot{\hat{W}} = \Gamma \phi e_{mrac}$ (nb3 cell 7), $\Gamma=4-10$ (nb3 cell 13).
- **regressor/basis**: Augmented state/error vectors (nb3 cell 17).
- **disturbance/uncertainty**: Time-varying reference sine wave, torque disturbances (nb3 cell 19).
- **tuning values**: LQR penalties $Q, R$ (nb3 cell 15), MRAC $\Gamma=4-10$ (nb3 cell 17).
- **sysID/estimation**: Inner loop weight adaptation (nb3 cell 19).
- **claimed results**: Sequential LQR successfully maps to PID gains (nb3 cell 18); MRAC handles rate loop uncertainty better than nominal PID (nb3 cell 18).

### nb4 & nb5 (direct_mrac_ff_proj)
- **purpose**: Quadrotor Unified State-Space Direct MRAC with Feedforward, Projection (nb4 cell 0), and Barrier Functions (nb5 cell 25).
- **plant model**: Full 6-DOF decoupled rotation (nb4 cell 16). $m=0.65$kg, $J_x=0.015, J_y=0.015, J_z=0.025$, $dt=0.006$s (nb4 cell 24). $\Lambda = diag(1/J_x, 1/J_y, 1/J_z)$ (nb4 cell 24).
- **reference model**: Unified 6-state End-to-End $A_m, B_m$ (not cascaded) (nb4 cell 2, nb4 cell 24).
- **controllers**: Direct MRAC with feedforward ($r_{filtered}$), Performance Recovery (LF learning), Adaptive Deadzone, Gamma Scheduling (nb4 cell 24, nb5 cell 24).
- **adaptation laws**: $\dot{\hat{W}} = \Gamma (\text{grad} + \text{grad}_{barrier} \times \text{deadzone}) - \sigma \hat{W} / \text{norm}$ (nb5 cell 24). Deadzone threshold 0.05 deg (nb5 cell 24). Projection bounds [-20, 20] (nb4 cell 15).
- **regressor/basis**: 6 baseline physical terms (bias, angle, rate, drag, sin, gyro) or RBF grids ($NUM\_BASIS$) (nb4 cell 24, nb5 cell 24).
- **disturbance/uncertainty**: Aerodynamic drag, unmodeled actuator dynamics (nb4 cell 24).
- **tuning values**: $\Gamma$ up to 25, $\tau_\gamma=25$s (nb5 cell 24). Barrier smoothing $0.01$ rad (nb5 cell 22).
- **sysID/estimation**: Online learning of physical terms (nb4 cell 34).
- **claimed results**: Adaptive control achieves $\mathcal{L}_1$ norm performance bounds despite unmodeled actuator dynamics (nb4 cell 56).

## 2. nb4 vs nb5 Differences

| Change | Cells | Effect |
| :--- | :--- | :--- |
| Set-Theoretic Barrier Function | `nb5 cell 22` | Added `barrier_gradient_modifier_log` function to compute barrier gradient (nb5 cell 22). |
| Barrier Config added | `nb5 cell 11` | Added `BarrierConfig` for error limits ($E\_MAX\_PITCH$) and smoothing (nb5 cell 11). |
| Gradient Modification | `nb5 cell 24` | `grad_barrier` added to the MRAC gradient before weight update to prevent constraint violations (nb5 cell 24). |
| Barrier Logging | `nb5 cell 25` | Added `barrier_value_pitch`, `barrier_active`, and error norms to the simulation log dictionary (nb5 cell 25). |

## 3. Conflicts
- **Architecture**: nb3 advocates for Cascaded PID-MRAC (industry standard like PX4) (nb3 cell 10), while nb4/nb5 argues for a Unified State-Space End-to-End reference model without explicit inner loops (nb4 cell 2).
- **Theory vs Practice**: nb4/nb5 notes a "Theory vs. Practice Gap" regarding the $\mathcal{L}_1$-norm condition for the performance recovery filter bandwidth $\lambda$; theory suggests high bandwidths but practice requires empirically finding the "root-locus-like sweep" due to unmodeled dynamics (nb4 cell 58).
- **Normalization vs Projection**: nb4 notes that projection limits and normalization serve different purposes and should not be matched, which contradicts simpler interpretations of weight bounding (nb4 cell 15).

## 4. Ideas Not Yet In Our Sim
- **Set-theoretic / error-bounding terms** (nb5 cell 24): `barrier_gradient_modifier_log` adds a repelling gradient when error approaches constraints (nb5 cell 22). Closest in sim: `none`.
- **Performance-recovery / low-frequency learning** (nb1 cell 11, nb5 cell 24): Uses a secondary low-pass state `Whatf` for weights (nb1 cell 11). Closest in sim: `sim/bench/ctrl_mrac3l.py`.
- **CRM (closed-loop reference model)**: Referenced conceptually. Closest in sim: `sim/bench/ctrl_mrac_b.py`.
- **Projection variants (component-wise)** (nb5 cell 24): Bounds each weight independently via `What_Proj_limits` (nb5 cell 24). Closest in sim: `none`.
- **FF structure** (nb4 cell 24): Direct addition of $r_{filtered}$ feedforward to reference model update (nb4 cell 24). Closest in sim: `none`.
- **Physical-feature regressor** (nb4 cell 24): Explicit physical terms `[bias, angle, rate, drag, sin, gyro]` (nb4 cell 24). Closest in sim: `none`.
- **Gamma Scheduling & Adaptive Deadzone** (nb5 cell 24): Modifies learning rate dynamically and pauses learning when error < 0.05 deg (nb5 cell 24). Closest in sim: `none`.

## 5. Operator Intent
- "You are asking a crucial question about the difference between Control Architecture and Reference Model Design." (nb4 cell 2)
- "What you've observed — the root-locus-like behavior of performance recovery parameters — is real, theoretically grounded..." (nb4 cell 59)
- "However, the systematic solution... is often not implemented in practical drone controllers..." (nb4 cell 59)
