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
</content>
</invoke>
<invoke name="Bash">
<parameter name="command">cd /d/Projects/UAV_lab/FreeRTOS-adaptive-controller-codex && python - <<'EOF'
import re
s=open('.claude_state.md',encoding='utf-8').read()
s=s.replace("  Do NOT flash. Build-only check OK. Report = docs + plots via SendUserFile at the end.\n",
"  Do NOT flash. Build-only check OK. Report = docs + plots via SendUserFile at the end.\n- 23:58 progress: flown variant = STRUCT6 (no Keil defines), variants OFF, ref type 0. Findings in\n  docs/flights/2026-10-07-load-swing-analysis.md (motors at 4000 always; MRAC z u_ad fixes sink; pitch swing grows).\n  NEXT: u_def/sat fractions, energy-injection check, Z_ratePID I limit, then replay + sim.\n",1)
open('.claude_state.md','w',encoding='utf-8').write(s); print(len(s.encode()))
EOF
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
