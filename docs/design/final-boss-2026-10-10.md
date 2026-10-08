# Final boss for 2026-10-10: vp row 17 (decided from data, not flown, PROPOSED)

Bottom line: no new firmware variant beat row 17 tonight, so the final boss is **vp row 17 = L2-only composite
law, gamma_c 8, tracking-error learning off on pitch/roll** (already in the firmware, daf790b, host test
test_vp_table_host). It is the only adaptive row that helps on the rope and does no harm on the arm in every test.

| candidate | replay (59 logs) | sim rope 570 g | sim arm 293 g | verdict |
|---|---|---|---|---|
| **row 17** L2only g8 | best of the 3L rows, no positive median cancel | RMSE 0.24 -> 0.08 m (altitude) | neutral, tilt halved on 3/4 mounts | **fly** |
| row 19 L2 g20 + D | first positive median cancel (+0.04) | 0.09 m, more tilt on the drop | worse than PID on 3/4 mounts, tilt 18-32 deg | rope hover only, never the arm |
| row 16 vp6 + L2 g20 | cancel -0.11 / -0.15 | not run | not run | 3rd in the campaign |
| S10X swing row | beats 17 on 32/59, blows up on injected logs | not run | not run | dropped |
| row 17 + omega_u 16 (Q20) | not run | 0.27 m, tilt 27 deg | worse on 4/4 mounts, tilt 26-38 deg | dropped |

Sources: [meta.md](../flights/plots/2026-10-08-meta/meta.md), [d_gamma_sweep.md](../flights/plots/2026-10-09-features/d_gamma_sweep.md),
[d_gain_range.md](../flights/plots/2026-10-09-features/d_gain_range.md), [b_s10x_replay.md](../flights/plots/2026-10-09-features/b_s10x_replay.md),
[sim_rope.md](../flights/plots/2026-10-09-features/sim_rope.md), [sim_arm.md](../flights/plots/2026-10-09-features/sim_arm.md).

## Q20-Q22

| Q | question | answer | evidence |
|---|---|---|---|
| Q20 | omega_u 15-20 rad/s | **no**, keep 4 / 5 | sim_arm.md Q20 table: worse on every case |
| Q21 | saturation freeze + X/Y clamp before the ref model | **not tested tonight**, keep current | sim saturation 16-20% on the rope (PID 16%): watch the motors in flight first |
| Q22 | no leak on the bias rows | **not tested tonight**, keep current | row 17 learns through L2 only; static trim is CG + load (meta.md), check the u_ad mean in the debrief |

## What no variant does yet

The swing. Replay: cancel stays near zero on every row (phase -50..-150 deg). Sim: tilt goes up, not down, with
every adaptive row. The feature work (item E) says the measured swing phase acc_bpq is the strongest feature, but
the basis != 0 path that would use it blows up on injected logs (S10X). Next variant, after tomorrow's flights: fix
that path on the host first (why injected logs diverge), then a swing row with acc_bpq only.
