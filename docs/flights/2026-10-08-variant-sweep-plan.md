# 2026-10-08 MRAC variant sweep: one-write switching, all sim variants, flight series (PROPOSED)

Operator ask: switch variants fast, every sim variant in the firmware, a no-load flight series (hover + roam),
log everything for offline analysis. Every value below is PROPOSED unless a source is named.

## Strategy

- **FW-A (tactical, flash today):** one Keil write `vp_id = N` applies a whole variant row. Only uses code that
  is already in the firmware (S6 + add-ons). Low risk.
- **FW-B (strategic, build-only, then flash):** port the sim feature sets S10, RBF6/12/24 (and S6+RBF12 at
  runtime) as a runtime feature mask, so they become more rows of the same table.
- One pack = one log = several segments. The log labels itself. No load ranks the variants; the best 2 go to the
  500 g demo.

## Switching: one Keil write

- `vp_id = N` in the watch window. Applied when not flying (DISARMED, or EMERGENCY on the ground: the same fix
  for `kp_id`). Sets every field of the row, resets the weights, sets `vp_active = N`. Refused in flight.
- Row = feature set, ref type p/r/y, gamma scale p/r/y, add-on knobs. Aligned `VP_ROW` table
  (`docs/firmware-table-pattern.md`). Rows copy the WP-33 flight presets (`mrac_pr.yaml`, `mrac_st.yaml`, ...).
- Reboot = vp 0 = the flown law.

## Logging

`ground_station/livewatch/exp8_frames.md`: exp1 slots 0-2 at 80 Hz + slot 3 (10 Hz) `vp_active`, `vp_id`,
`kp_active`, `kp_id`, `mrac_var_id[0..3]`, `g_ekf_of1_on`, `g_ctrl_axis_mask`, Z_ratePID UMax. Measured plan: 60,900 B/s
of the 70,042 budget. u_ad/u_nom/u_def/e/e_dot/Theta/Whatf are
already logged. FW-B adds one weight norm per axis instead of 24 weights.

## Variant table

| vp | variant | ref p/r, y | drive | add-on (p/r) | gamma | needs |
|---|---|---|---|---|---|---|
| 0 | S6 flown | passthrough | raw | none | x1 | FW-A |
| 1 | S6 x0.25 | passthrough | raw | none | x0.25 | FW-A |
| 2 | V1 | type 2, type 1 (bw_y 2) | normalized, lam_edot 0.0018 | none | x0.25 | FW-A |
| 3 | V2 | as V1 | as V1 | mu_sat 0.85 (p/r/y) | x0.25 | FW-A |
| 4 | PR | as V1 | as V1 | kappa 0.5, crm_ell 10 | x0.25 | FW-A |
| 5 | ST | as V1 | as V1 | st_eps 2, phi_max 10 | x0.25 | FW-A |
| 6 | 3L | as V1 | as V1 | lam_ang 4 | x0.25 | FW-A |
| 7 | S10 | passthrough | sim 0.18 | lowest sim saturation | FW-B |
| 8 | RBF12 | passthrough | sim 0.32 | unstructured | FW-B |
| 9 | RBF6 | passthrough | sim 0.32 | | FW-B |
| 10 | RBF24 | passthrough | sim 0.32 | | FW-B |
| 11 | S6+RBF12 | passthrough | x0.25 | V3 | FW-B |

LFHG left out: failed the SIL noise test on most seeds. Sim saturation medians (`sim/adaptive_compare/
results_coupled.json`, mc_medians sat_pct): PID 1.75 %, S6 8.59, S10 5.20, RBF6 5.73, RBF12 5.34, RBF24 7.73.

## One segment (repeat per vp)

1. On the ground, disarmed: `vp_id = N`, check `vp_active == N`.
2. ch8 off, take off, hover 15 s (shadow).
3. ch8 on, hover 20 s, then roam 20 s inside the soft fence.
4. ch8 off, land, disarm.

Abort: ch8 off at once (back to PID) if the swing grows over 3 cycles, a motor is pegged for more than ~2 s, or
the soft fence.

| pack | segments | log |
|---|---|---|
| 1 | vp 0, 1, 2, 3, 4, 5, 6 (as far as the pack goes) | `exp8_vpA_noload` |
| 2 | vp 7-11 (after FW-B) | `exp9_vpB_noload` |
| 3 | 500 g: PID, then ch8 with the best 2 | `exp10_vpbest-500g` |

## Offline analysis

`flight_review` gets a "variant segments" section: split by arm cycles, labelled by `vp_active`; per segment
hover sd of pitch/roll/z, x/y drift, corr(u_ad, rate), motor-at-rail share, weight growth, roam tracking error;
one ranking table.

## Work order

1. FW-A: VP table + `vp_id` poll (also in EMERGENCY on the ground), `kp_id` same fix, frame vars. Build 0/0,
   scoped tests. Operator flashes.
2. flight_review variant-segments section.
3. FW-B: features from `sim/adaptive_compare/sim_core.py` `features()` behind a runtime mask; CPU and CCM check;
   parity test against the sim; build-only; operator flashes.
