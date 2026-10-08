# exp18 (2026-10-09): pure PID vs vp13, back to back

Logs: `logs/pid_ref_2` (PID, flown first) and `logs/vp13_g4_last` (vp13 gamma x4, injected). Per-flight details:
`pid/adaptive_stats.md`, `vp13/adaptive_stats.md` (`python -m ground_station.analysis.adaptive_review`).
Load not stated by the operator. The roll trim sign (+0.054) matches the 570 g rope (exp16 +0.058), not the 293 g
arm (exp17 -0.06..-0.075), and both flights show the rope swing peaks 0.39/0.49 Hz. The "expected static torque"
line in the stats files assumes the rope.

| metric (whole airborne segment) | PID | vp13 | vp13 vs PID |
|---|---|---|---|
| airborne time (s) | 52 | 69 | |
| pitch sd (deg) | 3.10 | 2.66 | -14% |
| roll sd (deg) | 3.63 | 3.81 | +5% |
| rate sd pitch / roll (deg/s) | 10.6 / 12.9 | 10.4 / 13.8 | -2% / +7% |
| swing-band PSD pitch / roll | 31.8 / 61.5 | 32.5 / 63.1 | +2% / +3% |
| mean height error z - z_des (m) | -0.259 | +0.000 | 26 cm sag gone |
| Z error above e_sat (% of time) | 36.2 | 11.7 | |
| motor at the 4000 limit (% of time) | 2.7 | 7.0 | more saturation |
| battery V mean | 14.73 | 14.54 | flown second |
| stab CPU % mean | 8.7 | 9.7 | |
| x / y position Des - FB mean (cm) | +0.7 / -0.8 | -0.4 / -4.5 | |
| stick on, x (% of time) | 17 | 18 | similar piloting |

## What vp13's adaptive term did (vp13 flight)

| axis | static trim carried by u_ad | dynamic cancel (1 = all, < 0 = adds) | swing-band phase vs ideal |
|---|---|---|---|
| pitch | 56% | -0.04 | -90 deg |
| roll | 34% | -0.05 | -94 deg |
| yaw | ~0% | +0.07 | -114 deg |
| Z | 33% | +0.37 | 0 deg |

- Z is the only axis where u_ad cancels the moving disturbance (+0.37), with the right phase.
- Pitch/roll: u_ad takes over part of the static trim. In the swing band it lags 90 deg, so it neither damps nor
  cancels, the same pattern as exp17.
- The pitch sd gain comes from below the swing band: the swing-band PSD is unchanged.
- Confounds: different flight length, the battery was lower for vp13, one flight each. A single pair does not show
  the result is repeatable.
