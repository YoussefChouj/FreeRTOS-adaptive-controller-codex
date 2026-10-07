# 2026-10-08 exp8: MRAC variants vp 0-6, no load, of1 off (preset 5)

Log `logs/exp8_vpA_noload.slot0-3.csv` (100/50/50/10 Hz nominal, 0 drops). Page with plots:
`docs/flights/plots/2026-10-08-exp8.review.html`, section "Variant segments" (one row and one figure per vp).
Numbers are measured from the log; readings marked PROPOSED are interpretation.

## Bottom line

- **On attitude, vp 6 (3L, lam_ang 4) is clearly best:** lowest pitch/roll sd in stick-free hover with MRAC on
  (1.6 / 2.0 deg vs 2.7-4.4 for the others), and u_ad is uncorrelated with the rate (+0.02 / +0.01), so it does not
  push a swing. That is the property that failed under load on 10-07 (corr +0.4..+0.7).
- **vp 0 (S6 x1, flown law) pushes with the motion** (corr +0.33 / +0.33): the worst choice for the load.
- **The drift is the position estimate, not the MRAC variant.** With of1 off the EKF walks 1.3-3.9 m per flight,
  always toward +x / -y. In vp 3 the drone landed on the pad (operator), yet the EKF says 3.9 m: the operator's
  stick cancelled about 6.5 cm/s of estimate drift.
- MRAC on still raises attitude sd over PID in no-load hover for every variant except vp 3 pitch. Expected with no
  load (PID is tuned for this plant); the load test is where MRAC has to earn it.

## Per variant (stick-free hover = both x/y setpoints moving slower than 5 cm/s)

| vp | variant | air s | still on s | pit sd off / on | rol sd off / on | z rms on (m) | corr(u_ad, rate) p / r | u_ad rms p / r |
|---|---|---|---|---|---|---|---|---|
| 0 | S6 x1 | 101 | 41 | 0.50 / 2.95 | 0.90 / 3.46 | 0.038 | **+0.33 / +0.33** | 0.011 / 0.014 |
| 1 | S6 x0.25 | 58 | 14 | 1.89 / 3.26 | 1.05 / 3.20 | 0.032 | +0.05 / +0.38 | 0.006 / 0.006 |
| 2 | V1 | 64 | 35 | n/a / 2.94 | n/a / 2.82 | 0.144 | -0.05 / 0.00 | 0.006 / 0.011 |
| 3 | V2 (mu_sat 0.85) | 60 | 16 | 3.15 / 3.07 | 1.61 / 4.35 | **0.022** | +0.21 / +0.04 | 0.005 / 0.007 |
| 4 | PR | 54 | 28 | 0.45 / 2.68 | 0.56 / 3.75 | 0.129 | +0.13 / +0.13 | 0.005 / 0.006 |
| 5 | ST | 56 | 31 | 1.02 / 3.01 | 0.87 / 4.07 | 0.030 | +0.13 / +0.06 | 0.006 / 0.007 |
| 6 | 3L (lam_ang 4) | 99 | 24 | 1.09 / **1.63** | 1.20 / **1.97** | 0.023 | **+0.02 / +0.01** | 0.013 / 0.014 |

sd in deg. "off" = ch8 off (PID flies, MRAC shadow), mostly the first hover after takeoff, so off z rms (0.13-0.26 m)
holds the takeoff transient and is not compared. vp 1 and vp 3 have only 14-16 s of stick-free MRAC hover: their
numbers are the least certain. No motor reached the 4000 rail in any segment. vp 2 had ch8 on the whole flight.

## Drift with of1 off

| vp | EKF takeoff-to-landing dx / dy (m) | EKF walk rate (cm/s) | raw of1 flow integral dx / dy (m) |
|---|---|---|---|
| 0 | +3.19 / +0.04 | 3.2 | +0.74 / +0.48 |
| 1 | +1.51 / -1.06 | 3.2 | +0.89 / +0.07 |
| 2 | +2.34 / -3.00 | 5.9 | +1.10 / +0.18 |
| 3 | +1.85 / -3.44 | 6.5 | +1.29 / +0.30 |
| 4 | +1.23 / -1.03 | 3.0 | +0.73 / +0.44 |
| 5 | +1.06 / -0.83 | 2.4 | +0.31 / -0.18 |
| 6 | +2.63 / -2.25 | 3.5 | +1.05 / +0.55 |

Reading (PROPOSED):
1. Same sign in every flight: a systematic bias, not noise. With of1 off nothing observes `bof` (the of2
   gyro-fix velocity bias), so the EKF velocity carries a constant offset.
2. The position loop holds that biased estimate on the setpoint, so the real drone moves the other way at the
   bias speed: the drift the operator saw. The operator trims it with the stick, which moves the setpoint with
   the estimate (both walk; the real drone stays near the pad, as in vp 3).
3. The raw of1 flow is not ground truth either (rotation leak k ~ -0.6, scale): 0.3-1.3 m over the flight.
4. Open item: x / y error against the setpoint in stick-free hover is 0.24-0.69 m rms. The position loop holds
   the estimate loosely; not investigated yet.

Fix options (operator decides; all PROPOSED):

| option | how | cost |
|---|---|---|
| A. keep of1 off, trim with the stick | as flown | none; worked in vp 3 |
| B. of1 on with a weak gain | Keil: `g_ekf_of1_on = 1` and raise the EKF's `R_of1` from 1e-3 (e.g. 1e-2): slow `bof` learning, less swing-band leak | no flash; check against the 10-07 swing |
| C. learn `bof` with of1 on in the first hover, then of1 off | Keil, two writes | no flash |
| D. rotation compensation of of1 (subtract k x rate) | firmware | build + flash |

For the load test today: A (unchanged, known). B is the first thing to try after it.

## Recommendation for the load test (PROPOSED)

vp 6 first, vp 3 second. vp 3's clean landing comes from the operator's trim, not from the variant; vp 6 is the
variant with the least attitude noise and no push-with-the-motion signature. Both are one Keil write
(`vp_id`), on the ground. Runbook: `docs/flights/2026-10-08-load-test-runbook.md`.
