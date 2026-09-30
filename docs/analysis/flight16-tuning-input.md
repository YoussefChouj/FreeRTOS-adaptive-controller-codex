# flight16 - tuning input

Source: `python -m ground_station.analysis.flightlab analyze` on `logs/vofa/flight16*` and
`compare flight15 flight16` (reports in `logs/vofa/reports/flight16/report.md` and
`logs/vofa/reports/compare_flight15_vs_flight16/compare.md`, untracked; regenerate with the same commands).
Every number below is copied from those reports (run 2026-09-30). Recommendations are flightlab rule
outputs, not verified tuning decisions.

## Flight

| Field | Value |
|---|---|
| started_at | 2026-09-29 08:51:13, duration 127 s |
| git / notes | 5f9baaf / `pid_flight_test_poshold_kp06` |
| mrac_mode / ctrl_select | shadow / 1 |
| airborne | 1 segment, 87.24 s; steady 5 segments, 65.85 s |
| data quality | slots 0-3 at 100/100/25/10 Hz, 0 seq drops, 0 tsrc gaps; clock drift -39.92 ppm |
| recommendations | 17 warn, 6 info, 0 critical |

Changes vs flight15 (git 51b19a6 -> 5f9baaf): `100e7b2 pid: roll rate Kd 10->8, position-hold Kp 0.8->0.6`.
flight15 used preset `flight_default` and MRAC mode None, so some variables differ between the two logs.

## Steady-state loop numbers (flight16)

| Loop | e_mean | e_rms | integrator sat frac | osc peak Hz | lag ms |
|---|---|---|---|---|---|
| rate_roll | 12.62 | 20.1 | 0 | 4.5 | 40 |
| rate_pitch | 4.469 | 6.917 | 0 | 1.5 | 40 |
| rate_yaw | 1.767 | 6.571 | 0 | 1 | 400 |
| att_roll | 0.06502 | 0.8611 | 0.1196 | 1 | 280 |
| att_pitch | 0.09545 | 1.142 | 0.02125 | 1 | 280 |
| att_yaw | 0.1992 | 0.7888 | 0.5774 | 1 | n/a |
| alt_pos | -0.00711 | 0.1273 | n/a | 1 | n/a |
| alt_rate | 0.0127 | 0.1424 | n/a | 3 | 120 |
| pos_x | -10.66 | 11.35 | 0.9894 | 1.053 | n/a |
| pos_y | 1.012 | 3.127 | 0.4932 | 1.053 | n/a |
| vel_x | -6.814 | 7.965 | n/a | 1 | 340 |
| vel_y | 0.1067 | 4.876 | n/a | 1 | 440 |

## What the numbers point at

1. **pos_x is pinned.** Steady bias -10.66 with the integrator saturated 98.9 % of the time (airborne 0.984).
   pos_y is saturated 49 %. Rules: raise `Ctrler.locxPID.SumEMax` / `locyPID.SumEMax`, then `locxPID.Ki`.
   The constant x offset is also consistent with an x-axis trim or optical-flow bias; check before adding Ki.
2. **Roll rate error is 3x pitch** (e_rms 20.1 vs 6.917, roll bias e_mean 12.62). Compare: after Kd 10->8 the
   rate_roll/rate_pitch spectral peaks moved from 2-4 Hz to 25 Hz and u band power 8-20 Hz rose
   170.1 -> 1291 (roll) and 207.6 -> 1735 (pitch). Check the 25 Hz content before any further D change.
3. **Yaw.** Motor yaw-pair imbalance -21.03 % (threshold 5 %), rate_yaw u_mean 113.1, att_yaw integrator
   saturated 57.7 %. Rule output: props/motors first (hardware), then `yawPID.SumEMax`; the lag rules
   (`gyrozPID.Kp`, `yawPID.Kp` x1.15) only after the imbalance is fixed.
4. **Velocity loops lag** (vel_x phase -112 deg, vel_y -97.7 deg airborne). Rule: `locxsPID.Kp` / `locysPID.Kp` x1.15.
5. **Battery sag** 0.41 V/cell (threshold 0.3). Use a fresh pack for every demo run and log the rest voltage.

## MRAC readiness (shadow mode; matters for the PID vs MRAC demo)

Steady authority ratio RMS(u_ad) / RMS(u_nom) (`plugins/mrac.py:122`, threshold 0.5): pitch 2.19, roll 1.37, yaw 1.09, z_rate 1.29.
z_rate Theta[0] drifts. At these gains the adaptive term would dominate the PID if injected. Reduce gamma or
`mrac_to_mixer` (or enable projection on z) and re-check the ratio in shadow before an MRAC-injected flight.

## Logging gaps (info)

PID gains are not streamed for any loop (add a 1 Hz params slot); SumE missing for alt_pos, alt_rate, vel_x,
vel_y; 22 variables stuck while airborne (names in the report's evidence column).
