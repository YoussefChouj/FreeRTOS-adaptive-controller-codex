# D2 research digest: norms and SOTA for the 10-22 fixes (VPS agy worker, 2026-10-21 22:44 UTC)

Worker output, pasted as returned. CEO review notes (2026-10-22), read these first:

| Claim in the digest | CEO verdict |
|---|---|
| Reference-model mismatch "directly caused the 10-21 oscillations under LQR/MPC/ADRC" | CONTRADICTED for LQR/MPC: the measured cause is the Smith-predictor delay (VAR 47 = 12 ticks vs about 21 measured), reproduced in sim and fixed in 7c4d7e21. Mismatch is still a real risk for the MRAC-wrapped runs (D3). |
| One reference model per nominal (A_ref = A - BK) | AGREES with docs/research/mrac-vs-nominal-controller.md; this is the D3 design rule. |
| "Outer-loop position MRAC violates time-scale separation" | UNVERIFIED and over-stated: L1Quad (Wu et al. 2022) adapts the translational loop too. It is a delay/phase-margin condition to check, not a ban. D4 tests it in sim. |
| Neural-Fly pretrained weights not published | UNVERIFIED until the repo is checked (D5). |
| Lemniscate a_max = 17 pi^2 r / (2 T^2), tilt cap 20 deg | UNVERIFIED formula; the firmware lean cap is XYC_AMAX = tan(15 deg) g for the exp controllers. D8 recomputes from the real deck paths. |
| Multisine sysid, VAF >= 90 %, whiteness test | Standard practice (Morelli); used as the D7 quality gate. |

---


### Summary of Findings & Bottom Line

1. **MRAC Augmentation of Non-PID Baselines (LQR, MPC, ADRC):**
   - The reference model must represent the baseline closed loop ($A_{ref} = A - BK$). Mismatch acts as an unmodeled forcing disturbance that drives adaptation to cancel nominal baseline action, directly causing the 10-21 flight oscillations under LQR/MPC/ADRC.
   - For MPC, adaptation must be placed *inside* (underneath) to enforce a linear nominal plant model (Pereida & Schoellig 2018).
   - For ADRC, the Extended State Observer (ESO) and MRAC both attempt to estimate and cancel the lumped disturbance; without coordinate/spectral separation, this forms a competing two-estimator loop that causes observer peaking and chattering.

2. **Cascade Placement & Time-Scale Separation:**
   - Adaptation belongs strictly in the inner attitude/rate loop ($\omega \approx 20-50\text{ rad/s}$), where gyroscope feedback has $<2\text{ ms}$ latency and direct authority over body torques.
   - Outer-loop position MRAC violates singular perturbation time-scale separation conditions due to sensor latency ($50-100+\text{ ms}$) and inner-loop phase lag, inducing limit cycles.

3. **Neural-Fly & Learned Residual Force Methods:**
   - Pretrained weights are not published in [`aerorobotics/neural-fly`](https://github.com/aerorobotics/neural-fly) (restricted personal/educational license; only training scripts and CSV flight logs are provided).
   - Zero-shot transfer to a micro quadrotor is physically invalid due to Reynolds number mismatch ($Re \sim 10^4$ vs $10^5$), differing motor KV, and propeller aerodynamics. Transfer requires collecting flight logs on the target drone, training an offline DAIML basis, and adapting only the linear output layer online at 200 Hz via composite filtering.

4. **Trajectory Feasibility (Lemniscate & Error Scaling):**
   - For a Gerono lemniscate ($x = \frac{r}{2}\sin(2\omega t), y = r\sin(\omega t)$), peak lateral acceleration is $a_{max} = \frac{17\pi^2 r}{2 T^2}$.
   - Optical flow FOV restrictions cap drone tilt to $\le 20^\circ$ ($a_{max} \le 3.57\text{ m/s}^2$). For $r = 0.6\text{ m}$, feasibility requires lap period $T \ge 12-14\text{ s}$ to prevent optical flow loss.
   - Sensor latency ($\tau \approx 100\text{ ms}$) degrades phase margin and produces resonant overshoot at turning extremes scaling as $e_{peak} \approx v \tau + \frac{2.125 r \omega^2}{\omega_n^2}$.

5. **Quadrotor System Identification for Simulation:**
   - Recommended model structure: 1st-order rate lag with pure delay ($G_{\omega}(s) = \frac{K}{\tau s + 1} e^{-\tau_d s}$) and linear rotor drag matrix ($f_d = -D v$).
   - Excitation: 4-axis orthogonal phase-optimized multisine inputs (NASA Morelli 2003) allow simultaneous excitation of roll, pitch, yaw, and Z in a single 20-second hover flight without cross-talk and with low crest factor ($CF \le 1.4-1.8$).
   - Validation metrics: Variance Accounted For ($\text{VAF} \ge 90\%$) on held-out flight logs and residual autocorrelation whiteness bounds ($\pm \frac{1.96}{\sqrt{N}}$).
