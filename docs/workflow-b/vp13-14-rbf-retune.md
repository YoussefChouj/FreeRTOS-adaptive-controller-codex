# vp 13 / vp 14: vp 10 (RBF24) retuned to the flight envelope

Decided by the operator on 2026-10-08 (grilling session). vp 10's RBFs sat nearly flat in the exp14 flights because
their grid was not matched to the drone's real range. vp 13 and vp 14 keep vp 10's row exactly (same law, gamma 0.25,
pitch/roll only, S6 phi[0..3] zeroed, u_nom and xm kept: 30 features) and change only the bump layout.
Old vp 10 stays unchanged so it can be reflown as the reference. Insight feeds the 7-bank vp 15 design
(gap-to-neighbour widths vs Russian-doll hierarchy).

## Inputs (each normalised by its physical limit)

| input | firmware | limit | source |
|---|---|---|---|
| own rate xr | gyro?PID.FB x DEG2RAD / 3.4906585 | 200 deg/s | angle-PID UMax = commanded-rate limit |
| tilt xa | imu_data.pit/rol x DEG2RAD / 0.2617994 | 15 deg | gs_max_pitch/roll_deg |

Bump: g = exp(-0.5 (d/w)^2).

## vp 13 = basis 7 RBF24T (gap width): 6 rate x 4 tilt, index i*4+j

| | centres (normalised) | centres (physical) | widths (physical) |
|---|---|---|---|
| rate | -0.7 -0.3 -0.1 0.1 0.3 0.7 | -140 -60 -20 20 60 140 deg/s | 80 60 40 40 60 80 deg/s |
| tilt | -0.6 -0.2 0.2 0.6 | -9 -3 3 9 deg | 6 deg each |

Width = mean gap to the two neighbours; an edge bump uses its single gap.

## vp 14 = basis 8 RBF24D (Russian doll): 4 nested 3 rate x 2 tilt grids, index l*6+i*2+j

| level l | scale s | rate centres / width (deg/s) | tilt centres / width (deg) |
|---|---|---|---|
| 0 | 1 | -200 0 200 / 200 | -15 15 / 30 |
| 1 | 1/2 | -100 0 100 / 100 | -7.5 7.5 / 15 |
| 2 | 1/4 | -50 0 50 / 50 | -3.75 3.75 / 7.5 |
| 3 | 1/8 | -25 0 25 / 25 | -1.875 1.875 / 3.75 |

## Rows (TASK/StabilizerTask.c s_vp[])

vp 13 and vp 14 = vp 10's VP_ROW with basis 7 / 8. Z axis stays basis 0.

## Fair back-to-back flight plan (PROPOSED)

One pack, same 570 g load, each preset <= 25 s, hover:
PID >= 20 s, old vp 10, vp 13, vp 14 (vp 12 after if the pack allows).
Analysis: `ground_station/analysis/adaptive_review.py` (VP_BASIS 13 -> 7, 14 -> 8; rebuilds the 24 ext features).

## Logging

The vp 12 frame logs Theta[6..13] only. vp 13/14 need Theta[6..29] for pitch and roll: frame pending (budget check,
operator confirms the variable list and rate before recording).

## Tests

`python tools/host_tests.py mrac_inputs` (all three variants): test_retuned_rbf checks both layouts against the
closed form, S6 phi[0..3] zeroed, and all ext features in [0, 1] at the limits.
