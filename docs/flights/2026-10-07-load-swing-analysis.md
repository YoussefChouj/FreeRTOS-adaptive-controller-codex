# 2026-10-07 evening: 500 g swinging load, PID vs MRAC (overnight analysis)

Load: water bottle 6 x 20 cm on ~33 cm rope, hung at (+1..2 cm x, -6..7 cm y) from the centre.
Logs: `logs/exp{1,2,4,5}*-500g.slot{0,1,2}.csv` (stream_log, `exp1_frames.md` preset, measured 20.0 ms = 50 Hz).
Firmware flown: source HEAD at 20:40 flash (`JX_FLY_834c8564.axf`), MRAC_VARIANT = STRUCT6 (no Keil defines),
every WP-27/33 variant row OFF (power cycle wiped the PR preset), ref model type 0 (passthrough), ch8 = injection.

| run | armed (s) | injection | notes |
|---|---|---|---|
| exp1 | 10.5-40.6 | never | PID only |
| exp2_mrac | 7.3-35.7 | on from 14.2 (before takeoff) | MRAC whole flight |
| exp4 | 12.3-37.1 | on 28.9, off 35.1 | PID then MRAC |
| exp5 | 9.9-52.4 | on 37.7 | PID then MRAC |

No `g_wfb_status.safety_trip` in any run.

## Measured (3 s windows, airborne)

| quantity | PID (exp1, exp5 before 37.7 s) | MRAC injected (exp2, exp4/5 after switch) |
|---|---|---|
| z FB vs Des | 0.51-0.80 vs 0.84-1.12 (0.3-0.5 m low) | 1.0-1.19 vs 1.0-1.19 (on target) |
| Throttle_out | 3180-3330 | 3200-3370 |
| motor max | 3850-4000 (hits 4000) | 4000 in every window |
| motor min | 2290-2710 | 2000-2480 (exp2 hits the 2000 floor) |
| pitch std (deg) | 0.9-2.35 | 2.35 -> 5.2 (exp2), 3.7 -> 9.0 (exp4), 1.7 -> 4.4 (exp5) |
| dominant roll/pitch freq | 0.24-0.88 Hz | 0.24-0.93 Hz, end of exp2 1.6-2.0 Hz |
| z u_ad (shadow under PID) | 1.0-1.27 (computed, not injected) | 0.3-0.86 |
| pitch/roll u_ad std | 0.002-0.056 | 0.005-0.034 |

Pendulum: L ~0.33 m rope + ~0.10 m to bottle centre -> ~0.43 m -> ~0.76 Hz (PROPOSED estimate, not measured).

## Findings so far

1. Thrust authority is the binding limit. Every airborne window has a motor at the 4000 ceiling, even under PID.
2. PID sink: the z loop leaves 0.3-0.5 m steady error. MRAC's z channel computes ~+1 N (u_ad) even in shadow,
   so the missing term is a thrust bias the PID integral does not supply (check Z_ratePID I limit).
3. MRAC holds altitude via z u_ad, but pushes the motors to both rails, which leaves no differential authority.
   The pitch swing then grows. The pitch/roll adaptive terms stay small, so the growth is not pitch Theta drift.

## Z loop budget and saturation (measured windows)

| window | z err (m) | Z_ratePID.U | z u_ad x54 | motor 2 at 4000 | u_def p/r, z |
|---|---|---|---|---|---|
| exp1 PID 22-40 s | 0.08 | 227 | 59 (shadow) | 1 % | ~0, ~0 |
| exp5 PID 22-37 s | 0.42 | 258 | (shadow) | 10 % | ~0, ~0 |
| exp5 MRAC 41-52 s | 0.03 | 142 | 39 | 21 % | 0.009, 0.047 |
| exp2 MRAC 19-34 s | -0.04 | 131 | 21 | 33 % | 0.012, 0.066 |

- Motor 2 carries the offset load (-6..7 cm y) and saturates 2-3x more often with MRAC on (higher z, more thrust).
- PID sink (PROPOSED cause): Z_ratePID integral capped at UiMax 100 units and Z_posPID Ui at 0.3, while ~265
  units of extra thrust are needed for 500 g (estimate at 54 units/N), plus battery sag over the flight.
- rpm[2] reads ~2000 and rpm[0] 2200-2600 while rpm[1]/rpm[3] read 6800-8500 at similar PWM: sensor or channel
  mapping issue, not physics (flagged for the code review).

## Why the swing grows under MRAC (energy test)

Band-pass 0.3-2 Hz, correlation of each torque term with the body rate (negative = damping, positive = adds energy):

| window | pitch corr(u_ad, rate) | pitch corr(u_nom, rate) | roll corr(u_ad) | std u_ad / u_nom |
|---|---|---|---|---|
| exp1 PID (u_ad shadow) | +0.50 | -0.16 | +0.42 | 0.16-0.19 |
| exp5 PID (shadow) | +0.35 | -0.13 | +0.17 | 0.14 |
| exp5 MRAC on | +0.58 | -0.43 | +0.07 | 0.23-0.26 |
| exp2 MRAC on | +0.42 | -0.33 | +0.46 | 0.26 |
| exp4 MRAC on 29.5-35 s | +0.66 | -0.58 | +0.56 | 0.16-0.24 |

The adaptive term pushes WITH the swing in every window, even in shadow, so it is the law, not the injection.
Per-cycle pitch swing (0.2-3 Hz band, zero-crossings): the frequency stays 0.7-1.2 Hz (pendulum-coupled mode,
estimate 0.76 Hz) while the amplitude grows: exp4 3 deg -> 6.4 -> 11.9 deg in 5 s after the switch, exp5 2-3 deg
-> 7.6 deg over 14 s, exp2 4 deg -> 8.9 deg. The operator's "increasing frequency" is the growing amplitude of a
constant-frequency mode (PROPOSED reading).

PROPOSED mechanism: STRUCT6 features are [1, rate, rate*tanh(rate), cross, u_nom, rate_cmd] with passthrough
ref, so the weights act like a slow integrator of the rate error (output ~90 deg behind the rate), and the u_ad
low-pass at omega_u 4 rad/s (pitch) / 5 (roll) adds atan(5.3/4) ~ 53 deg more lag at 0.85 Hz. Net ~+37 deg from
the rate, cos ~ 0.8 in phase -> negative damping, of order 15-25 % of the PID's damping torque. Combined with motor 2
at the rail (no differential headroom), the pendulum mode becomes unstable. A regression of u_ad on the features
(R2 0.2-0.5) gives a positive rate weight (~+0.03) and negative command weight (~-0.025), i.e. u_ad ~ +0.03 e.
Candidate fixes to test in sim: raise omega_u (less lag), lam_edot or a rate-damping term in the drive, a notch or
band-stop on the drive at the pendulum band, sigma/e-mod leakage, and a thrust cap so motor 2 keeps headroom.

## Overnight bench results (sim/bench, fw-matched gains, 0.5 kg sling L 0.43 m, 2 seeds, 20 s)

Every number below is a simulation result and PROPOSED. None is a flight measurement.

**Retraction.** An earlier draft proposed vel_kp x0.5. That run used `pid_tuned2` gains, not the flown
`API/pid.c` table. On fw-matched gains (`sim/bench/fwpid.py`), the bench reproduces both flight signatures: PID sinks
to z 0.48 m (setpoint 1.0), and STRUCT6 holds z 1.01 m.

**Lead: of1 rotation leak.** In the swing band, the velocity feedback over the FC gyro is 85-103 cm/rad (flight
logs). That matches the slope of the raw of1 flow, about 3x the physical g/w^2 (31 cm/rad at 0.9 Hz). The flown
EKF (41c9dea, two-channel) fuses of1 raw with no gyro compensation (R_of1 1e-3), so body rotation reads as
velocity. Bench model: `sp['of_rot_leak'] = k` (velocity error = k * height * body rate). From the logs, k is about
-0.6. At k -1 the STRUCT6 swing grows (pitch sd 2.1 -> 5.2 deg, load 31 -> 56 deg), which is the flight signature.

**Keil presets in the bench** (probe9; sd = std per 4 s window, deg; load = max swing angle per 4 s window):

| run | of1 leak | pitch sd | roll sd | load max (deg) | z (m) |
|---|---|---|---|---|---|
| PID | -0.6 | 1.2-1.4 | 3.6 -> 1.2 | 29 -> 20 | 0.47 (sinks) |
| p0 flown (STRUCT6, of1 on) | -0.6 | 1.1 -> 2.7 -> 2.0 | 3.7, 2.0, 3.2, 3.7, 3.5 | 29, 22, 23, 27, 27 | 1.01 |
| p1 of1 off | 0 | 0.9-1.2 | 2.9 -> 0.7 | ~16 | 1.01 |
| p2 of1 off + p/r gamma x0.1 | 0 | 0.9-1.2 | 2.9 -> 0.7 | ~16 | 1.01 |
| p3 of1 off + MRAC z only | 0 | 0.9-1.2 | 2.9 -> 0.7 | ~16 | 1.01 |
| p4 MRAC z only, of1 on | -0.6 | - | 2.7-3.0 | 20-24 | - |
| p2 gamma x0.1 with of1 on | -0.6 | - | 2.6-3.1 | 25 | - |

Reading (PROPOSED): with the load hanging at z 1.0, the of1 leak keeps the swing alive even with pitch/roll MRAC
off (p4) or slowed (gamma x0.1). Turning of1 off removes it. PID only looks calm in flight because it sank and the
bottle rested on the ground.

**PID sink: Z_ratePID clamps** (probe10, PID only, leak -0.6; z mean per 4 s window):

| Z_ratePID UMax / UiMax / SumEMax | z (m) per 4 s | pitch sd end |
|---|---|---|
| 300 / 100 / 250 (flown) | 0.53 -> 0.46 | 1.4 |
| 300 / 300 / 700 | 0.53 -> 0.73 | 1.4 |
| 500 / 100 / 250 | 0.54 -> 0.46 | 1.4 |
| 500 / 300 / 700 | 0.54 -> 0.98-1.00 | 1.7 |
| 600 / 400 / 920 | 0.54 -> 1.01 | 1.9 |

Both clamps bind: Up ~268 + Ui 100 clips at UMax 300, and raising UMax alone changes nothing. Firmware presets 5 and
6 (c2e1f96) set 500 / 300 / 700 (column zhd). Presets 0-4 restore the boot values.

**Headroom with of1 off** (probe11, leak 0, sling 0.5 kg, 2 seeds; roll sd and load max per 4 s window, start -> end):

| Z_ratePID clamps | controller | roll sd (deg) | load max (deg) | z (m) |
|---|---|---|---|---|
| flown 300 / 100 / 250 | PID | 2.8 -> 0.9 | 21 -> 15 | 0.48 |
| flown | p1 (MRAC all axes) | 2.9 -> 0.7 | 21 -> 17 | 1.01 |
| flown | p3 (MRAC z only) | 2.8 -> 0.7 | 21 -> 15 | 1.01 |
| zhd 500 / 300 / 700 | PID | 2.4 -> 0.8 | 20 -> 16 | 1.01 |
| zhd | p1 | 2.4 -> 0.8 | 20 -> 17 | 1.01 |
| zhd | p3 | 2.4 -> 0.9 | 20 -> 16 | 1.01 |

Reading (PROPOSED): the headroom removes the PID sink in the bench (0.48 -> 1.01 m) and changes nothing for MRAC,
which already held height. With of1 off and headroom on, PID and MRAC look alike in the bench; the flight is the test.

## Morning plan (PROPOSED, bench only, not flown)

1. Flash branch `overnight-2026-10-08` (build checked, 0 errors). Fallback: tag `fw-flown-2026-10-07` or the archived
   `OBJ/archive/JX_FLY_834c8564.axf`; preset 0 is the flown configuration and is the boot default.
2. Disarmed, in the Keil watch window: `kp_id = 1` (or 3), `kp_go = 1`, check `kp_active`. Then fly the load manually.
3. If the swing is gone, try preset 5 (PID vs MRAC without the sink) and 6.
4. If the swing persists with of1 off, the leak is not the driver: stop and go back to preset 0 or the flown axf.

## Plots

`python -m ground_station.analysis.flight_review logs/<stem>.slot0.csv --no-sat --out <page>` now reads stream_log
captures. Pages (setpoint vs actual, spectra, sample intervals): `docs/flights/plots/2026-10-07-<stem>.review.html`
for exp1-500g, exp2_mrac-500g, exp5_pid_then_mrac_active-500g. Whole-log spectral peaks (measured):

| flight | pitch angle | roll angle | gyroy FB | gyrox FB |
|---|---|---|---|---|
| exp1 PID | 0.56 Hz | 0.54 Hz | 0.84 Hz | 0.90 Hz |
| exp2 MRAC | 0.77 Hz (amp 1.13) | 0.56 Hz | 1.69 Hz (amp 11.8) | 1.69 Hz |
| exp5 PID then MRAC | 0.93 Hz (amp 1.32) | 0.81 Hz | 0.93 Hz (amp 8.3) | 0.85 Hz |

A simple pendulum of L 0.43 m (33 cm rope + half the bottle) swings at 0.76 Hz, which matches the exp2 pitch line.
The MRAC flights carry the large pitch-rate lines, in line with the swing the pilot saw.
