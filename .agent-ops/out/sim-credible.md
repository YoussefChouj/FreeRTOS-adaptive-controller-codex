# sim-credible digest (supervisor inline, 2026-09-27)
## Defects -> root cause -> fix
- z all-diverged: K_EFF 222 (wrong), thrust loss from t=0, lam applied twice -> firmware K_EFF 64.6 u/N, fault at t_f, single lam (b75eebf)
- yaw S1 != 0: Flight 8 bias in every scenario + MC -> bias only in S2; MC bias symmetric (b75eebf)
- yaw ~100 deg all-alike: +-180 knife-edge command + torque scale + tuning vs zero reference -> fixed (b75eebf)
- coupled MRAC reference: linear plant without mixer/lag/clamp, logged 1 step late -> nominal PID on the same plant, shifted 1 step; C1 now 0.00 for all (ce17ed0)
- coupled actuators started at idle 2000 -> hover 2950 (ce17ed0)
- coupled gamma fixed 0.1 for all -> tuned per controller (15 gammas x 8 draws seed 101); S6/RBF* 0.32, S10 0.18
- coupled C5 yaw bias sign opposite to C2; MC yaw bias one-sided 0..0.12 -> C5 same sign; MC +-0.11
- C2 ~24 deg for every controller = bias present at t=0: psi reaches -80 deg in 0.5 s before any adaptation (measured)
## Per-axis rms_ref S1..S5 | MC median  (see figures_axes/results_axes.json)
pitch PID 0/1.35/2.99/1.46/5.41|2.0  S6 .11/.35/.66/1.17/1.64|.86  RBF6 .09/.48/.65/1.08/1.38|1.0  RBF24 .08/.39/.71/1.2/1.39|1.03
yaw   PID 0/42.3/3.6/1.61/4.15|4.2   S6 .29/6.67/4.97/2.66/6.47|2.57 RBF12 .27/6.88/2.4/1.99/2.97|2.64 RBF24 .3/7.51/2.64/1.93/2.78|2.58
z(m)  PID 0/.12/.04/.03/.15|.28      RBF6 0/.09/.04/.03/.12|.21
## Coupled roll/pitch/yaw deg, z m, sat%  (results_coupled.json)
C1 all 0.  C2 PID 0/.1/24.0 ; S6 yaw 35.9, S10 25.2, RBF6 35.0, RBF12 35.2, RBF24 33.2 (adaptive WORSE)
C3 PID 2.9/3.3/1.1 ; RBF* 2.2/1.0/0.9, S6 2.3/1.1/0.9
C4 PID 3.4/2.6/2.0 sat0 ; S6 3.8/2.4/3.3 sat3 ; RBF12 3.5/2.4/2.8 sat2 (yaw worse)
C5 PID 5.9/3.1/11.6 sat14 ; RBF6 3.6/3.0/11.4 sat25 ; S6 4.4/3.0/13.3 sat25
MC median roll/pitch/yaw: PID 5.99/4.58/4.09 (2 diverged) ; RBF12 3.60/3.00/3.91 (1) ; S6 3.93/3.67/4.38 (1)
## Remaining weaknesses
- coupled C2 yaw: per-axis gain does not carry over; cause not isolated. Yaw torque effectiveness assumed (G_EFF).
- 1-2 of 30 coupled MC draws diverge for every controller incl. PID.
