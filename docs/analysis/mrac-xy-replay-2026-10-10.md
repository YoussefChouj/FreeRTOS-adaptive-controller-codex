# x/y MRAC log replay (2026-10-10)

Purpose: test the new x/y velocity layers (commit 7d83471, off at boot, unflown) on the PC against old flight logs
before any x/y flight. Tool: `python -m ground_station.analysis.mrac_xy_replay [--md out.md] [--ref-bw BW]`
(host build of `API/mrac*.c` around `ground_station/analysis/mrac_xy_replay_host.c`).

Method: open loop. Every log with `Ctrler.locx/ysPID.Des/FB/U` is resampled to 5 ms and fed to `MRAC_Control`
with x/y learning and injection on from power-on. The plant does not respond (the logs are PID-only flights), so
this shows the error the law would see and how large u_ad and Theta would grow, not how the closed loop changes.
Persistent errors are overstated: in closed loop u_ad would shrink them.

## Bottom line

| check | limit | passthrough reference (boot default) | verdict |
|---|---|---|---|
| finite outputs | all | all finite, 45 logs, 3978 s gated | pass |
| abs u_ad | u_max 1.0 m/s^2 | never above 1.00 (clamp holds) | pass |
| abs e p99 | e_sat 0.4 m/s | above on 24 of 90 axis-runs, worst 1.23 | tanh saturation often active |
| abs e > e_freeze | 1.2 m/s | 8 of 90 axis-runs, at most 1.1 % of time | rare hard freeze (u_ad set to 0) |
| time at u_max | - | 18 of 90 axis-runs, up to 37 % (x axis, drift logs) | open-loop artefact, see below |

The law is numerically safe on all 45 logs. The error it learns from is too large with the boot passthrough
reference model, because e = FB - Des then includes the PID velocity loop's normal lag.

## Figure-8 logs (Des/FB only, no U: e only, computed in Python)

With a passthrough reference model, e = FB - Des exactly, so the 5 figure-8 stream logs (exp12, vp13; they have
no `U`, so the C replay skips them) can be checked directly. 50 Hz grid, gated on flight_phase 1/2.
A first-order lag fit of FB to Des gives bandwidth 2 rad/s on 9 of 10 axis-runs (3 rad/s on vp13_gap).

| log | ax | abs r p99 m/s | passthrough e p99 / > e_sat | first-order bw 4: e p99 / > e_sat | best-fit bw: e p99 / > e_sat |
|---|---|---|---|---|---|
| exp12_vp6-570g_traj_presets | x | 0.54 | 0.45 / 1.4 % | 0.31 / 0.4 % | 0.30 / 0.0 % |
| exp12_vp6-570g_traj_presets | y | 0.63 | 0.49 / 2.0 % | 0.38 / 0.9 % | 0.37 / 0.8 % |
| vp13_g4 | x | 0.87 | 0.76 / 4.6 % | 0.49 / 1.4 % | 0.37 / 0.7 % |
| vp13_g4 | y | 0.85 | 0.74 / 6.4 % | 0.52 / 3.4 % | 0.45 / 2.0 % |
| vp13_g4_arm_load_293g_1 | x | 0.87 | 0.89 / 9.1 % | 0.60 / 4.2 % | 0.47 / 3.2 % |
| vp13_g4_arm_load_293g_1 | y | 0.85 | 0.94 / 11.3 % | 0.62 / 6.7 % | 0.53 / 3.9 % |
| vp13_g4_last | x | 0.87 | 1.03 / 14.2 % | 0.55 / 5.7 % | 0.47 / 3.0 % |
| vp13_g4_last | y | 0.83 | 0.75 / 6.0 % | 0.47 / 2.4 % | 0.40 / 1.0 % |
| vp13_gap | x | 0.73 | 0.57 / 3.1 % | 0.30 / 0.2 % | 0.24 / 0.0 % |
| vp13_gap | y | 0.81 | 0.75 / 4.8 % | 0.43 / 1.3 % | 0.38 / 0.7 % |

Time above e_freeze 1.2: at most 0.3 % with passthrough, 0.0 % with the first-order model at bw 4.
Today's matrix runs laps 3 and 4 at about 104 and 138 cm/s (computed), above the 0.87 m/s p99 in these logs,
so the passthrough error will be larger still.

## Time at u_max (open-loop artefact)

The runs with u_ad pinned at 1.0 are x-axis hover/landing logs with small error (p99 0.18 to 0.48 m/s) but a
steady one-sided bias (`flight_test_landing_x_drift_1` 37 %, `flight_test_drift_fix_1` 29 %,
`f17_hover_shadow2` 28 %). Open loop, the bias never closes, so Theta integrates it up to the clamp. This is the
layer trying to cancel the known x drift; in closed loop the error would shrink. The clamp held in every case.

## PROPOSED (needs operator OK, config change)

Run x/y with a first-order reference model instead of passthrough: `ref_type = 1` on mrac_config_x/y only
(per-axis V1 field, other axes unchanged), `ref_model_bw = 2` rad/s (best fit above, replacing the unmeasured 4).
Then e measures departure from the velocity loop's own response, not its lag. Result of the full C replay at
bw 2 on the 45 logs: see the next section.

## C replay with first-order reference model, bw 2 (`--ref-bw 2`)

Same 45 logs, 90 axis-runs, everything else unchanged.

| measure | passthrough | first-order bw 2 |
|---|---|---|
| axis-runs with e p99 above e_sat 0.4 | 24 | 18 |
| worst e p99 m/s | 1.23 | 1.03 |
| axis-runs with any time above e_freeze 1.2 | 8 (max 1.1 %) | 0 |
| worst time above e_sat | 19.7 % | 16.6 % |
| max abs u_ad m/s^2 | 1.00 | 0.80 |
| axis-runs with time at u_max | 18 (max 37.4 %) | 0 |
| all outputs finite | yes | yes |

The first-order model removes hard freezes and clamp time on every log. Tanh saturation is still often active
on the aggressive roaming logs (u_nom p99 about 3 m/s^2); on the figure-8 logs it drops to 0 to 3.9 % of time.

## Appendix: full C replay table, passthrough

See `--md` output of the tool; 45 logs, x and y rows, columns: gated s, abs e p50/p99/max, time above e_sat and
e_freeze, max abs u_ad, time at u_max, u_nom p99, Theta norm max/end.
