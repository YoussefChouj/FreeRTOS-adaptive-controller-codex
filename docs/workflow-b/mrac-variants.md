# MRAC variants (WP-27): what each does, how to switch it, how to fly it

Spec: `docs/analysis/controller-roadmap-2026-10-03.md` (Q1-Q4, test plan). Every variant is compiled in
(`API/mrac_variant.h`) and **OFF at power-on**; OFF reproduces today's law bit for bit
(`python API/tests/run_mrac_equiv.py` -> EQUIV OK). Numbers below are PROPOSED unless the roadmap measured them.

## Switching: CMD 0x1D MRAC_VARIANT
`idx = field << 2 | axis` (axis 0 pitch, 1 roll, 2 yaw, 3 z), value float32. Refused while airborne
(FLYING/LANDING) and when out of range or non-finite (`MRAC_VariantParamSet`, `API/mrac.c`). Fields:
0 ref_type, 1 ref_delay_s, 2 drive_norm, 3 lam_edot, 4 kappa_pr, 5 crm_ell, 6 mu_sat, 7 lam_ang, 8 rbf_on,
9 rbf_rate_scale, 10 rbf_ang_scale, 11 gamma_scale (all of `mrac_g_gamma[axis][*]`), 12 ref_model_bw;
WP-33: 13 st_eps, 14 st_phi_max, 15 st_bar, 16 lf_gain, 17 sigma_lf, 18 gam_f (17-18 write the existing rows).
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
| PR | `u_ad += kappa_pr (Theta - Whatf)'Phi` (Yucelen-Calise) and closed-loop ref model `xm += DT crm_ell (x - xm)` (WP-29 E5). WP-33 fix: Whatf now filters every tick while kappa_pr > 0, as the sim does (`ctrl_p2_yucelen.py:197`); before, it froze in the deadzone and left kappa (Theta - Whatf) as an offset | `mrac_pr.yaml`, see "WP-33 limits" | u_max clamp, crm_ell DT <= 0.25 |
| ST | set-theoretic (Arabi, Gruenwald, Yucelen, Nguyen 2018; notebook P2 c6): gradient times `k_st = eps (eps - |e|/2)/(eps - |e|)^2` (1 at e = 0, cap st_phi_max), plus `st_bar` log-barrier drive above 0.75 eps. The sim (`ctrl_p2_yucelen.py:20-24`) used the unnormalised 1/(eps^2 - s^2) | `mrac_st.yaml` | projection, u_max clamp |
| LFHG | low-frequency learning (Yucelen-Calise): per axis `lf_gain > 0` turns on the `sigma_lf (Theta - Whatf)` pull (the global `l1_filtering_on` stays 0) and multiplies gamma by lf_gain; gam_f = Whatf bandwidth | `mrac_lfhg.yaml` | projection, u_max clamp; DT gamma gamma_scale lf_gain sigma_lf <= 1 |
| 3L layer 1 | drive `s += lam_ang e_int`, e_int = 1 s leaky integral of e, clipped to e_sat (sim ctrl_mrac3l.py:133). L2/L3 band gating not built: routing lost in sim (H6) | lam_ang: sim default 4, tuned 2.2 | e_sat clip, u_max clamp |
| V3 RBF12 | 4x3 Gaussians on (rate/3.0, angle/0.26) for pitch/roll, features 6..17, symmetric bounds +-0.05 | rbf_on 1, gamma_scale 0.25 | projection, u_max clamp |

V3 needs a build with `-DMRAC_VARIANT=1` (or `MRAC_VARIANT_STRUCT6_RBF12` in `API/mrac_variant.h`); the default
build is STRUCT6 and ignores rbf_on. Any variant on: a non-finite drive or output zeroes that axis' u_ad and
weights (`mrac_var_id[ax] != 0` only). `u_ad` also passes the simplex fade and injection ramp as today.

## Logs
`mrac_shadow` log group (already in every campaign) carries u_ad, u_nom, u_def, e, e_dot, Theta/Whatf[0..5].
xm = x - e with x = `Ctrler.gyro*PID.FB`/57.3 (core set). Variant state by symbol (subscribe or livewatch):
`mrac_var_id[0..3]` (uint16 since WP-33; bits: 1 ref_type, 2 drive_norm, 4 delay, 8 kappa, 16 crm, 32 mu_sat,
64 lam_ang, 128 rbf, 256 st, 512 st barrier active this tick, 1024 lfhg), `mrac_ref_type_eff[0..3]`, `mrac_config_<ax>.<field>`, `mrac_state.<ax>.e_int`, `Theta[6..17]`.
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
| 6 (WP-33) | `pr_refmodel` | 1, p/r gamma x0.25 | H, D, F | no abort; F RMSE <= 1.1 x battery 1 |
| 7 (WP-33) | `st_mrac` | 1, p/r gamma x0.25 | H, D, F | same |
| 8 (WP-33) | `lfhg_mrac` | 1, p/r lf_gain 0.25 | H, D, F | same; least SIL margin, fly last |

V1 must pass before V2, V3 or a WP-33 variant is injected. Campaign aborts: tilt 12 deg, position error 0.5 m, saturation window
0.5 s. Watch by hand (no AbortLimits field): |u_ad| > 0.5 |u_nom| for 1 s, a simplex trip.
Before injecting, read `mrac_cyc.max[0..3].total` to measure the cycle cost (estimates in the WP-27 report).

## WP-33: SIL limits, flight presets, cost
Source: `docs/analysis/sil-limits-2026-10-04.md` (`python -m sim.sil.limits`; 3 seeds, majority vote; SIL only,
unvalidated). Setup: doublet D, V1 drive on pitch/roll, **only pitch/roll injected** (`g_ctrl_axis_mask` 0x03),
plant motor clamp 2000-4000, MOTOR_TAU 50.5 ms, 15 ms base delay; extra delay +0/+5/+10 ms, noise x2. A run fails on
an abort pid does not also trip (pid itself tilts > 12 deg on D from +5 ms). G = gamma multiplier on pitch/roll.

| variant | stable G (+0 ms) | flight preset | gain margin | delay margin | noise x2 | first failure at the edge |
|---|---|---|---|---|---|---|
| V1 (ref) | 0.25-0.5 | gamma x0.25 | x4 | +0 ms (hard-freeze relay) | pass | T at G 1: tilt 13.4 vs pid 9.0 deg |
| PR | 0.25-0.5 | gamma x0.25, kappa_pr 0.5, crm_ell 10 | x4 | +10 ms | pass | T at G 1 (13.2 deg); G 2-8: P, error > 0.5 m under delay |
| ST | 0.25-0.5 | gamma x0.25, st_eps 2.0, st_phi_max 10, st_bar 0 | x4 | +10 ms | pass | T at G 1 (14.5 deg) |
| LFHG | 0.25-0.5 | lf_gain 0.25 (gamma_scale 1), sigma_lf 0.8, gam_f 16 | x4 | +0 ms (hard-freeze relay) | fail (T, 2/3 seeds) | T at G 1 (13.1 deg) |

- No variant diverges up to G 128 (projection bounds the weights); every gain edge is the 12 deg tilt abort: the
  injected rate correction steepens the doublet's attitude transient.
- PR knobs at G 0.25: kappa_pr 0.25-2, crm_ell 2-50, ref_model_bw 5-80 all pass. crm_ell keeps |e| below e_freeze,
  which is why PR (and ST eps 2) keep the +10 ms delay margin the others lose.
- ST: eps 1.0 has edge G 0.5 (k_st saturates in doublets); eps < 1 fails at G 0.25; ref_model_bw < 44 fails (wn 5:
  RMSE 49 cm). st_bar 0.2 moves the +0 ms edge to G 8 but fails +5 ms at every G and injects 2-6x V1's high-frequency
  content: not a flight setting.
- LFHG: LF learning does not move the gain edge (G 1, as V1) and trims the high-frequency content of u_ad by 6-23 %
  at G >= 4. The SIL gives no case for a high gain; the preset is the V1 gain with the LF pull on.
- **Hard-freeze relay (firmware, all variants):** at +5 ms, |e| crosses e_freeze 1.2 rad/s, the freeze zeroes u_ad in
  one tick and the u_ad low-pass ramps it back, about 4 Hz. It is the only +5 ms failure of V1, V2 and LFHG at G 0.25
  (0/3 seeds fail with `hard_freeze_on` 0). PROPOSED: hold u_ad instead of zeroing it, or fade it, as a default-OFF
  variant; not built here (flight behaviour unchanged).
- Flight setup (PROPOSED): the descriptors set pitch/roll only (14-knob descriptor limit), so yaw keeps its firmware
  law. To fly as tested, write `g_ctrl_axis_mask` = 3 by probe before the campaign and 15 after (WP-31: injected yaw
  and z trip abort U by design). With all axes injected, see the matrix rows (`docs/analysis/sil-matrix-2026-10-04.md`).

Cost (WP-33). RAM, measured with sizeof on the host build (`API/tests/mrac_sizeof.c`): MRAC_AxisConfig_t 216 -> 232 B,
+64 B CCM over 4 axes (ST 48, LFHG 16; PR's kappa_pr/crm_ell 32 B are WP-27's); mrac_var_id 4 -> 8 B (uint16);
MRAC_AxisState_t unchanged (156 B). Ops per axis per rate-loop step when ON (n = 6 features), cycles PROPOSED for the
M4F (add/mul 1, VDIV 14): ST 11 FP ops incl. 1 divide (~35 cyc), barrier +11 incl. 2 divides (~45 cyc); LFHG 6 x 6 = 36
(y *= lf_gain + the Whatf update; the sigma_lf term is computed today even when OFF), ~45 cyc; PR 50 (6 x 4 for
kappa (Theta - Whatf)'Phi + 6 x 4 for Whatf), ~60 cyc. OFF: 3 compares per axis. Measure with `mrac_cyc.max[ax].total`.
`ENABLE_LYAPUNOV_BARRIER` (`API/mrac.h:79`) is read by no code; the barrier is ST's st_bar.
