# Research brief lab1018-bias3: MEASURE the camera-fix bias, 10-18 (bad) vs 10-15 (good)

Read-only: no edits to tracked files, no commits, never POST to 8081 or touch the drone. You MAY write and run
throwaway analysis scripts under `.agent-ops/research/nb/bias3/`. 20-minute budget, 160K token ceiling (GEMINI.md
rule 10). Output: `.agent-ops/out/argon-lab1018-bias3.md`, at most ~1000 words. Every number must come from a
script you ran (name the script and the input file). Mark guesses UNVERIFIED.

## Already known (do not re-derive)

- `.agent-ops/out/argon-lab1018-bias2.md`: 10-18 ran `--yaw-deg 94 --align fixed`, floor scan `floor-1018`.
  REJECTED as the cause by itself: the good 10-15 flights (`logs/video_truth/f1-robust`, `f1b-robust`, floor
  `floor-2026-10-15`) and 10-17 used the same `--yaw-deg 94 --align fixed`. Something else changed.
- Fix pairing math: `shadow_pairs` + `EarthAlign.fit` in `ground_station/analysis/live_tracker.py:868-889,950-984`
  (fix as sent z = g_ext_pos.e + ano_of.earth, cm; applied correction c = g_ext_pos.c_x/c_y). That code assumes
  slot1 = ano_of.earth_x/y + Ctrler.Z_posPID.FB and slot2 = g_ext_pos.*; the 10-18 .fly plans put them in other
  slots, so `live_tracker fit logs/mem2-...` fails. Find the columns by header, whatever slot or folder
  (`logs/*.slot*.csv`, `logs/flights/2026-10-18/`, `logs/sessions/20261018-*`).

## Measure, per flight (10-18: m1-u100, mem1..mem6, z1; 10-15: f1-robust, f1b-robust)

1. Per airborne stretch (Z FB > 0.15 m): residual yaw (deg), scale, and constant offset (cm) of fix vs EKF path
   (fit rotation+scale+translation on the pairs).
2. Applied correction |c| p50/p90/max (cm) and how it evolves (does it grow, does it jump at lift-off?).
3. n_rej / (n_ok + n_rej) and the fix rate (Hz) airborne.
4. `tracker.out` `pad_xy` and the ground-frame fixes before lift-off: is the pad origin a real drone position or a
   wrong blob? Compare with the floor scan's id0 centre.
5. Anything else that differs 10-15 vs 10-18 in the tracker command line or `tracker.out` (delay-ms, detector,
   gate, window size line "window 'r5cam' ready WxH", marker count, median shift).

## Deliverable

Bottom line (4 lines: what changed, size of the bias, cause, confidence). One table, one row per flight, columns
from items 1-3. Then items 4-5 short. Then the smallest fix with file:line and a check that would prove it on a
log replay (no flight needed).
