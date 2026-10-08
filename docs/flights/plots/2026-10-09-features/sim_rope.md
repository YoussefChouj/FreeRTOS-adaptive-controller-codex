# Item 6: closed-loop rope sim, PID vs 3L-v2 rows 17 and 19 (2026-10-09)

Sim only, 5 seeds, hover 20 s, metrics from 3 s. Every number here is a SIM result: PROPOSED until it is flown.

```
cd sim/bench
python demo_loads.py --rope --seeds 5 --out results/demo_rope.json
```

## Setup

| item | value | source |
|---|---|---|
| load | 0.57 kg on a 0.43 m rope (33 cm rope + half bottle 10 cm) | measured |
| attach point | x +0.015 m (measured 1-2 cm), z -0.03 m | z PROPOSED |
| rope | tension-only spring k 2000 N/m, c 5 N s/m, initial swing 0.1 rad | PROPOSED |
| drop case | rope cut at t = 10 s (`sling['t_rel']`, plant.py) | sim |
| controllers | flashed PID (pid_tuned2+f1x); the same PID plus firmware API/mrac*.c rows 17 / 19 (ctrl_fwmrac, inj 1) | row args = the replay winners |

## Result

| case | metric | PID | + row 17 | + row 19 |
|---|---|---|---|---|
| rope570 | median RMSE [m] | 0.241 | **0.076** | 0.088 |
| rope570 | median rmse_z [m] | 0.218 | 0.029 | 0.029 |
| rope570 | max tilt [deg] / mean sat | 19.3 / 0.16 | 21.8 / 0.20 | 20.4 / 0.20 |
| rope570_drop | median RMSE [m] | **0.083** | 0.086 | 0.096 |
| rope570_drop | max tilt [deg] / mean sat | 14.4 / 0.08 | 14.6 / 0.10 | 20.4 / 0.11 |

Paired difference against PID, with the 95% bootstrap CI:

| case | row 17 | row 19 |
|---|---|---|
| rope570 | -0.165 [-0.380, +0.014] | -0.153 [-0.327, +0.012] |
| rope570_drop | +0.003 [-0.187, +0.004] | +0.013 [-0.140, +0.015] |

## Reading

- On the hanging rope, both rows cut the RMSE by about 3x. Nearly all of the gain is **altitude**: PID sags (rmse_z
  0.22 m), the MRAC z channel holds height (0.03 m). The swing (tilt) is not damped: max tilt is 1-2 deg *higher*
  with MRAC. This matches the replay (meta.md, d_gain_range.md): no variant cancels the swing yet.
- Load drop: no gain from either row. Row 19 (D gain) peaks at 20 deg tilt against 14 for PID. This is the same
  weakness the replay showed on the f17 load-removal logs. Row 19 stays hover-only and is flown after row 17.
- Motors saturate 16-20% of the time with the rope in the sim. The sim thrust margin and the rope k/c are not
  measured, so treat this as a flag to watch on the real motors, not as a fact.
- 5 seeds: every CI crosses zero. This is a direction, not proof.

## Consequence for the morning

The flight order stays 17 -> 19 -> 16. Watch altitude hold (MRAC should beat PID), max tilt, and motor saturation.
Nothing here says row 19 beats row 17.
