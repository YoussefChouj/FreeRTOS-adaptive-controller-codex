# Adaptive-controller roadmap, 2026-10-03 (WP-24 -> WP-27)

**Bottom line.** Hover logs cannot rank the reference-model orders on pitch/roll: the order changes the
0.2-5 Hz rate error by only 6-21 %. The large terms are a standing rate-command offset that saturates the
bias weight, and vibration above 5 Hz. Yaw is different. Its reference model (30 rad/s) runs more than 10x
faster than the yaw loop that flew. A first-order model at about 2 rad/s cuts yaw band error by 58-69 %.
The firmware law also has a hidden trap: switching to type 1 or 2 shrinks the adaptation drive 88-3900x,
and raising gamma cannot fix that. WP-27 should therefore build three switches, all OFF by default:
**V1 reference model v2** (per-axis type, normalized drive), **V2 saturation-aware leakage** (asymmetric
loads) and **V3 RBF12 block** (unstructured, swinging or shifting payload). Fly V1 first. Performance
recovery and the 3-layer routing are not ready to fly.

Measured = computed in this run by the scripts below on the flight logs named. Everything else is PROPOSED
or cited.

```
# logs: main checkout logs/vofa/ (untracked); 5f9baaf (flight16) MRAC law == main (S1b table refactor, bit-exact)
python -m ground_station.analysis.refmodel_replay <main>/logs/vofa/flight16.meta.json <main>/logs/vofa/flight14.meta.json
python -m ground_station.analysis.refmodel_replay <main>/logs/vofa/flight16.meta.json --drive-norm --lam-tau 1
python -m ground_station.analysis.controller_cost
```

## Q1. Reference-model order (measured, shadow flights 16 and 14)

**Data.** Six logs carry the MRAC bus (`mrac_state.<ax>.{x,r,e,u_nom,u_ad}`). Only two are usable for
replay. flight16 (100 Hz, 93 s learn-gated) and flight14 (50 Hz, 39 s) were both shadow flights
(`output_injection_on`=0). Both flew type 0 (passthrough): logged `e` equals `x - r` to within 3e-8, as in
`mrac.c:372-378`. The `ref_model_type` flag itself was never logged. The replay is faithful: replayed vs logged
`u_ad` correlates 0.96/0.95/0.99 (r/p/y) on flight16 and 0.95/0.97 on flight14 pitch/yaw. flight14 roll
correlates only 0.03; that is not explained. The three older logs (git unknown: flight_test_2, _hover_2,
_hover_active_1) have `e != x - r` by 0.12-1.31 rad/s, so a different law flew and they are not replayed.
flight_test_hover_shadow_1 lacks `e`/`u_ad`. Replay = `refmodel_replay.replay_axis`, which mirrors
`MRAC_UpdateAxis` (`mrac.c:325-549`) at 200 Hz on linearly interpolated logs.

**Rate error, 0.2-5 Hz band-pass (`rms e bp`, rad/s), plant lag behind the model (ms), drive noise:**

| ref model (firmware drive) | f16 roll | f16 pitch | f14 roll | f14 pitch | lag bp f16 r/p, f14 r/p | drive s >5 Hz (f16 p, f14 r) |
|---|---|---|---|---|---|---|
| 0 passthrough (flown) | 0.0372 | 0.0308 | 0.0424 | 0.0333 | 45/35, 55/25 | 0.80, 0.72 |
| 1st, bw 44 (fw) | 0.0348 | 0.0280 | 0.0384 | 0.0314 | 30/15, 40/10 | 0.81, 0.74 |
| 2nd, wn 44 z 0.8 (fw) | 0.0336 | 0.0271 | 0.0361 | 0.0312 | 15/0, 25/-5 | 0.96, 0.93 |
| 2nd, fitted (wn 23-30, z 0.4, d 0-30 ms) | 0.0318 | 0.0260 | 0.0334 | 0.0298 | 0/0, 0/-5 | 0.97, 0.95 |

| yaw (f16 / f14) | rms e bp | lag bp ms | note |
|---|---|---|---|
| 0 passthrough (flown) | 0.1079 / 0.0777 | >=150 / >=150 | lag hits the 150 ms search bound |
| 1st, bw 30 (fw, PROVISIONAL `mrac.c:701-704`) | 0.1000 / 0.0740 | >=150 / >=150 | |
| 1st, bw 2.0 fitted (grid floor) | 0.0339 / 0.0323 | -5 / 15 | -69 % / -58 % |

Findings:
1. **Faster-than-plant signature.** Pitch/roll lag the model by 25-55 ms under passthrough, by 10-40 ms under
   the 1st-order model at 44 rad/s, and by -5..25 ms under the 2nd-order model. Yaw lags every firmware option
   by at least 150 ms. Band-pass correlation of r with x is 0.51 (f16) and 0.13 (f14) on yaw, against 0.85-0.94 on
   pitch/roll. The flown yaw loop is far slower than `ref_model_bw`=30.
2. **In hover the order is a second-order effect on pitch/roll.** The band error changes by 6-21 %. Two
   larger terms dominate. (a) A standing command offset: `mean r` is +0.126 rad/s on roll in both flights and
   +0.047..0.060 on pitch, while `mean x` stays near 0. This is ~3x the band error, and no unity-gain model
   removes it. (b) Vibration: 45-96 % of the drive power lies above 5 Hz.
3. **Authority in shadow mode.** RMS(u_ad)/RMS(u_nom) is 1.47/2.30/1.03 (f16 r/p/y) and 2.33/3.06/1.37 (f14).
   It comes from the bias weight sitting at its projection limit (θ0 = 0.139-0.149 against a limit of 0.15,
   `mrac.c:664,670`) against offset (a). Shadow mode has no feedback to shrink that offset, so this number
   overstates the authority an injected flight would reach. That last point is an inference.
4. **Drive-scale trap (firmware).** Type 1 multiplies the drive by P = 1/(2·bw) = 1/88 (`mrac.c:369`). Type 2
   uses P_e = 1/(2·wn²) = 1/3872 (`mrac.c:453`). Under type 1 at firmware gains the bias weight reaches only
   θ0 0.011-0.030, against 0.139-0.149 under passthrough. Scaling gamma by 88 leaves θ0 unchanged (0.030 both
   ways): the sigma/e-mod leak (`mrac.c:494`) scales with gamma, so the equilibrium θ = grad/σ stays put
   (test `test_drive_norm_restores_bias_learning_that_gamma_scaling_cannot`). The fix is to normalize the
   drive itself to `s = PBe + λ·e_dot` (`--drive-norm`). With that, every order learns the bias like
   passthrough (authority 2.29-2.31, f16 pitch). λ = τ = 1/(2ζwn) pushes the drive's power above 5 Hz from
   0.81 to 0.89 (f16 pitch) and from 0.75 to 0.84 (f14 roll); λ = 0.1τ leaves it at 0.82/0.77.

**Recommendation.**
- **Pitch/roll: 2nd order, wn 44, ζ 0.8, normalized drive, λ = 0.1τ.** Among the firmware bandwidths it has
  the lowest band error (-6..-12 %) and it closes the lag. The fitted wn 23-30 and ζ 0.4 sit at the ζ grid
  floor; keep 44 until the doublet flights pick between 44 and 30 (PROPOSED).
- **Yaw: 1st order with normalized drive.** bw 2 rad/s is the fit at the grid floor, so the true value may be
  lower. Confirm it with yaw steps.
- **Missing log vars** (log plan below): `mrac_state.<ax>.xm`, `.xm_dot` (100 Hz); `ref_model_type` (it had
  to be inferred); `Theta[3..5]` (only 0..2 are logged, at 10 Hz); `mrac_inj.inj_alpha`, `.learn_gate`;
  `u_def` (declared at `mrac.h:253`, never written in `API/ TASK/ BSP/ USER/`); and excitation. Hover gives
  r rms of 0.06-0.09 rad/s, so steps or doublets are needed to separate the orders.

## Q2. Performance recovery

- **Firmware: not implemented.** `ENABLE_PERFORMANCE_RECOVERY` is only a low-pass on u_ad at omega_u
  (`mrac.h:75`, `mrac.c:533-539`). `lambda_perf` and `tau_v` exist but are zero and unused (`mrac.h:196-198`,
  `mrac.c:645-646`). The low-frequency copy `Whatf` exists (`mrac.c:520-523`), but `l1_filtering_on`=0
  (`mrac.c:735`), so it stays at 0, as flight16 showed (`docs/agent/ledger/2026-09-30-full.md:80`).
  `controller.c` has no performance-recovery path (`controller.c:13-30`).
- **Sim: no evidence that it helps.** `MRAC_PR`, the Yucelen-Calise form u_ad = -WᵀΦ - κ(W - W_f)ᵀΦ, exists only
  as the uncommitted `sim/bench/ctrl_p2_yucelen.py` in the main checkout (`.agent-ops/tasks-src/p2-c1-yucelen.md:43`
  said "do not commit"). Its untuned 5-row sanity run (`<main>/.agent-ops/out/p2-c1-yucelen.md:14-20`) gave
  rmse 0.81/0.767/0.932/0.596 against FwPID 0.736/0.794/0.82/0.571, and it **diverged** on steps/motor_loss
  (inf, max|U| 1193), like MRAC_S6 (6565). It was never tuned at equal budget or put on a leaderboard. Set-theoretic
  MRAC_ST did not diverge (0.735/0.771/0.824/0.591/0.352).
- **To fly it:** (1) commit the sim code; (2) tune it at equal budget (P1, `REPORT.md:22-24`) on the tune split,
  then score test and held-out rows; (3) firmware: `kappa_pr` row (default 0), run the `Whatf` filter whenever
  kappa > 0, and add κ·(Θ-Whatf)·Φ to raw_u_ad at `mrac.c:528-531`, about 6 MAC per axis (PROPOSED). Not for
  tomorrow.

## Q3. Three-layer MRAC (operator's idea)

- **Sim design** (`.agent-ops/tasks-src/night-w3-mrac3l.md:10-24`, `sim/bench/ctrl_mrac3l.py`): L1 = physics
  reference model, a simulated nominal cascade (angle PID -> rate PID -> 4-tick delay -> 50 ms lag ->
  integrators, `:85-94`), error s = rate err + λ·angle err (`:133`). L2/L3 = 4-band one-pole bank 0.5/2/5/12 Hz on
  s (reactive) or the preview (predictive) (`:57,96-105`), softmax gate (`:139-146`), Γ(t) = γ·g·A (`:160`),
  norm clamp 3 (`:166-167`), 25 rad/s LPF on u_ad (`:170`).
- **What worked** (`.agent-ops/out/night/REPORT.md`): unrouted 3L (L1 + 6-feature basis), test median
  0.0729 m, -0.0046 [-0.0106, -0.0005] vs pid_tuned2 (l78, l113), ties SatAware; best held-out-family estimate
  -0.0104, CI includes 0 (l78); lag yardstick -2.5 ms (l145); zigzag xtrack 0.040 m (l148).
- **What did not:** routing is worse at equal effort (H6 KILLED, +0.0126..+0.0142 vs unrouted on tune, l169);
  predictive adds nothing beyond reactive (H6b, l170); the reference model misses the plant by 27 % vs a 10 %
  target (l144). The gain over S6 comes from the basis at low γ (inference, no ablation, l136-138).
- **Cost @168 MHz** (`controller_cost.py`, analytic, PROPOSED until measured with `mrac_cyc`, `mrac.h:156-166`):
  L1 only, roll+pitch 2248 cyc/tick, 13.4 µs, 0.27 % of 5 ms, +152 B; both gates 6192 cyc, 36.9 µs, 0.74 %,
  +880 B (8 logf/expf per axis). Today's STRUCT6 x4: 2168 cyc, 0.26 %; MRAC_CCM 1608 B (`ledger/2026-09-30-full.md:60`).
- **Firmware design** (hooks exist): L1 becomes V1 reference-model type 3, "cascade model", after V1 flies
  (PROPOSED). L2/L3 fill the empty `MRAC_L2_Update` / `MRAC_L3_Feedforward` (`mrac.c:290-299`, timed as
  `mrac_cyc.l2_*`) and write the gate into `mrac_g_gamma[ax][grp]` (`mrac.c:283`, used at `:492`), one band per
  feature group, so Γ(t) = γ·g needs no new law code. Switch `MRAC_ENABLE_3L` (default 0); do not fly routing.

## Q4. Structured vs unstructured for loads, dense paths, indoor wind/ground effect

| candidate | sim evidence (`REPORT.md`) | verdict |
|---|---|---|
| S6 structured (today: bias, rate, rate·tanh, cross, u_nom, xm; `mrac.c:252-274`) | 0.099, fails div (l42) | base |
| S10, +sin(angle), \|p\|·u, u\|u\|, acc (`sim_coupled.py:80`) | 0.101 (l43); cog 0.131 vs S6 0.116 (l60) | no |
| **SatAware**, S6 + leak μ\|ΔU\|Θ (`ctrl_mrac_b.py:109-125`) | **0.0726, best**; fails only the dropout cap by 2 % (l30, l180); yaw_imb_hi 0.124 vs pid_tuned2 0.219 (l61) | **fly (V2)** |
| **RBF12**, 4x3 Gaussians on (rate, angle) + u_nom + ref (`sim_coupled.py:40,67-84`) | 0.074 (l32); **cog 0.064, payload 0.052** = best (l59-60); div 7.1 % FAIL (l79) | **fly (V3)**, watch divergence |
| RBF24 | 0.078, fails div/sat (l34) | no |
| L1-style filter (`sim/bench/c_ref/aug_l1.c`) | 0.084, +0.007 vs pid_tuned2 (l37); lowest div 3.4 %; yaw_imb_hi 0.104 (l61) | backup for yaw |
| concurrent / composite learning | +22 %, div 14.7 %, H7 KILLED (l46, l171) | no |
| low-frequency learning (Whatf) / Yucelen PR | untuned, diverged (Q2) | no |
| ground-effect feature (R/4z)² (`phase2_lit_review.md:107,111-115`) | not benched; GE family adaptive ≈ PID, 0.054-0.060 (l67) | later |

In the sim, adaptive control does not help in wind (pid_tuned2 0.073, best, l56). A CoG offset is the bias
feature: the standing roll offset in Q1 is exactly the torque the bias weight targets. A swinging or shifting
payload needs an angle- or rate-dependent feature, which is RBF12's (rate, angle) grid. Dense paths: zigzag
xtrack is sataware 0.039, pid_tuned2 0.042, rbf12 0.054 (l30-33).

## Firmware design for WP-27 (defaults OFF => today's behaviour unchanged)

Every switch goes in `API/mrac_variant.h` as `#ifndef X / #define X 0`. Even when a switch is compiled in, its
runtime defaults reproduce today's outputs. New per-axis floats are `MRAC_SET` rows in struct order and new
per-feature rows are `MRAC_BASIS` (`docs/firmware-table-pattern.md:67-101`), with history below the table.
Prove "OFF = same" with `python API/tests/run_mrac_equiv.py` (`EQUIV OK`, also `--define MRAC_CAPACITY=24`).
Cycle/RAM figures: `controller_cost.py` (PROPOSED); measure with `mrac_cyc.max[ax].total` before injecting.

**V1 `MRAC_ENABLE_REFMODEL_V2`** (est. 2352 cyc/tick, 0.28 %, +192 B)
- `MRAC_AxisConfig_t` rows `ref_type` (-1 = global `mrac_flags.ref_model_type`, today), `ref_delay_s` (0),
  `drive_norm` (0), `lam_edot` (0); `MRAC_AxisState_t` gets `r_buf[8]`, `r_idx`.
- `mrac.c` `MRAC_UpdateAxis` step 1 (`:351-379`) switches on the per-axis type and reads r through the delay
  ring. The drive at `:444-458` becomes, when `drive_norm`, `s = PBe + lam_edot*e_dot` for type 2 and
  `s = PBe` for type 1.
- Flight values (PROPOSED): p/r type 2, wn 44, ζ 0.8, λ 0.0018 s (0.1τ); yaw type 1, bw 2. Log: `xm`, `xm_dot`, `e_dot`, `ref_type`.

**V2 `MRAC_ENABLE_SATAWARE`** (est. 2252 cyc/tick, 0.27 %, +32 B)
- `TASK/StabilizerTask.c:1391-1406` (mixer, after `Controller_Update` at `:1374-1377`): back-project
  commanded-minus-clamped motor values into a per-axis deficit and write `mrac_state.<ax>.u_def`.
- `mrac.c:490-503`: when `mu_sat` > 0, add `- mu_sat*fabsf(u_def)*Theta[i]` inside the `y` bracket (sim form,
  `ctrl_mrac_b.py:120`).
- New `MRAC_SET(mu_sat, 0,0,0,0)`; sim knob 0.850 at γ 0.050 (`REPORT.md:185`), flight value PROPOSED.
  Log: `u_def` 100 Hz, motors, `Throttle_out`.

**V3 `MRAC_VARIANT_STRUCT6_RBF12`** (est. 4800 cyc/tick with roll+pitch RBF, 0.57 %, +672 B)
- `mrac_variant.h`: N_FEATURES 18, CAPACITY 24, N_GROUPS 7 (+`MRAC_GRP_RBF`); `MRAC_Bus_t` (`mrac.h:115`) gets `ang`.
- New row `{MRAC_BLK_RBF, 6, 12, MRAC_GenRBF}` in `mrac_block_table` (`mrac.c:278-280`), separable (4+3 expf).
- New `MRAC_SET` rows `rbf_on` (0 => phi[6..17] = 0, so Phi_sq, Θ and u_ad are exactly STRUCT6), `rbf_rate_scale`
  and `rbf_ang_scale` (PROPOSED 3.0 rad/s and 0.26 rad = the 15° tilt limit, `REPORT.md:11`).
- 12 `MRAC_BASIS` rows per axis (yaw and z rows γ 0). Log: `Theta[6..17]` at 10 Hz.

**Log plan** (campaign capture, under the 80 % link cap, `capture_preset.py:34`): today's 100 Hz MRAC bus plus
`xm`, `xm_dot` (100 Hz); `Theta[0..5]`, `mrac_flags.ref_model_type`, `mrac_inj.inj_alpha`, `.learn_gate`,
`mrac_simplex.fade`, `.tripped` (25 Hz); `mrac_cyc.max[0..3].total` (1 Hz); each variant's own vars above.

## Workflow-B test plan, 2026-10-04 (all PROPOSED)

Fence z <= 1.4, |x| <= 1.3, |y| <= 1.7. Every experiment flies at z 0.8. Steps are scenario `goto` steps; shapes
come from `trajectory_pipeline.SHAPES` (`trajectory_pipeline.py:176-181`), in the format of
`campaigns/example_circle.yaml`. One flash carries V1-V3 compiled in and OFF (supervisor reflash; workers do not
flash). Variants are switched by runtime rows while disarmed.

| exp | steps | metric (per flight) |
|---|---|---|
| H hover | takeoff 0.8, hold 40 s, land | rate e bp rms, mean e, authority, position RMS |
| D doublet | goto x +0.4 / -0.4 / 0 (dwell 3 s), then the same in y | e bp rms, lag x->xm (bp), overshoot |
| F dense | path figure8 width 1.0 height 0.5, v 0.3 m/s, 2 laps | position RMSE, xtrack |

Abort on any flight (`REPORT.md:213-215`): |u_ad| > 0.5|u_nom| for 1 s, tilt > 12°, a motor at the 4000 clamp
for more than 0.5 s, a simplex trip, or position error > 0.5 m.

| battery | campaign | flights (inj = output_injection_on) | pass if |
|---|---|---|---|
| 1 | `pid_ref` | V1 compiled in, inj 0 (shadow): H, D, F | baseline; V1 shadow band e <= flown-type log value (replay: -6..-12 % p/r, -58..-69 % yaw) |
| 2 | `v1_refmodel` | inj 1, γ x0.25 via `mrac_g_gamma` (`mrac.c:283`): H, D; then γ x1: D, F | no abort; F position RMSE <= 1.1 x battery 1 |
| 3 | `v2_sataware` | mu_sat on, inj 1: H, D, F; repeat H with 100 g offset payload | clamp time <= battery 1; RMSE <= 1.1 x battery 1 |
| 4 | `v3_rbf12` | rbf_on, inj 1 at γ x0.25: H, D; with swinging payload: H, F | no Θ-norm growth > 5 s; RMSE <= 1.1 x battery 1 |
| 5 | `pid_ref_end` | repeat battery 1 H, F | drift check against battery 1 |

The order runs from safest to riskiest. V1 must pass before V2 or V3 is injected, because both build on its drive.
