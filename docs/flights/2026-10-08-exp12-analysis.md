# 2026-10-08 exp12: 570 g, kp 5 + vp 6, PID vs MRAC by hand (presets refused)

Log `logs/exp12_vp6-570g_traj_presets.slot*.csv`, 63,050 slot-0 rows at 100 Hz, 0 drops. Plots (160 dpi PNG):
`docs/flights/plots/2026-10-08-exp12/`. Numbers are measured from the log; readings and next steps are PROPOSED.

| item | value |
|---|---|
| presets | `kp_active` 5 (mask 0x0F) the whole log; `vp_active` 6 in every flight |
| vp 7 / vp 8 | tried on the ground at 229 s and 286 s: `vp_active` 238 = 0xEE (default build has no basis > 0), back to 6 before take-off |
| trajectory presets | `traj_status` 0xE2 (bad `traj_id` at `traj_go`), not logged; all moves were by hand |
| flights | F1 16-59 s PID, F2 123-198 s MRAC, F3 344-386 s PID then 386-402 s MRAC, F4 443-498 s MRAC, F5 539-562 s PID then 562-608 s MRAC |

## PID vs MRAC (segment edges trimmed 1 s; "still" = 2 s windows with x/y setpoints fixed within 2 cm)

| segment | still (s) | pitch / roll sd still (deg) | z - z_des still (m) | u_ad / u_nom RMS pitch, roll | motor2 at 4000 (%) |
|---|---|---|---|---|---|
| F1 PID | 34.5 | 1.13 / 1.23 | -0.099 | 0 (not injected) | 0.1 |
| F2 MRAC | 50.2 | 1.67 / 1.73 | -0.008 | 0.78, 0.54 | 2.0 |
| F3 PID | 30.4 | 2.02 / 1.86 | -0.147 | 0 | 1.0 |
| F3 MRAC | 9.7 | 1.01 / 1.55 | +0.003 | 0.16, 0.14 | 1.5 |
| F4 MRAC | 39.2 | 1.70 / 1.60 | +0.013 | 0.86, 0.48 | 3.6 |
| F5 PID | 13.4 | 1.44 / 1.58 | +0.016 | 0 | 1.5 |
| F5 MRAC | 30.5 | 1.49 / 1.53 | -0.027 | 0.19, 0.19 | 6.5 |

MRAC on from take-off (F2, F4): pitch u_ad/u_nom grows over the flight (F2 thirds 0.64, 0.79, 0.90; |Theta[0]|
0.042 to 0.057). MRAC switched on mid-flight (F3, F5): 0.14-0.21. u_ad against u_nom r = -0.14 to +0.06 and against
the body rate r = -0.08 to +0.19 (no damping, no anti-damping). Max |u_ad| 0.092 pitch, 0.062 roll; max |u_nom| 0.26.
Dominant sway 0.25-0.9 Hz in both modes (load pendulum band).

## Reading (PROPOSED)

1. **Swing.** When MRAC runs from take-off, its pitch/roll output grows to 0.5-0.9 of the PID's own output but is
   uncorrelated with the PID and with the body rate, so it adds torque that does not oppose the swing: still-hover sd
   1.6-1.7 deg vs 1.13-1.23 in F1 PID. Not consistent though: F3 PID was the worst (2.02 / 1.86), and F5 PID vs
   MRAC (small u_ad) is equal (1.44 / 1.58 vs 1.49 / 1.53). The extra wobble follows the size of u_ad, not MRAC itself.
2. **Height.** Same as exp11: PID sinks 0.10-0.15 m (F1, F3), MRAC holds z within 0.03 m. This is the clear MRAC win.
3. **Authority.** PID alone is not short of authority for attitude (max |u_nom| 0.26; motor2 at its cap only
   0.1-1.5 % of the PID time). Full-flight MRAC adds a pitch/roll command about as large as the PID's (RMS ratio up to
   0.86), which is not a small trim; MRAC segments also spend more time at the motor2 cap (2.0-6.5 % vs 0.1-1.5 %,
   with battery sag later in the log mixed in).
4. **Next.** Fly MRAC on z only with PID p/r (z win kept, p/r swing removed) or lower the p/r adaptation gain
   (SIL: stable at gamma 0.25-0.5); keep the load at 500 g so motor2 has headroom.

## Adaptive layer (tool `ground_station/analysis/adaptive_review.py`, stats `plots/2026-10-08-exp12/adaptive_stats.md`)

Plots: `adaptive_pitch_features.png`, `adaptive_roll_features.png`, `adaptive_yaw_features.png`,
`adaptive_z_rate_features.png` (weights Theta[0..5], basis phi, per-feature torque Theta_i*phi_i),
`adaptive_contrib_zoom.png`, `adaptive_contrib_rms.png`, `adaptive_spectra.png`, `adaptive_scatter.png`,
`adaptive_drift.png`. The S6 basis is rebuilt from the logged signals and checked against the logged u_ad.

| finding (measured) | value |
|---|---|
| what u_ad is made of, pitch / roll / yaw | ~100 % bias Theta[0]; Theta[1..5] stay within 0.006 of zero (the load features never learn in 10-75 s) |
| what u_ad is made of, z | 89 % bias, 11 % u_nom feature |
| u_ad against the body rate in the sway band (0.25-0.9 Hz) | phase -27 to -57 deg in 7 of 8 segment-axes; Re H ratio -0.13 to -0.67 (u_ad removes 13-67 % of the PID's damping); F3 roll is the one exception (+0.13) |
| replay with a faster u_ad filter (same weights) | omega_u 10 / 15 / 20 rad/s makes it worse: Re H ratio -0.16 to -1.03 (less lag, the in-phase bias motion reaches the motors) |
| MRAC p/r when u_ad stays small (F3, F5: u_ad/u_nom 0.14-0.21) | attitude sd equal to PID (1.01 / 1.55 and 1.49 / 1.53 deg) |

## Drift (stats table "Static offsets per segment")

| finding (measured, estimate frame) | value |
|---|---|
| attitude tracking, pitch / roll Des - FB mean | -0.06 to +0.23 deg / -0.02 to -0.12 deg (the angle loops hold the setpoint) |
| x velocity with the stick centred | +0.55 to +1.58 cm/s, same under PID and MRAC |
| roll stick (x) active | 6-20 % of each segment; median velocity command +24 to +51 cm/s, always +x |

## Reading (PROPOSED)

1. **Why MRAC p/r swings more.** Only the bias weight moves. It is a slow integrator whose in-band motion is in
   phase with the body rate, so it pumps the pendulum. More basis features (S10, RBF) add weights that also do not
   learn in a flight this short. A faster u_ad filter makes it worse (replay). The lever is the adaptation gain:
   lower p/r gamma keeps u_ad in the 0.14-0.21 range where MRAC p/r matched PID.
2. **Demo config.** `kp_id 5` (MRAC on all four axes) + `vp_user` = the vp 6 law with `g = 0.10` (was 0.25),
   omega_u unchanged. z MRAC keeps its height win. Fallback: MRAC z only with PID p/r.
3. **Drift.** The estimator sees at most a 1.6 cm/s creep with the stick centred, and every stick input in the
   log commanded +x. Either the operator steered +x, or the drone moves in the room in a way the optical-flow
   estimate does not see (then the position loop cannot correct it, PID or MRAC). Check on the next take-off:
   20 s hands off, watch the drone against the floor while `locxPID.FB` stays within a few cm.

## Uncertainty estimate (stats sections "Uncertainty estimate", "Load swing frequency"; plot `adaptive_uncertainty.png`)

The disturbance each axis had to cancel is rebuilt from the log: fit `xdot = b * u(t - k) / ...` on 2-8 Hz content
(outside the swing band), then `-Delta_hat = u_injected(t - k) - xdot / b`, low-passed at 3 Hz. Same for PID and
MRAC segments, so the needed trim is measured independently of who supplied it.

| axis | b | delay | fit r (2-8 Hz) |
|---|---|---|---|
| pitch | 51.5 | 50 ms | 0.73 |
| roll | 45.3 | 50 ms | 0.65 |
| yaw | 38.4 | 20 ms | 0.64 |
| z | 22.5 | 40 ms | 0.34 |

| finding (measured) | value |
|---|---|
| static disturbance (needed trim), same under PID and MRAC | pitch -0.049 to -0.080, roll +0.051 to +0.060, yaw +0.018 to +0.032, z +1.6 to +2.3 |
| share of that trim supplied by u_ad, MRAC from take-off (F2, F4) | pitch 0.67-0.69, roll 0.54-0.59, z 0.31-0.32 (mid-flight switch: pitch 0.13-0.20) |
| dynamic disturbance in the swing band, RMS | pitch 0.020-0.027, roll 0.018-0.027; u_ad dynamic RMS 0.008-0.016 |
| u_ad against -Delta_hat in the band | gain 0.12-0.27, phase -66 to -142 deg (u_ad lags ~100 deg) |
| fraction of the swing disturbance cancelled by u_ad | pitch -0.04 to +0.31, roll +0.02 to +0.30, yaw 0.00-0.14, z -0.12 to +0.32; unchanged with b x0.7 / x1.4 |
| an ideal estimator (true -Delta_hat through a first-order lag w) | w 4 rad/s: pitch 0.45-0.73, roll 0.23-0.54; w 16: ~0.9; w <= 1: can be negative (roll F3 -8.45) |
| swing frequency, Welch peak of gyro 0.15-1.5 Hz | roll 0.44-0.59 Hz (mostly 0.49), pitch 0.49-0.88 Hz |
| predicted pendulum (rope 33 cm + half bottle) | 0.76 Hz (fixed pivot, L 0.43 m) to 0.95 Hz (free drone 988.5 g + 570 g) |

The MRAC static trim is right; the dynamic part is not followed. The limit is the estimator (bias weight only,
slow and lagging), not the u_ad output filter (an ideal learner behind the same kind of lag would cancel half).
The 0.49 Hz peak fits a 1 m pendulum, not 0.43 m: either the rope is longer than noted or the peak is the
position loop wobbling. Check by measuring the rope (PROPOSED).

## Feature capability (stats section "Feature capability", all MRAC segments pooled)

Each weight learns at a rate `gamma_i * E[phi_i^2 / (1 + |phi|^2)]` (normalized gradient, `API/mrac.c`) and can
contribute at most `lim_i * RMS(phi_i)`.

| pitch feature | RMS phi | speed vs bias | reach lim * RMS phi | actual RMS Theta*phi |
|---|---|---|---|---|
| bias | 1 | 1 | 0.15 | 0.046 |
| x | 0.143 | 0.0026 | 0.0071 | 0.00015 |
| x tanh x | 0.043 | 5.1e-5 | 0.00085 | 1e-5 |
| cross | 0.019 | 1.2e-5 | 0.00096 | 1.5e-6 |
| u_nom | 0.074 | 3.6e-4 | 0.015 | 1.4e-4 |
| xm | 0.127 | 1.0e-3 | 0.019 | 3.4e-4 |

Roll and yaw look the same. Every non-bias feature learns 400x to 80,000x slower than the bias and its limit
caps it below the swing disturbance it should cancel (RMS 0.020-0.027). z is the exception: the u_nom feature
learns (speed 0.19, reach 0.29, actual 0.18). Bias 63 % settle time: pitch 1-21 s, roll 1-2 s, yaw 1-3 s, z 2-6 s.

## Runtime variants and next config (PROPOSED)

1. **vp rows are not cumulative.** vp 4 is the only row with performance recovery (kappa 0.5, crm 10); vp 6 is
   V1 + lam_ang 4 without PR. Combinations go through `vp_user` fields then `vp_user_go = 1`.
2. **Live switching.** vp 0-6 switch in one session (ch8 off, write `vp_id`, ch8 on). vp 7-11 (basis 1-5)
   need the MULTI build (`MRAC_VARIANT 2`); the default and the current working-tree build refuse them (0xEE).
   Next: MULTI as the one default build (one line + Keil build), then a per-axis feature mask in `vp_user`.
3. **Gamma.** Keep the bias bandwidth about a decade below the 0.5 Hz swing: g 0.10 (time constant ~10-20 s,
   still learns the trim in one flight) is a reasonable first try. The swing itself is the job of LFHG
   (`mrac_config_pitch/roll.lf_gain` 2, then 4, sigma_lf 0.8, from the Keil watch window), which targets the
   estimator gap above.
4. **Features.** Per-feature normalization (gamma_i scaled by 1 / E[phi_i^2]) and higher x / xm limits would let
   the features learn in a flight; firmware change, approval first.
5. **Logging.** The current command covers vp 0-6. Add `u_pr`, `Theta[6..11]` (RBF bases), `vp_active` and the
   feature mask to one MRAC log preset so every segment labels itself.
6. **Drift.** MRAC has no x/y channel (mask 0x0F); drift is the same under PID and MRAC. Options: locxs/ys Ki
   0.008 -> ~0.02, or an adaptive x/y bias (firmware). First the 20 s hands-off check.

## Outside-force feature test (measured, offline, `adaptive_stats.md` "Outside-force feature")

Idea (grilling 2026-10-08): body-frame accel x/y sees only non-thrust forces (rope pull, slosh, wind, walls), so
-Delta_hat = w * a_xy with one constant weight (lever arm) for any load. Plot `adaptive_force_feature.png`.

- pitch vs acc X: r -0.09 to -0.37 at lag 0 (constant-weight cancel 0.01-0.14), w -0.03 to -0.14 per g, same
  sign in all 7 segments; best with acc X leading by 60-120 ms (r up to -0.47, cancel ~0.22).
- roll vs acc Y: r -0.02 to -0.23 (cancel 0.00-0.05). Cross pairs (pitch-Y, roll-X) are weaker still.
- adding acc x/y to the S6 four (x, x tanh x, cross, xm) adds 0.00-0.08 cancel.
- Verdict: right axis pairing and sign, causal lead, but it explains only a small share of the 0-3 Hz
  disturbance. Open checks (PROPOSED): restrict to the 0.25-0.9 Hz swing band (the 1-3 Hz part of -Delta_hat may
  be motor/PID noise), add acc Z (vertical rope force x knot offset), and log the knot position.
