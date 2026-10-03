# MRAC variants (WP-27): what each does, how to switch it, how to fly it

Spec: `docs/analysis/controller-roadmap-2026-10-03.md` (Q1-Q4, test plan). Every variant is compiled in
(`API/mrac_variant.h`) and **OFF at power-on**; OFF reproduces today's law bit for bit
(`python API/tests/run_mrac_equiv.py` -> EQUIV OK). Numbers below are PROPOSED unless the roadmap measured them.

## Switching: CMD 0x1D MRAC_VARIANT
`idx = field << 2 | axis` (axis 0 pitch, 1 roll, 2 yaw, 3 z), value float32. Refused while airborne
(FLYING/LANDING) and when out of range or non-finite (`MRAC_VariantParamSet`, `API/mrac.c`). Fields:
0 ref_type, 1 ref_delay_s, 2 drive_norm, 3 lam_edot, 4 kappa_pr, 5 crm_ell, 6 mu_sat, 7 lam_ang, 8 rbf_on,
9 rbf_rate_scale, 10 rbf_ang_scale, 11 gamma_scale (all of `mrac_g_gamma[axis][*]`), 12 ref_model_bw.
Ranges: `ground_station/analysis/mrac_variants.py` (a host test keeps it equal to the firmware table).
Ground station: descriptors `ground_station/analysis/controllers/mrac_v1|v2|v3.yaml`; each has a preset per
campaign plus `restore` (= every knob's firmware default). `campaign_presets(<campaign yaml>)` returns
(start, restore); send start with `apply_params` before the first go and restore after the last landing. The
campaign runner does not send them itself (it applies tuner proposals only; adding a hook needs
`ground_station/service/campaign_runner.py`, outside WP-27).

## Variants
| id | what it does | knobs (flight value) | bounded by |
|---|---|---|---|
| V1 ref model v2 | per-axis reference type, optional command delay, normalized drive `s = PBe (+ lam_edot e_dot)`; removes the 88-3900x drive shrink of types 1/2 (roadmap Q1 finding 4) | p/r ref_type 2, drive_norm 1, lam_edot 0.0018; yaw ref_type 1, drive_norm 1, ref_model_bw 2 | u_max clamp |
| V2 sat-aware | leak `mu_sat |u_def| Theta` inside the gamma bracket; `u_def` = mixer clip of the last tick projected per axis (`API/controller.c`, Keil build only) | mu_sat 0.85 p/r/y (sim knob, units differ) | leak only pulls Theta to 0 |
| PR | `u_ad += kappa_pr (Theta - Whatf)'Phi` (Yucelen-Calise) and closed-loop ref model `xm += DT crm_ell (x - xm)` (WP-29 E5) | kappa_pr, crm_ell: no flight value; sim diverged untuned (roadmap Q2) | u_max clamp, crm_ell DT <= 0.25 |
| 3L layer 1 | drive `s += lam_ang e_int`, e_int = 1 s leaky integral of e, clipped to e_sat (sim ctrl_mrac3l.py:133). L2/L3 band gating not built: routing lost in sim (H6) | lam_ang: sim default 4, tuned 2.2 | e_sat clip, u_max clamp |
| V3 RBF12 | 4x3 Gaussians on (rate/3.0, angle/0.26) for pitch/roll, features 6..17, symmetric bounds +-0.05 | rbf_on 1, gamma_scale 0.25 | projection, u_max clamp |

V3 needs a build with `-DMRAC_VARIANT=1` (or `MRAC_VARIANT_STRUCT6_RBF12` in `API/mrac_variant.h`); the default
build is STRUCT6 and ignores rbf_on. Any variant on: a non-finite drive or output zeroes that axis' u_ad and
weights (`mrac_var_id[ax] != 0` only). `u_ad` also passes the simplex fade and injection ramp as today.

## Logs
`mrac_shadow` log group (already in every campaign) carries u_ad, u_nom, u_def, e, e_dot, Theta/Whatf[0..5].
xm = x - e with x = `Ctrler.gyro*PID.FB`/57.3 (core set). Variant state by symbol (subscribe or livewatch):
`mrac_var_id[0..3]` (bits: 1 ref_type, 2 drive_norm, 4 delay, 8 kappa, 16 crm, 32 mu_sat, 64 lam_ang,
128 rbf), `mrac_ref_type_eff[0..3]`, `mrac_config_<ax>.<field>`, `mrac_state.<ax>.e_int`, `Theta[6..17]`.
A named log group for these needs `ground_station/livewatch/campaign_capture.py` (outside WP-27).

## Flight order (roadmap test plan; z 0.8, fence z <= 1.4, |x| <= 1.3, |y| <= 1.7)
H = hover 40 s; D = goto x +-0.4, y +-0.4, dwell 3 s; F = figure-8 1.0 x 0.5 m, 0.3 m/s, 2 laps.
| battery | campaign (`ground_station/service/campaigns/`) | inj | flights | pass if |
|---|---|---|---|---|
| 1 | `pid_ref` | 0 (V1 shadow) | H, D, F | V1 band e <= passthrough replay (-6..-12 % p/r, -58..-69 % yaw) |
| 2a | `v1_refmodel` | 1, gamma x0.25 | H, D | no abort |
| 2b | `v1_refmodel_g1` | 1, gamma x1 | D, F | no abort; F RMSE <= 1.1 x battery 1 |
| 3 | `v2_sataware` | 1 | H, D, F, H + 100 g offset | clamp time <= battery 1; RMSE <= 1.1 x |
| 4 | `v3_rbf12` (RBF build) | 1, gamma x0.25 | H, D, H + swing, F + swing | no Theta-norm growth > 5 s; RMSE <= 1.1 x |
| 5 | `pid_ref_end` | 0 | H, F | drift vs battery 1 |

V1 must pass before V2 or V3 is injected. Campaign aborts: tilt 12 deg, position error 0.5 m, saturation window
0.5 s. Watch by hand (no AbortLimits field): |u_ad| > 0.5 |u_nom| for 1 s, a simplex trip.
Before injecting, read `mrac_cyc.max[0..3].total` to measure the cycle cost (estimates in the WP-27 report).
