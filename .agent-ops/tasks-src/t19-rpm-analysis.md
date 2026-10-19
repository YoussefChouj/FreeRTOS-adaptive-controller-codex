# T19: motor RPM in the flight analysis (conversion, stale detection, plots, metrics)

Do not contact 127.0.0.1:8081, UDP 14550 or the probe, and do not flash. Work offline. Commit with LF line endings on your branch.

## Context (measured on main 1fc90e5)

- `ground_station/analysis/flight_signals.yaml:81-88` maps:
  - `rpm_period_cyc1..4` → `slot2.rpm_dbg_period_cyc[0..3]`
  - `rpm_edges1..4` → `slot2.rpm_dbg_edges[0..3]`
- Nothing in `ground_station/analysis/*.py` uses them. There is no conversion, plot or metric.
- Firmware: `BSP/rpm.c` (the arrays are at around line 35).
  - `rpm_dbg_period_cyc[i]` is the timer-cycle period between edges.
  - `rpm_dbg_edges[i]` is a monotonic edge counter.
- The supervisor's working formula is `RPM = 60 * 168e6 / period_cyc`. **Verify it against `BSP/rpm.c` and the timer setup**: the clock source/prescaler, and whether an edge is per electrical or mechanical revolution (pole pairs). If they are unclear, keep the formula in one named constant/function with a comment that cites the file:line you based it on.

## Task

1. In the analysis package, find where derived signals, metrics and plots are built. Start with `flight_report` / metrics / plots under `ground_station/analysis/` and follow the existing patterns.
2. Derive `rpm1..rpm4`:
   - Set the value to NaN when `period_cyc` is 0.
   - Set it to NaN when the edge counter has not increased since the previous sample (a stale/stopped motor). Use a hold window of a few samples, which is reasonable because the logging rate is about 50 Hz.
3. Add a plot: 4 motor RPM traces vs time, research-paper style, consistent with the existing plots.
4. Add metrics, in `metrics.json` and `metrics.csv`, following the existing naming:
   - per-motor mean, std and max RPM over the flight/armed window;
   - the stale fraction;
   - an asymmetry index, `(max(mean_i) - min(mean_i)) / mean(mean_i)`;
   - the front-back and left-right mean differences, using the motor-layout mapping if the codebase defines one; otherwise leave them out and say so.
5. Add these to `summary.md` if the other metrics are listed there.
6. Degrade gracefully when the RPM keys are absent: no crash, and a "not recorded" note.

## Tests (offline, synthetic)

- A synthetic recording with known periods gives the expected RPM.
- Frozen edges give NaN.
- An absent-keys run passes.

Run `python -m pytest ground_station/analysis -q`, then the whole tree once with `python -m pytest ground_station -q`. `test_opencode_json_exists` failing in a worktree is expected; ignore it.

Report in `.agent-ops/out/t19-report.md`:

- the formula and its source at file:line;
- the files changed;
- the verbatim test counts;
- what was NOT done.
