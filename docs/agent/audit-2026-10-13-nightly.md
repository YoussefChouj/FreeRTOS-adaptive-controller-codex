# Audit of the 2026-10-13 nightly run (operator request, 2026-10-13 morning)

Source: `docs/agent/nightly-2026-10-13.md` rows 1-13, commits named there. Question: does what was marked "done"
match what the operator asked for?

## Verdict per item

| # | Item | Marked | What it really is | Gap |
|---|---|---|---|---|
| 1 | Coral mask learned at start | done | Replay only: r6_f4 fix error p50 272 -> 7.6 cm | Not run live yet; f4b suspect |
| 2 | NN gain | done | Retrain with r6 worse than the baseline NN on 2 of 3 bins; the NN clearly beats earth only on b7 (112.3 -> 54.0 cm) | **Goal missed** |
| 3 | Camera delay 0.21 s | done | xcorr coherence 0.44 (f4) and 0.14 (f4c), f2 inconclusive | Weak evidence for a default |
| 4 | Hands-off scoring | done | `video_truth handsoff4` | OK |
| 5 | Run sheet | done | docs/lab/run-sheet-2026-10-13-drift-free.md | OK |
| 6 | Yaw refit | done (monitor) | Refits -21..+15 deg, too noisy, not applied | OK as a monitor |
| 7 | Mode 3 q_pos 0.05 | done | Host test only; worker's gate claim not reproduced; replay no gain | Not flashed, not flown |
| 9 | Skills | done | camera-truth + stream-log docs | OK |
| 10 | Live plot (VOFA+ replacement) | done | **Custom 790-line DearPyGui app** (`live_plot.py`, 8d9450b2); "visuals not inspected" | **Intent mismatch, see below** |
| 11 | Live video in workflows A/B/C | done | Dry run without the phone | Not run with the phone |
| 12 | Drift | ready | Nothing flown | Fixes vs truth4 offset 40-95 cm unexplained |
| 13 | SOTA research | done | Doc; external numbers UNVERIFIED | Did not drive item 10 (below) |

## Item 10: why it diverged from the intent

Intent (operator, 2026-10-13): adopt a mature, modular plotting pipeline that fits our use, not a GUI from scratch.

1. The item was written as "VOFA+ replacement" and handed to a worker as a build task. Build vs adopt was never asked.
2. The research (24775707, 21:31) landed 2 minutes before the build (8d9450b2, 21:33), so it could not inform it.
   Its section 5 compares PlotJuggler, Rerun, Foxglove and VOFA+, then reads as a build plan for our own DearPyGui
   viewer ("already installed in Python venv").
3. `stream_log` already had a VOFA+ FireWater output (CSV over UDP, `stream_log.py:477`) before this item.
4. The new `--plot` wire format (`{"type":"row","v":[...]}`, `stream_log.py:136-141`) is our own, not one a
   standard tool reads directly.
5. "Done" meant 9 unit tests plus a 7 s launch. Nobody looked at a plot.

PROPOSED fix: adopt PlotJuggler (open source; its UDP server plugin reads JSON messages and saves layouts). Change
`stream_log --plot` to send one flat JSON object per row (`{"t":..,"s_ekf_of.x[0]":..}`) and drop `live_plot.py`
once a live capture shows in PlotJuggler. Not installed or tested here. Keep VOFA+ FireWater as the fallback.

## Process lessons

- A tool item needs an "adopt or build" line before a worker gets it, checked against the operator's words.
- "Done" for a GUI or a lab tool means it was seen working, not that the tests pass.
