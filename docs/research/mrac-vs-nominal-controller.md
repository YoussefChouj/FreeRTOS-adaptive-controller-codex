# Does changing the nominal controller change the MRAC design? (2026-10-20)

Question (operator, 2026-10-20): when the nominal controller changes (PID -> LQR / MPC / ADRC), must the
reference model and the adaptation law change too, to keep the stability guarantee?

Verdict: yes for the "closed-loop reference model" MRAC that this firmware's x/y layer uses, where the
reference model is the baseline's own closed loop. The reference model, the Lyapunov P and the ideal gains
all change with the baseline. The law's form (gradient, theta_dot = Gamma Phi e^T P B) stays the same.
No for the "make the plant look nominal" family (L1, disturbance observer), where the target is the plant
model and any baseline designed for that model can sit on top. Argon could not check this (Gemini quota,
2026-10-20), so it was researched inline; the textbook equations below are the standard result and still
need a check against the chapter text.

## 1. Closed-loop reference model (adaptive augmentation)

Source: Lavretsky and Wise, *Robust and Adaptive Control with Aerospace Applications* (Springer 2013),
chapter "Adaptive Augmentation of a Linear Baseline Controller" (title confirmed from the book's table of
contents; equations below NOT yet checked against the chapter).

- Plant: x_dot = A x + B Lambda (u + Theta^T Phi(x)), Lambda and Theta unknown.
- Baseline: u_bl = -K^T x (LQR, or PI with the integrator in the state).
- Reference model = the baseline closed loop with no uncertainty: A_ref = A - B K^T.
- Lyapunov: A_ref^T P + P A_ref = -Q. Law: Theta_hat_dot = Gamma Phi(x) e^T P B, e = x - x_ref.
- At the nominal plant the ideal adaptive gains are zero: adaptation only fills the gap to the baseline.

So the baseline sets A_ref, therefore P, therefore the drive e^T P B. Change K and all three change.
The proof needs a linear state-feedback baseline. A cascaded or output-feedback PID adds filter states,
and the error equation then goes through a transfer function that must be strictly positive real
(SPR, the Kalman-Yakubovich-Popov lemma); otherwise the proof does not hold. MPC (constraints, switching)
and ADRC (its own observer state) are outside this proof as written.

Closest worked quadrotor example: EuroGNC 2022, "Closed-Loop MRAC Augmented LQR with Integral Action for
Quadrotor UAV" (https://eurognc.ceas.org/archive/EuroGNC2022/html/CEAS-GNC-2022-002.html).

## 2. Make the plant look nominal (L1 / disturbance observer)

Source: Pereida and Schoellig, IROS 2018, "Adaptive Model Predictive Control for High-Accuracy Trajectory
Tracking in Changing Conditions" (https://arxiv.org/abs/1807.05290). An inner L1 adaptive loop makes the
quadrotor behave like a chosen linear reference model; an outer MPC is designed on that model. The
deviation from the model is bounded, and a robust MPC version uses that bound (ICRA 2019 workshop paper,
https://www.dynsyslab.org/wp-content/papercite-data/pdf/pereida-icra19a.pdf).

Here the order is reversed: adaptation first, then the baseline is designed on the model adaptation
enforces. Any baseline that stabilises that model with margin can sit on top. This is the architecture
that fits a "controller-agnostic" claim.

## 3. This firmware (checked 2026-10-20)

| Layer | Reference model | P | Depends on the xy controller? |
|---|---|---|---|
| Rate axes (roll, pitch, yaw, z) | fixed 1st/2nd order, ref_model_bw 44 / 44 / 30 / 20 rad/s (API/mrac.c:1551) | closed form from bw, zeta (mrac.c:1004-1015); dropped when drive_norm is on (mrac.c:1021-1025) | No: the inner rate loop is the same under every xy controller |
| Position x/y | 1st order, bw 2.0 rad/s, fitted to the PID closed loop on 5 figure-8 logs (mrac.c:1691) | scalar, from bw | Not in code: API/mrac.c never reads g_xy_ctrl_kind; StabilizerTask.c:2236 swaps PID/ADRC/LQR/MPC under it |

Consequence: with LQR / MPC / ADRC in the xy loop, the x/y MRAC still pulls the closed loop toward the
PID's 2 rad/s fit. If the new baseline is faster, adaptation will slow it down (it fights the baseline).
Per-variant rows at StabilizerTask.c:1725-1728 can set ref_type / bw per run, so a per-baseline fit is
possible without new code: fit bw from each controller's own no-adaptation flight (E2-E4 with u0).

## 4. Thesis impact

"Controller-agnostic MRAC" (memory: thesis-core-claim) holds only if one of these is true:
1. the reference model is refit per baseline (closed-loop augmentation, section 1), and the thesis says so;
2. the adaptive layer targets a plant model (section 2), and the baselines are designed on that model.
Today's x/y layer is neither: its model is PID-specific. Decide before the E1-E4 matrix is analysed.

## Open (UNSOURCED until checked)

- Exact SPR condition for the cascaded PID baseline; squaring-up (Lavretsky output-feedback MRAC).
- ADRC + MRAC: two estimators (ESO and adaptive weights) chasing the same lumped disturbance; no proof found.
- L1 augmentation of a nonlinear baseline for quadrotors (L1Quad, Wu et al. 2023): not read yet.
