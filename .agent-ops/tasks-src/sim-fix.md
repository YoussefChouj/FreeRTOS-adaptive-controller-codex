# Task sim-fix: fix yaw metrics + C2 scenario sizing in the multi-axis sims (HARD LIMIT 12 minutes)

## Goal
Rerun `python sim/adaptive_compare/run_coupled.py` and `python sim/adaptive_compare/run_axes.py` (both exit 0)
after these fixes, regenerating figures_coupled/ + results_coupled.json and figures_axes/ + results_axes.json.

## Fixes (files: sim/adaptive_compare/sim_coupled.py, run_coupled.py, sim_axes.py, run_axes.py ONLY)
1. Yaw error in every metric must be wrapped: e = ((psi - psi_ref + 180) % 360) - 180. Currently C2 yaw RMS = 2445 deg
   and C5 = 1476 (heading runs away or is unwrapped). Also plot heading wrapped or as an error trace.
2. rms_* metrics in results_coupled.json must be deviation from the nominal-PID reference run (as in sim_core.metrics),
   so PID in C1 gives ~0. Keep the tracking-vs-command RMS as extra keys rms_cmd_*.
3. C2 "flight8 reality" imbalance: size the constant yaw disturbance torque so the PID steady yaw U is ~450-550
   (flight8 measured 450-650 and HELD heading within +-10 deg/s), which pushes M3/M4 near the 4000 clamp; verify
   PID holds heading (bounded yaw error) and that sat_pct > 0 and climb/roll authority is reduced. Print the
   steady yaw U and the M1..M4 means in the digest.
4. Yaw axis in sim_axes: yaw MC medians ~40 deg for every controller; check it is the wrap/metric issue and fix.
Keep everything else unchanged. No firmware edits, no commits, no port 8081, foreground only.

## Deliverable
`.agent-ops/out/sim-fix.md` (<= 30 lines): STATUS, what changed, new key-number tables (coupled per scenario:
roll/pitch/yaw/z rms + sat% for PID and best layer; axes MC medians), SUBSTITUTIONS:/NOT RUN: lines.
