# 2026-10-08 exp12: 570 g, kp 5 + vp 6, PID vs MRAC by hand (presets refused)

Log `logs/exp12_vp6-570g_traj_presets.slot*.csv`, 63,050 slot-0 rows at 100 Hz, 0 drops. Plots (160 dpi PNG):
`docs/flights/plots/2026-10-08-exp12/`. Numbers are measured from the log; readings and next steps are PROPOSED.

| item | value |
|---|---|
| presets | `kp_active` 5 (mask 0x0F) the whole log; `vp_active` 6 in every flight |
| vp 7 / vp 8 | tried on the ground at 229 s and 286 s: `vp_active` 238 = 0xEE (default build has no basis > 0), back to 6 before take-off |
| trajectory presets | `traj_status` 0xE2 (bad `traj_id` at `traj_go`), not logged; all moves were by hand |
| flights | F1 16-59 s PID, F2 123-198 s MRAC, F3 344-386 s PID then 386-402 s MRAC, F4 443-498 s MRAC, F5 539-562 s PID then 562-608 s MRAC |

## PID vs MRAC (segment edges trimmed 1 s; "still" = 2 s windows with x/y setpoints fixed within 2 cm)

| segment | still (s) | pitch / roll sd still (deg) | z - z_des still (m) | u_ad / u_nom RMS pitch, roll | motor2 at 4000 (%) |
|---|---|---|---|---|---|
| F1 PID | 34.5 | 1.13 / 1.23 | -0.099 | 0 (not injected) | 0.1 |
| F2 MRAC | 50.2 | 1.67 / 1.73 | -0.008 | 0.78, 0.54 | 2.0 |
| F3 PID | 30.4 | 2.02 / 1.86 | -0.147 | 0 | 1.0 |
| F3 MRAC | 9.7 | 1.01 / 1.55 | +0.003 | 0.16, 0.14 | 1.5 |
| F4 MRAC | 39.2 | 1.70 / 1.60 | +0.013 | 0.86, 0.48 | 3.6 |
| F5 PID | 13.4 | 1.44 / 1.58 | +0.016 | 0 | 1.5 |
| F5 MRAC | 30.5 | 1.49 / 1.53 | -0.027 | 0.19, 0.19 | 6.5 |

MRAC on from take-off (F2, F4): pitch u_ad/u_nom grows over the flight (F2 thirds 0.64, 0.79, 0.90; |Theta[0]|
0.042 to 0.057). MRAC switched on mid-flight (F3, F5): 0.14-0.21. u_ad against u_nom r = -0.14 to +0.06 and against
the body rate r = -0.08 to +0.19 (no damping, no anti-damping). Max |u_ad| 0.092 pitch, 0.062 roll; max |u_nom| 0.26.
Dominant sway 0.25-0.9 Hz in both modes (load pendulum band).

## Reading (PROPOSED)

1. **Swing.** When MRAC runs from take-off, its pitch/roll output grows to 0.5-0.9 of the PID's own output but is
   uncorrelated with the PID and with the body rate, so it adds torque that does not oppose the swing: still-hover sd
   1.6-1.7 deg vs 1.13-1.23 in F1 PID. Not consistent though: F3 PID was the worst (2.02 / 1.86), and F5 PID vs
   MRAC (small u_ad) is equal (1.44 / 1.58 vs 1.49 / 1.53). The extra wobble follows the size of u_ad, not MRAC itself.
2. **Height.** Same as exp11: PID sinks 0.10-0.15 m (F1, F3), MRAC holds z within 0.03 m. This is the clear MRAC win.
3. **Authority.** PID alone is not short of authority for attitude (max |u_nom| 0.26; motor2 at its cap only
   0.1-1.5 % of the PID time). Full-flight MRAC adds a pitch/roll command about as large as the PID's (RMS ratio up to
   0.86), which is not a small trim; MRAC segments also spend more time at the motor2 cap (2.0-6.5 % vs 0.1-1.5 %,
   with battery sag later in the log mixed in).
4. **Next.** Fly MRAC on z only with PID p/r (z win kept, p/r swing removed) or lower the p/r adaptation gain
   (SIL: stable at gamma 0.25-0.5); keep the load at 500 g so motor2 has headroom.
