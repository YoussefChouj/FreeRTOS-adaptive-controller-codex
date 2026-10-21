# D7: how good is the xy velocity plant fit? (2026-10-22)

Question (user, 10-22): the sysid model is the basis for most simulation (xy_tune, xy_ctrl gains, sim); is it good
enough, and if not, refit it.

Model (`ground_station/analysis/xy_tune.py`): a = first-order lag (tau) of the command delayed d samples,
v' = k a - drag v + bias. Output-error fit, 1 s horizon from many starts, d tried 0..400 ms (free).
Data: the three 10-21 sysid flights r6_f4_sysid, r6_f4b_sysid_p5300, r6_f4c_sysid_p4000 (closed loop, 50 Hz).
Script: `tools/sysid_quality.py` (the fixed-delay hold-out is the same `fit`/`vaf` on `s[:cut]` / `s[cut:]` at d 10 and 11),
run from the repo root: `PYTHONPATH=. python tools/sysid_quality.py`.
All numbers below are measured from those runs.

## Bottom line

The gain k is solid; the **delay is not identifiable** from these flights, and the free fit's delay (240-400 ms)
is wrong. Fixing the delay at the measured ~200-220 ms costs almost nothing in fit and gives physical lags.
The model is good for 1 s-ahead prediction (VAF 0.79-0.93) but its residuals are not white: there is
dynamics it does not capture.

## Evidence

| log / axis | free d (ms) | k | tau (s) | drag | rmse m/s | VAF fit | ACF out | resid-input xcorr peak | fixed 200 ms: rmse / tau / VAF |
|---|---|---|---|---|---|---|---|---|---|
| f4 x  | 240 | 0.76 | 0.14 | 0.15 | 0.087 | 0.94 | 28 % | lag 4, 0.08 | 0.088 / 0.18 / 0.93 |
| f4 y  | 260 | 0.76 | 0.13 | 0.22 | 0.104 | 0.90 | 24 % | lag 3, 0.17 | 0.105 / 0.19 / 0.88 |
| f4b x | 380 | 0.62 | **0.02 (bound)** | 0.51 | 0.141 | 0.92 | 32 % | lag 9, 0.12 | 0.148 / 0.18 / 0.82 |
| f4b y | **400 (grid edge)** | 0.64 | **0.02 (bound)** | 0.66 | 0.168 | 0.89 | 72 % | lag 9, 0.23 | 0.177 / 0.20 / 0.80 |
| f4c x | 320 | 0.65 | 0.06 | 0.24 | 0.121 | 0.94 | 24 % | lag 8, 0.09 | 0.125 / 0.18 / 0.90 |
| f4c y | **400 (grid edge)** | 0.62 | 0.04 | 0.78 | 0.150 | 0.93 | 60 % | lag 8, 0.23 | 0.159 / 0.22 / 0.79 |

1. **Flat delay profile.** Cost vs delay barely moves: f4 x is 0.088 at 180 ms, 0.087 at 240 ms, 0.088 at 340 ms.
   On f4b y and f4c y the cost keeps falling to the 400 ms edge of the grid. A long delay plus a near-zero lag
   (tau at its 0.02 s bound) fits about as well as a short delay plus a 0.2 s lag: the two trade off.
   The mean of the free fits is what set `XYC_D 33` (330 ms) in `API/xy_ctrl_gains.h`; the loop delay measured
   on 10-21 is ~21 ticks (210 ms, D1).
2. **Fixed delay is nearly free.** At 200 ms the rmse rises 0.5-6 % and tau becomes a physical 0.16-0.22 s on
   every log/axis; k stays 0.74-0.84.
3. **Hold-out (fit on the first 70 % of starts, score the last 30 %)**, delay fixed at 200 / 220 ms:
   f4 x 0.77 / 0.77, f4 y 0.50 / 0.51, f4b x -0.23 / -0.19, f4b y -0.62 / -0.57, f4c x 0.73 / 0.73,
   f4c y 0.26 / 0.28. The free-delay hold-out collapses the same way on f4b (0.01, -0.28), so the f4b collapse is
   **not** delay overfitting: the last 30 % of f4b is a different regime (cause not yet checked: battery sag,
   excitation amplitude, or position near the arena edge).
4. **Cross-log VAF** (fit on one log, score another, free d): 0.73-0.93. The model transfers between flights, so
   the y-axis hold-out drop is time-varying behaviour, not a model that only fits one flight.
5. **Residuals are coloured.** 24-72 % of the 25 autocorrelation lags fall outside +/-1.96/sqrt(n), and the
   residual correlates with the command at lag 3-9 samples (60-180 ms), worst on y. That is unmodelled input
   dynamics around the delay (for example a second lag pole, or a delay that is not a pure transport delay).
6. **Drag spreads 0.15-0.78 1/s** across flights with k nearly constant: drag is absorbing the unmodelled part.

## What this means for the sim and the controllers

| Use | Effect | Action |
|---|---|---|
| Smith predictor (LQR, MPC) | predictor longer than the real delay limit-cycles (2026-10-15 note in `xy_ctrl.c`) | done: every exp script writes VAR 47 = 21 ticks (7c4d7e21); header default `XYC_D 33` stays until the header is regenerated |
| xy_tune / sim gain search | designs against 240-400 ms delay plants: gains tuned for the wrong delay mix | post-lab: pick the delay from the profile with a parsimony rule (shortest delay within 2 % of the best cost) or fix it at the measured value, then regenerate |
| Model error bars in sim | k +/- 10 %, delay +/- 20 ms are the measured spread | keep the K x0.9 / x1.15 robustness cases (test_xy_ctrl_adrc_noise) |
| Better model | coloured residuals at 60-180 ms | try a second-order input lag (two poles + delay) with delay fixed; accept only if hold-out VAF improves on f4 and f4c |

Not changed tonight: the gains header. Regenerating it changes LQR, MPC and ADRC gains on the morning of a lab
day, which is a flight-behaviour change without a flight to check it. The VAR 47 = 21 write already gives the
predictor the measured delay.

## Standard this follows

Ljung, *System Identification* (2nd ed., 1999), ch. 16: validate on data not used for the fit, test residual
whiteness and residual-input independence; a flat cost in one parameter means that parameter is not identifiable
from the data and must come from a separate experiment or a prior. Here the separate measurement is the D1 loop
delay (~21 ticks).
