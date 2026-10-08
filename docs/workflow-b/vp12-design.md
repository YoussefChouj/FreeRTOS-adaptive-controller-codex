# vp 12 design (grilled with the operator 2026-10-08, NOT built yet)

vp 12 = S10 + performance recovery (PR) + swing model + battery sag + motor lag + new x/y velocity layers.
Every number below is PROPOSED unless it cites a measurement. Today's demo is NOT vp 12: it is the runtime row
vp_user = S10 + PR (kappa 0.5, crm_ell 10, basis 1, lam_ang 4, then vp_user_go = 1) on build 4adff87.

## Rules for every vp variant (operator, 2026-10-08)

- Normalise each feature by its physical size (rate / max rate setpoint, angle / 15 deg, accel / g,
  u / u_max, velocity / velocity clamp, swing phases / g). Sizes come from config limits; a per-feature
  gamma multiplier (default 1) is the only trim.
- One gamma per axis. Each lim is a share of that axis's control authority.

## Loads

1. 570 g water bottle on a rope (noted 33 cm), hanging toward the motor2 corner, slosh. Swing measured
   0.44-0.88 Hz, mostly 0.49 (docs/flights/plots/2026-10-08-exp12/adaptive_stats.md). Swing-band disturbance
   0.020-0.027 N.m RMS (docs/flights/2026-10-08-exp12-analysis.md:97). Motor2 at cap 20-40 % under PID (exp9).
2. Then 300 g fixed on one arm: about 0.42 N.m per axis (computed from sim constants, not measured), no swing.

## Attitude layer (pitch/roll; Z battery only; yaw unchanged)

| feature | input | lim (share of u_max 6.74 N.m) |
|---|---|---|
| bias | 1 | 10 % = 0.67 N.m |
| S10 set + rate both signs | as flown (mrac.c:1160-1167) | as flown |
| swing in-phase, quadrature | per-axis adaptive notch estimator on body accel x/y | 2 % each (3x measured peak) |
| battery sag | u * (V_arm / V - 1), V captured at arm | 5 % pitch/roll, 15 % Z (new Z slot) |
| motor lag | u_nom - lag(u_nom), tau 1/19.8 s (roll sysid) + runtime field | 10 % |

Total u_ad cap stays 100 % of u_max (backstop: simplex sat trip, 200 ms at u_max, mrac.c:56).
Swing estimator: one per axis, own freeze guard (freeze when amplitude tiny), clamp 0.3-2.5 Hz, start 0.5 Hz,
both runtime fields.

Gamma (educated guess, operator accepted): pitch/roll 1.5 (flown; learns 0.42 N.m in ~1.4 s at e 0.2,
~10x slower than the 44 rad/s ref model), Z and yaw unchanged, all trims 1. Online-tuned gamma: later variant.

## x/y velocity layer (new)

- Adds to the locxsPID / locysPID output (tilt command). Output cap 50 % of the 15 deg tilt limit = 7.5 deg.
- Learns from a first-order velocity reference model, bw 2 rad/s (runtime field).
- Features and lims (share of 15 deg): bias 20 %, drag v 10 %, drag |v|v 10 %, swing in-phase 15 %,
  swing quadrature 15 % (reuses the pitch/roll swing estimators). Swing sizing: 570 g at a 10 deg swing pulls
  ~1 N on 15.3 N, ~3.6 deg lean (physics, swing amplitude not measured).
- Gamma sized for ~4 s to the full 7.5 deg lean.
- Learn gate: airborne > ~1 s AND g_ekf_of_health good; weights reset to 0 at arm, frozen otherwise
  (pid.c:345: disarmed flow drift once wound the integrator into a hard takeoff).

## Runtime feature mask

- One bit per feature per axis: a 24-bit integer in one float field (exact up to 2^24).
- Masked feature: phi 0, Theta and Whatf reset to 0 (as the RBF switch does), so a stale weight never returns.
- Timing = the vp rule: on the ground any time, in flight only while ch8 is off; otherwise pending, shown in
  the mask readback.

## Unified logger

- At start, read vp_active and the masks, and subscribe only the weights of active features (names from the ELF
  via ground_station/livewatch/mrac_features.py).
- Slot 0 (attitude, motors, u_ad, e) never stops. On a vp or mask change, re-subscribe the weights slot only
  (~1.5 s gap, only while ch8 is off).
- Fix campaign_capture.py:86 MRAC_N_FEATURES = 6 (stale for the MULTI build's 30).

## Later

vp 13 = multi-axis RBF (16 centres on pitch/roll rate + accel x/y). Online-tuned gamma. Then Neural-Fly.
