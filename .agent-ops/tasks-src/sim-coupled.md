# Task sim-coupled: coupled 6-DOF quad simulation, firmware cascades + mixer saturation, PID vs MRAC layers

## Goal
`python sim/adaptive_compare/run_coupled.py` finishes in < 5 min and writes PNG figures + `results_coupled.json`
into `sim/adaptive_compare/figures_coupled/` comparing PID, S6, S10, RBF6, RBF12, RBF24 (adaptive layer on
roll+pitch+yaw rate loops simultaneously) in a COUPLED rigid-body quad. HARD TIME LIMIT: 20 minutes total.

## Context pointers
- Roll template (verified): `sim/adaptive_compare/sim_core.py` (PID = AW_LEGACY ComputePID copy, MRAC law,
  S6/S10/RBF features, constants) and `run_all.py` (figure style). Import from sim_core; do not edit it.
- Read the firmware: mixer (motor mix, PWM clamp 4000, min/idle) in `TASK/stabilizertask.c` / `API/`; roll/pitch/
  yaw angle+rate PID gains and the altitude/thrust path. Use ACTUAL numbers, cite file:line in the digest.
  Yaw facts: gyroz AW_LEGACY, EMin 1000, Ki 0.005, UiMax 500, SumEMax 1e5, Umax/Upmax 650.
- Model: 6-DOF rigid body (Euler eqs with gyroscopic cross terms (Jy-Jz)qr etc.), 4 motors with first-order
  lag tau=1/19.8 s + 15 ms delay, per-motor thrust and yaw drag torque, X-config mixer from the firmware,
  per-motor PWM clamp (saturation couples axes), altitude hold loop, J0=0.0023 for roll (Jy, Jz from mrac.c or
  assumed; say so).
- Scenarios: C1 nominal hover + attitude steps on roll AND yaw simultaneously; C2 flight8 reality: constant
  yaw imbalance torque that needs ~450-650 yaw U so M3/M4 run near the 4000 clamp, then roll/pitch steps +
  climb command -> show saturation, lost climb authority, cross-axis error; C3 motor fault (one motor -25%
  thrust at t=6 s); C4 gust + drag + CoG offset; C5 combined. Plus 30-draw Monte Carlo.
  Metrics: per-axis rms deviation from the nominal-PID reference, altitude error, % time any motor saturated.

## Constraints
- Do not touch: firmware (`API/`, `TASK/`, `BSP/`, `USER/`, `Global_file/`), `OBJ/`, sim_core.py, run_all.py,
  and NOT `sim/adaptive_compare/sim_axes.py`/`run_axes.py`/`figures_axes/` (another worker owns them).
- No flashing, no probe, no port 8081. Foreground commands only. Do not commit. numpy/scipy/matplotlib only.

## Deliverables
1. `sim/adaptive_compare/sim_coupled.py`, `sim/adaptive_compare/run_coupled.py`
2. `figures_coupled/`: C2 time plot (roll, pitch, yaw rate, altitude, 4 motor PWMs with clamp line) for PID vs
   best layer; per-scenario RMS bar chart per axis; saturation-time chart; Monte Carlo box plot; 3-D or
   top-view trajectory for C5. 200 dpi presentation-sized, >= 7 PNGs.
3. `results_coupled.json` (per scenario, controller, axis metrics; saturation %; MC medians; diverged flags)
4. `.agent-ops/out/sim-coupled.md` (<= 30 lines: STATUS, mixer + gains with file:line, assumptions, key numbers)

## Verification
- `python sim/adaptive_compare/run_coupled.py` -> exit 0; `ls sim/adaptive_compare/figures_coupled/*.png | wc -l` >= 7
SUBSTITUTIONS: / NOT RUN: lines required in the digest.
