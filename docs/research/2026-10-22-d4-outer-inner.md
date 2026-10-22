# D4: xy MRAC in the inner loop, the outer loop, or both (sim, 2026-10-22)

**Bottom line (sim only).** A fast velocity-layer MRAC plus a slow position-layer MRAC (gamma 5 inner, 1 outer)
gives the lowest error in every scenario: 16 % below inner-only on the hold, 20 % below with a 15 % thrust
loss, 11 % below on the sine. With both layers at the same fast gain (5 / 5) they fight: the error is worse than
inner-only and the command rms rises by about 30 %. The outer layer alone hurts the hold. So the time-scale split
is what makes two layers work, which supports the thesis claim of a configurable multi-time-scale MRAC.
These are sim results: the firmware has no position-layer xy MRAC (`API/mrac.c` adapts velocity only), so the
outer layer here is a design study.

Analogy: two people steering one boat. One makes quick small corrections and the other slowly trims the course.
If both make quick corrections, they over-steer each other.

## Setup

Tool: `tools/d4_mrac_outer_inner_sim.py`. It reuses the D3 plant (`tools/d3_mrac_wrap_sim.py`):

- one axis at 100 Hz;
- a 21-tick loop delay;
- 0.9 cm position noise and 2 cm/s velocity noise;
- the PID cascade gains from `API/pid.c`.

Both layers use the same reference: a noise-free twin running the same PID on the header plant.

| layer | regressor (SI) | output | limit | error |
|---|---|---|---|---|
| inner (as in firmware) | 1, v, v tanh v, u_nom | added to the acceleration command | 1 m/s^2 | v - twin v |
| outer (design study) | 1, p, vd | added to the velocity demand on the next tick | 0.3 m/s | p - twin p |

Both laws are W' = g phi e - 0.1 g |e| W, with each weight limited to |W| <= 2. The firmware gamma was not read, so
gamma is swept. Each case is run on 3 noise seeds; the table shows the mean, with the spread (max - min) in
brackets. Scoring starts at 3 s.

## Results: rms position error (cm)

| layer | g inner | g outer | step + wind | step + wind, thrust x0.85 | sine 0.25 Hz + wind |
|---|---|---|---|---|---|
| none | - | - | 12.12 (0.31) | 18.66 (0.29) | 27.79 (0.42) |
| inner | 1 | - | 7.60 (0.35) | 9.71 (0.36) | 21.91 (0.41) |
| inner | 5 | - | 5.85 (0.46) | 6.22 (0.46) | 17.56 (0.42) |
| outer | - | 1 | 13.90 (0.51) | 15.97 (0.42) | 17.83 (0.90) |
| outer | - | 5 | 22.07 (3.01) | 23.84 (0.86) | 28.32 (0.52) |
| both | 1 | 1 | 6.94 (0.13) | 7.82 (0.29) | 16.29 (0.57) |
| both | 5 | 5 | 13.51 (0.98) | 13.83 (0.20) | 19.55 (0.62) |
| **both** | **5** | **1** | **4.93 (0.09)** | **4.96 (0.04)** | **15.60 (0.09)** |
| both | 1 | 0.2 | 5.90 (0.10) | 6.09 (0.10) | 16.53 (0.12) |

Command rms on step + wind, in cm/s^2:

| none | inner 5 | both 5 / 1 | both 5 / 5 |
|---|---|---|---|
| 105.2 | 131.1 | 129.8 | 169.2 |

Full output, including max error and the rms of both adaptive signals: run the tool.

## What it means

1. **The split is the mechanism.**
   - At 5 / 5 the inner adaptive signal reaches 0.78 m/s^2 rms, against 0.43 m/s^2 inner-only. The two loops chase each other's
     correction through the 21-tick delay.
   - At 5 / 1 the inner signal is back to 0.39 m/s^2, and the outer signal stays small (0.03 m/s rms on the hold).
2. **The outer layer alone is a tracking aid, not a hold aid.**
   - On the sine at gamma 1 it cuts the error from 27.8 to 17.8 cm.
   - On the hold it adds error (13.9 against 12.1).
   - At gamma 5 it makes every scenario worse.
3. **The gains are robust.** Both split cases (5 / 1 and 1 / 0.2) beat inner-only at the same inner gain on all
   three scenarios. The seed spread of the winner (0.04 to 0.09 cm) is far below its gap to inner 5 (0.9 to 2.0 cm).

## Limits (read before quoting)

- **Plant model:** the sim is the header plant from sysid D7. Its fit-quality limits carry over.
- **Gamma:** not the firmware value. The ranking holds across the swept range only.
- **Outer reference:** it uses the twin's position, which is the nominal closed loop. Another reference model would need a
  rerun.
- **No flight data:** a position-layer MRAC needs firmware work and an A/B flight before it can go into the thesis as
  a measured result.
