# 2026-10-08 morning brief: load-swing fixes, Keil presets, manual-mode checklist

Branch `overnight-2026-10-08`. Built (0 errors, 0 warnings), **not flashed**. Every number below is from the bench
(sim/bench, firmware-matched gains, 0.5 kg sling, L 0.43 m) or from the 10-07 logs; nothing here has flown.
Details and tables: `docs/flights/2026-10-07-load-swing-analysis.md`.

## Bottom line (PROPOSED)

| problem in flight | bench cause | firmware answer |
|---|---|---|
| MRAC: swing grows until landing | of1 (raw optical flow, no gyro fix) feeds body rotation into velocity | presets 1-3: of1 off |
| PID: sinks until the bottle rests on the floor | Z_ratePID clamps (UMax 300, UiMax 100) both bind | presets 5-6: 500 / 300 / 700 |

## Keil presets (watch window, disarmed only)

Set `kp_id`, then `kp_go = 1`; `kp_active` shows the applied preset. A reboot returns to preset 0.

| kp_id | of1 in EKF | MRAC axes | p/r gamma | Z_ratePID clamps |
|---|---|---|---|---|
| 0 | on | all (0x0F) | x1 | boot (flown 10-07) |
| 1 | off | all | x1 | boot |
| 2 | off | all | x0.1 | boot |
| 3 | off | z only (0x08) | x1 | boot |
| 4 | on | z only | x1 | boot |
| 5 | off | all | x1 | 500 / 300 / 700 |
| 6 | off | z only | x1 | 500 / 300 / 700 |

## Lab order (manual mode, no campaigns)

1. Flash: `python -m ground_station.flashtool.rebuild_and_flash --yes` on branch `overnight-2026-10-08`. Stop on any
   non-zero exit.
2. Disarmed: preset 1, check `kp_active = 1`. Fly the bottle by hand, WiFi log as on 10-07 (Ctrl+C now ends a
   group capture cleanly, bd736b1).
3. Swing gone: try preset 3, then 5 (PID vs MRAC without the sink), then 6.
4. Swing still there with of1 off: the leak is not the driver. Go back to preset 0 and stop tuning.
5. Fallback at any point: reboot (preset 0), or flash tag `fw-flown-2026-10-07` /
   `OBJ/archive/JX_FLY_834c8564.axf`.

## Code review findings

| # | finding | evidence | action |
|---|---|---|---|
| 1 | of1 rotation leak into the velocity loop | bench: leak k -0.6 reproduces the growing swing; of1 off removes it | presets 1-3; a gyro-compensated of1 is the real fix (future) |
| 2 | Z_ratePID headroom too small for +0.5 kg | bench: 0.48 m flown clamps vs 1.01 m with 500 / 300 / 700 | presets 5-6 |
| 3 | RPM ch2 (PC6) reads 0.31-0.39 of the other channels' mean in every flight; ch0 (PA0) degraded through the day, median 5435 / 4436 / 4353 / 2311 RPM in exp1 / exp2 / exp4 / exp5; ch2 is 0 in 4-7 % of flying frames. Same ISR for all four channels, 2 pulses per rev: a missed edge makes one period span more than one revolution, so missed edges read low, never high. Hardware, not firmware | logs/exp*-500g.slot2.csv, `rpm_dbg_rpm[]`, `BSP/rpm.c` `RPM_EdgeISR` | debug only (no control code reads it). Bench, props off: check the ch2 and ch0 sensor alignment, marks and wiring; watch `rpm_dbg_edges[]` rise at 2 per rev on all four |
| 4 | of2_h (ToF) reads the floor, not the bottle | 10-07 logs | no change needed for height; an outlier gate is still open |
| 5 | fence / Simplex did not trip during the swing | pilot report | not checked overnight; check the trip thresholds against the logged angles |

## Lessons from 10-07 (friction to remove)

- Match firmware gains before proposing a gain change: the vel_kp x0.5 proposal came from a bench with the wrong
  gains and was retracted.
- Confirm the WiFi log preset in chat before each flight (QA format), not in the dashboard queue.
- Terminal over dashboard: approvals in the dashboard were not reliable; manual mode plus Keil presets needs neither.
- One change per flight, and say which preset flew in the log name (for example `exp6_p1-500g`).
