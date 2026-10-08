# Item B: the firmware S10X swing row in the log replay (2026-10-09 overnight)

Question: does the MULTI basis 6 (S10X: sin, |p|un, un|un|, angacc, ANF accel swing in-phase/quadrature, sag,
motor lag; `API/mrac.c` MRAC_GenExt) with the L2 law beat the best law so far, "L2only g8" (row 17)?

Tool: `python -m ground_station.analysis.mrac_log_replay --set 3l --shadow` and `--set s10x --shadow`, 59 logs.
Driver args added: `acc:1` (feeds mrac_in_acc from Acc_X/Y_Real, mg x 0.00980665), `swref:<v>`
(mrac_vp12.swing_ref), `mask:<v>` (ext-slot mask on pitch and roll, from tick 0).

## Measured (cancel ratio median (worst log); 0 = no effect, > 0 = part of the disturbance removed)

| variant | pitch | roll |
|---|---|---|
| L2only g8 (row 17, STRUCT6) | -0.07 (-2.80) | -0.09 (-4.01) |
| MULTI L2only g8 (basis 0, build control) | -0.07 | -0.09 (identical: the MULTI port is exact) |
| S10X L2only g8 | -0.21 (-65.98) | -0.11 (-114.18) |
| S10X L2only g20 | -0.22 | -0.16 |
| S10X L2only g8, swing_ref 2 / 5 m/s^2 | -0.21 / -0.21 (-60) | -0.15 / -0.16 (-103) |
| S10X + L2 g8 (tracking-error law on) | -0.37 | -0.32 |
| S10X vp6 law | -4.65 | -2.95 |
| S10X L2only g8, all ext slots masked off (mask 0) | -0.21 (-59.57) | -0.16 (-101.39) |
| ... core phi 0-2 / no swing / no angacc / no sag / no lag | -0.21 each | -0.11 .. -0.16 |

## What it says

- S10X beats row 17 on 32 of 59 logs, but every blow-up (cancel -5 to -66) is on a log flown with MRAC
  injection on (exp2, exp11, exp12, exp13, exp14 vp8-vp11, f17, flight_test_hover). On the 34 PID logs mask 0
  equals L2only g8 exactly.
- The swing features are not the cause: swing_ref x4 and x10 change nothing, and with every ext slot masked
  off the blow-up stays (-51.8 on exp11 vs -62.6 with all slots on). Masking works (the values move).
- So the cause is in the basis != 0 path itself (14 features, VID_RBF variant id) on injected logs, not in
  the regressor. Not found tonight. It may be a replay artefact (the flown u on those logs contains the flown
  variant's u_ad), since vp8-vp11 flew without diverging on 2026-10-08.

## Verdict

S10X is **not** in the morning pack. Row 17 (L2only g8, STRUCT6) stays the 3L candidate. Open: find what the
basis != 0 path does differently on injected logs before any MULTI basis row is flown with L2.
