# Brief res-block1-features (Argon, read-only research)

Context: thesis = configurable MRAC that augments any nominal controller on a small quadrotor (STM32F4, 1 kHz inner
loop). Block 1 = the adaptive regressor Phi(x), u_ad = W_hat^T Phi(x). Today: 36 Gaussian RBF cells + 4 physics
features (API/mrac.c, basis MRAC_BASIS_RBF40T_BPQ around line 1795). Worry: too few / wrong features make the
weights absorb unmodelled terms.

Question: what is the best-supported way to CHOOSE block-1 features offline from flight logs, so online adaptation
only tunes weights? Compare, with sources:
1. Sparse regression over a rich physics library (SINDy and variants: drag v|v|, rotor omega^2, induced velocity,
   ground effect 1/h^2, battery sag, attitude coupling terms).
2. Learned basis + online linear weights (Neural-Fly, O'Connell 2022; related meta-learning adaptive control).
3. Generic approximators: RBF vs Chebyshev/Fourier/random features vs small NN; cost on an MCU (flops, memory).
4. Pitfalls of fitting on CLOSED-LOOP data (controller makes regressors collinear), and how papers handle it
   (excitation, instrumental variables, regularization).
Also say if the Neural-Fly dataset is public (URL).

Answer: at most 40 lines, each claim with a source (URL + short quote), then a one-line recommended pipeline for us.
