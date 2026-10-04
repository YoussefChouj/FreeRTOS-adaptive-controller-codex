# SIL limit tests of the MRAC variants, 2026-10-04 (WP-33)

`python -m sim.sil.limits`: 1353 runs (seeds 0, 1, 2), wall 573 s. Generated; do not edit. SIL only, unvalidated against flight (the WP-31 matrix caveat applies). Setup and rules: the `sim/sil/limits.py` docstring. Every threshold and preset here is PROPOSED.

A point fails when most seeds trip an abort that pid does not trip in the same condition and seed (tilt: > 12 deg, or > pid + 2 deg where pid trips T). Cells: doublet position RMSE cm at +0 ms (seed mean), then the failing conditions with their flags. X crash, U |u_ad| > 0.5 |u_nom|, T tilt, C motor at 4000 > 0.5 s, S simplex, P error > 0.5 m. Conditions: +0/+5/+10 ms extra command delay, +0 n2 = sensor noise x2.

pid on the same doublet (reference, seed mean): +0 ms: rmse 7.2 cm, tilt 8.9 deg, T in 0/3 seeds; +5 ms: rmse 7.9 cm, tilt 11.8 deg, T in 2/3 seeds; +10 ms: rmse 8.3 cm, tilt 13.1 deg, T in 3/3 seeds; +0 n2 ms: rmse 10.3 cm, tilt 10.3 deg, T in 0/3 seeds.

## Stage 1: gain sweep (G = gamma multiplier on pitch/roll)

| variant | G 0.25 | G 0.5 | G 1 | G 2 | G 4 | G 8 | G 16 | G 32 | G 64 | G 128 |
|---|---|---|---|---|---|---|---|---|---|---|
| v1 | 7.7 UT(+5) | 8.0 T(+0 n2) | 9.6 T(+0) T(+0 n2) | 10.5 T(+0) T(+0 n2) | 11.0 T(+0) T(+5) T(+0 n2) | 8.4 T(+0) T(+5) T(+10) T(+0 n2) | 6.1 T(+5) | 7.0 T(+10) T(+0 n2) | 7.2 T(+0 n2) | 7.5 T(+0) T(+5) T(+0 n2) |
| v2 | 7.8 U(+5) T(+0 n2) | 8.0 | 9.5 T(+0) T(+0 n2) | 10.6 T(+0) T(+0 n2) | 11.5 T(+0) T(+5) T(+0 n2) | 8.1 T(+0) T(+5) T(+10) T(+0 n2) | 6.2 | 7.1 T(+10) T(+0 n2) | 6.9 T(+0 n2) | 7.7 T(+0) T(+5) T(+10) T(+0 n2) |
| pr | 7.8 | 8.1 | 11.2 T(+0) T(+5) T(+0 n2) | 15.7 T(+0) TP(+5) T(+10) T(+0 n2) | 17.8 T(+0) TP(+5) TP(+10) T(+0 n2) | 8.0 T(+0) T(+5) TP(+10) T(+0 n2) | 5.6 T(+10) | 7.2 T(+5) T(+10) T(+0 n2) | 8.3 T(+0) T(+5) T(+10) T(+0 n2) | 8.8 T(+0) T(+5) T(+10) T(+0 n2) |
| st | 9.0 T(+0 n2) | 8.4 T(+0) T(+5) T(+0 n2) | 7.6 T(+0) UT(+5) T(+10) T(+0 n2) | 6.2 T(+5) T(+10) T(+0 n2) | 6.9 T(+10) T(+0 n2) | 7.1 T(+5) T(+0 n2) | 7.2 T(+0) T(+0 n2) | 8.5 T(+0) T(+5) T(+10) UT(+0 n2) | 8.3 T(+0) T(+10) T(+0 n2) | 9.0 T(+0) T(+5) T(+10) T(+0 n2) |
| st_e2 | 8.0 | 8.9 T(+0 n2) | 9.1 T(+0) T(+5) T(+0 n2) | 7.8 T(+5) T(+0 n2) | 5.7 T(+5) T(+10) T(+0 n2) | 6.5 | 7.0 T(+10) | 7.3 | 7.2 T(+10) T(+0 n2) | 7.4 T(+0) |
| st_p2 | 8.0 | 9.5 T(+0) T(+0 n2) | 10.4 T(+0) T(+0 n2) | 11.8 T(+0) T(+5) T(+0 n2) | 8.6 T(+0) T(+5) T(+10) T(+0 n2) | 6.2 T(+5) | 7.1 T(+0 n2) | 7.2 | 7.6 T(+0) T(+0 n2) | 7.6 T(+0) T(+0 n2) |
| st_bar | 6.6 T(+5) T(+0 n2) | 7.3 T(+5) T(+10) T(+0 n2) | 6.7 T(+5) T(+10) T(+0 n2) | 6.9 T(+5) | 7.2 T(+10) T(+0 n2) | 7.6 T(+0) T(+5) T(+0 n2) | 7.9 T(+0) T(+5) T(+10) T(+0 n2) | 8.4 T(+0) T(+5) T(+10) UT(+0 n2) | 9.2 T(+0) T(+5) T(+10) UT(+0 n2) | 13.1 UT(+0) UT(+5) T(+10) UT(+0 n2) |
| lfhg | 7.6 U(+5) T(+0 n2) | 8.1 | 9.2 T(+0) T(+0 n2) | 9.9 T(+0) T(+0 n2) | 10.9 T(+0) T(+5) T(+0 n2) | 9.6 T(+0) T(+5) T(+10) T(+0 n2) | 6.5 T(+0) T(+5) T(+10) T(+0 n2) | 6.8 T(+0 n2) | 6.7 T(+0 n2) | 7.2 T(+0 n2) |

High-frequency content of the injected correction at +0 ms (RMS tick-to-tick change of corr p/r, mixer units, seed mean; pid injects 0):

| variant | G 0.25 | G 0.5 | G 1 | G 2 | G 4 | G 8 | G 16 | G 32 | G 64 | G 128 |
|---|---|---|---|---|---|---|---|---|---|---|
| v1 | 0.4 | 0.4 | 0.9 | 1.9 | 4.8 | 5.8 | 4.9 | 5.7 | 6.2 | 6.3 |
| v2 | 0.5 | 0.5 | 0.9 | 1.9 | 4.9 | 5.6 | 4.7 | 5.8 | 6.1 | 6.5 |
| pr | 0.1 | 0.2 | 0.4 | 0.8 | 2.0 | 3.7 | 4.6 | 6.5 | 6.9 | 7.6 |
| st | 1.8 | 4.4 | 5.7 | 5.0 | 6.7 | 6.9 | 9.4 | 11.4 | 9.8 | 9.9 |
| st_e2 | 0.4 | 1.3 | 2.3 | 4.6 | 4.2 | 5.6 | 6.4 | 6.0 | 6.4 | 7.4 |
| st_p2 | 0.5 | 1.0 | 2.1 | 5.0 | 6.5 | 4.9 | 6.0 | 6.2 | 6.9 | 7.6 |
| st_bar | 2.3 | 5.0 | 5.7 | 7.6 | 9.0 | 10.8 | 10.8 | 12.1 | 10.9 | 15.4 |
| lfhg | 0.5 | 0.5 | 0.9 | 1.8 | 3.7 | 5.1 | 4.6 | 4.8 | 5.6 | 5.5 |

## Envelope, flight gain and margins

Gain edge = the first G that fails at +0 ms. Flight G = the largest grid G <= edge / 4 and <= the firmware bound (gamma_scale 2; LFHG lf_gain 10); 0 = none, then the margins are those of G 0.25. Delay margin = the largest extra delay up to which every delay passes at the flight G.

| variant | stable G at +0 ms | gain edge | first G failing any condition | flight G | gain margin | delay margin | noise x2 | first failure at the gain edge (+0 ms) |
|---|---|---|---|---|---|---|---|---|
| v1 | 0.25 .. 0.5 | 1.0 | 0.25 | 0.25 | x4 | +0 ms | pass | T tilt in 3/3 seeds (u_ad/u_nom 0.23, tilt 13.4 vs pid 9.0, max err 24 cm, hf 0.9) |
| v2 | 0.25 .. 0.5 | 1.0 | 0.25 | 0.25 | x4 | +0 ms | fail | T tilt in 3/3 seeds (u_ad/u_nom 0.28, tilt 13.4 vs pid 9.0, max err 24 cm, hf 0.9) |
| pr | 0.25 .. 0.5 | 1.0 | 1.0 | 0.25 | x4 | +10 ms | pass | T tilt in 2/3 seeds (u_ad/u_nom 0.17, tilt 13.2 vs pid 9.0, max err 26 cm, hf 0.4) |
| st | 0.25 .. 0.25 | 0.5 | 0.25 | 0 | - | +10 ms | fail | T tilt in 3/3 seeds (u_ad/u_nom 0.34, tilt 14.0 vs pid 9.0, max err 26 cm, hf 4.4) |
| st_e2 | 0.25 .. 0.5 | 1.0 | 0.5 | 0.25 | x4 | +10 ms | pass | T tilt in 2/3 seeds (u_ad/u_nom 0.21, tilt 14.5 vs pid 9.0, max err 29 cm, hf 2.2) |
| st_p2 | 0.25 .. 0.25 | 0.5 | 0.5 | 0 | - | +10 ms | pass | T tilt in 3/3 seeds (u_ad/u_nom 0.26, tilt 13.5 vs pid 9.0, max err 25 cm, hf 1.0) |
| st_bar | 0.25 .. 4 | 8.0 | 0.25 | 2 | x4 | +0 ms | pass | T tilt in 2/3 seeds (u_ad/u_nom 0.30, tilt 12.5 vs pid 9.0, max err 20 cm, hf 10.9) |
| lfhg | 0.25 .. 0.5 | 1.0 | 0.25 | 0.25 | x4 | +0 ms | fail | T tilt in 2/3 seeds (u_ad/u_nom 0.38, tilt 13.1 vs pid 9.0, max err 24 cm, hf 0.9) |

## Flight presets (the campaign presets of the descriptors)

Rule: among a variant's settings, the one with a flight G, then the widest delay margin, then noise x2 passed, then the highest flight G. The rest of the V1 drive is mrac_v1.yaml v1_refmodel_g1 on pitch/roll.

| variant | descriptor | setting | knobs (pitch/roll) | flight G | gain margin | delay margin | noise x2 | rmse +0 cm |
|---|---|---|---|---|---|---|---|---|
| pr | mrac_pr.yaml | pr | kappa_pr 0.5, crm_ell 10, gamma_scale 0.25 | 0.25 | x4 | +10 ms | pass | 7.8 |
| st | mrac_st.yaml | st_e2 | st_eps 2, st_phi_max 10, gamma_scale 0.25 | 0.25 | x4 | +10 ms | pass | 8.0 |
| lfhg | mrac_lfhg.yaml | lfhg | sigma_lf 0.8, gam_f 16, lf_gain 0.25 (gamma_scale 1) | 0.25 | x4 | +0 ms | fail | 7.6 |

### Hard-freeze attribution

The failing edge points re-run with hard_freeze_on 0 (CMD 0x0F idx 3). The freeze zeroes u_ad in one tick when |e| > e_freeze (1.2 rad/s p/r) and the u_ad low-pass ramps it back; under delay this repeats at a few Hz.

| variant | G | condition | firmware: failing seeds, flags | freeze off: failing seeds, flags | hf firmware -> freeze off |
|---|---|---|---|---|---|
| v1 | 1 | +0 ms | 3/3 T | 3/3 T | 0.9 -> 0.4 |
| v1 | 0.25 | +5 ms | 2/3 UT | 0/3 - | 5.5 -> 0.1 |
| v2 | 1 | +0 ms | 3/3 T | 3/3 T | 0.9 -> 0.4 |
| v2 | 0.25 | +5 ms | 2/3 U | 0/3 - | 5.3 -> 0.1 |
| pr | 1 | +0 ms | 2/3 T | 2/3 T | 0.4 -> 0.4 |
| st | 0.5 | +0 ms | 3/3 T | 2/3 T | 4.4 -> 1.2 |
| st | 0.25 | +0 n2 ms | 2/3 T | 3/3 T | 3.6 -> 0.7 |
| st_e2 | 1 | +0 ms | 2/3 T | 3/3 T | 2.3 -> 0.8 |
| st_p2 | 0.5 | +0 ms | 3/3 T | 3/3 T | 1.0 -> 0.4 |
| st_bar | 8 | +0 ms | 2/3 T | 3/3 T | 10.8 -> 6.8 |
| st_bar | 2 | +5 ms | 2/3 T | 1/3 T | 9.6 -> 4.7 |
| lfhg | 1 | +0 ms | 2/3 T | 2/3 T | 0.9 -> 0.4 |
| lfhg | 0.25 | +5 ms | 2/3 U | 0/3 - | 5.7 -> 0.1 |

## Stage 2: the variants' own knobs at the flight gain (+0 and +10 ms)

Each knob is swept alone around the variant's sweep centre (ST: st_eps 1.0, st_phi_max 10).

| variant | G | knob | values | pass range | failures | centre, or nearest pass |
|---|---|---|---|---|---|---|
| v1 | 0.25 | ref_model_bw | 5, 10, 20, 44, 80 | 5 .. 80 | - | 44 |
| pr | 0.25 | ref_model_bw | 5, 10, 20, 44, 80 | 5 .. 80 | - | 44 |
| pr | 0.25 | kappa_pr | 0.25, 0.5, 1, 1.5, 2 | 0.25 .. 2 | - | 0.5 |
| pr | 0.25 | crm_ell | 2, 5, 10, 20, 35, 50 | 2 .. 50 | - | 10 |
| st | 0.25 | ref_model_bw | 5, 10, 20, 44, 80 | 44 .. 80 | 5: UTP(+0); 10: TP(+0); 20: T(+0) | 44 |
| st | 0.25 | st_eps | 0.2, 0.3, 0.5, 0.8, 1, 1.2, 2 | 1 .. 2 | 0.2: T(+0); 0.3: T(+0); 0.5: T(+0); 0.8: T(+0) | 1 |
| st | 0.25 | st_phi_max | 2, 5, 10, 50 | 2 .. 10 (gaps) | 5: T(+0); 50: T(+0) T(+10) | 10 |
| st | 0.25 | st_bar | 0.05, 0.2, 0.5, 1 | 0.2 .. 0.2 | 0.05: T(+0); 0.5: T(+10); 1: T(+10) | 0 |
| lfhg | 0.25 | ref_model_bw | 5, 10, 20, 44, 80 | 5 .. 80 | - | 44 |
| lfhg | 0.25 | gam_f | 0.5, 2, 8, 16, 32, 64, 100 | 0.5 .. 100 | - | 16 |
| lfhg | 0.25 | sigma_lf | 0.1, 0.8, 2, 5 | 0.1 .. 5 | - | 0.8 |

Stage 2 position RMSE cm at +0 ms, seed mean (the knob's effect inside the envelope):

| variant | knob | value: rmse |
|---|---|---|
| v1 | ref_model_bw | 5: 8.1, 10: 7.9, 20: 7.8, 44: 7.7, 80: 7.6 |
| pr | ref_model_bw | 5: 7.8, 10: 7.6, 20: 7.7, 44: 7.8, 80: 7.8 |
| pr | kappa_pr | 0.25: 7.9, 0.5: 7.8, 1: 7.6, 1.5: 7.6, 2: 7.6 |
| pr | crm_ell | 2: 7.8, 5: 7.8, 10: 7.8, 20: 7.7, 35: 7.7, 50: 7.6 |
| st | ref_model_bw | 5: 49.4, 10: 35.2, 20: 12.4, 44: 9.0, 80: 7.8 |
| st | st_eps | 0.2: 10.4, 0.3: 11.5, 0.5: 10.8, 0.8: 10.5, 1: 9.0, 1.2: 7.8, 2: 8.0 |
| st | st_phi_max | 2: 8.0, 5: 9.5, 10: 9.0, 50: 7.7 |
| st | st_bar | 0.05: 8.4, 0.2: 6.6, 0.5: 7.3, 1: 7.5 |
| lfhg | ref_model_bw | 5: 8.2, 10: 7.9, 20: 7.8, 44: 7.6, 80: 7.6 |
| lfhg | gam_f | 0.5: 7.8, 2: 7.8, 8: 7.7, 16: 7.6, 32: 7.8, 64: 7.7, 100: 7.8 |
| lfhg | sigma_lf | 0.1: 7.9, 0.8: 7.6, 2: 7.8, 5: 7.8 |
