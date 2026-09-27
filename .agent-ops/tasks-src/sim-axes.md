# Task sim-axes: yaw + altitude(z) + pitch single-axis PID-vs-MRAC simulations

## Goal
`python sim/adaptive_compare/run_axes.py` finishes in < 4 min and writes PNG figures + `results_axes.json`
into `sim/adaptive_compare/figures_axes/` comparing PID, S6, S10, RBF6, RBF12, RBF24 on the YAW, Z (altitude)
and PITCH axes, each using the firmware's real cascade structure and gains. HARD TIME LIMIT: 20 minutes total.

## Context pointers
- Template to copy (roll axis, already verified): `sim/adaptive_compare/sim_core.py` (PID class = AW_LEGACY
  ComputePID, MRAC update, features S6/S10/RBF, scenarios via nominal_params) and `sim/adaptive_compare/run_all.py`
  (scenarios S1-S5, Monte Carlo, figure style). Reuse their style/colours; import from sim_core where possible.
- Firmware gains: grep `API/pid.c`, `TASK/stabilizertask.c`, `API/mrac.c` for the yaw (yawPID/gyrozPID), altitude
  (height/velocity-z PID, thrust path) and pitch (pitch/gyroy PID) init values. USE THE ACTUAL NUMBERS; cite
  file:line for each gain in the digest. Recent yaw facts: gyroz AW_LEGACY, EMin 1000, Ki 0.005, UiMax 500,
  SumEMax 1e5, Umax/Upmax 650; heading hold commands rate +-60 at the +-180 wrap.
- Plant: yaw inertia Jz and pitch Jy from API/mrac.c if present (else Jz ~ 2*J0, Jy ~ J0 and say so); yaw torque
  is rotor drag torque (weaker effectiveness than roll: state the assumed ratio). Z: mass from firmware/mrac
  if present, else 1.0 kg (say so), thrust = hover + U/K_Z, gravity, drag, baro/accel noise.
- Scenarios per axis, mirror roll: nominal, bias (yaw: constant rotor-imbalance torque equal to ~450 PID units
  as measured in flight8; z: battery sag = thrust gain -15%), inertia/mass change + motor fault, drag+gust, combined.
  Commands: yaw heading steps +-45 deg + one wrap crossing; z steps 0->1 m->0.5 m + sine; pitch same as roll.

## Constraints
- Do not touch: firmware (`API/`, `TASK/`, `BSP/`, `USER/`, `Global_file/`), `OBJ/`, sim_core.py, run_all.py,
  and NOT `sim/adaptive_compare/sim_coupled.py` / `figures_coupled/` (another worker owns them).
- Hardware: no flashing, no probe, no port 8081. Foreground commands only. Do not commit.
- Monte Carlo: 40 draws per axis max (runtime). numpy/scipy/matplotlib only.

## Deliverables
1. `sim/adaptive_compare/sim_axes.py`, `sim/adaptive_compare/run_axes.py`
2. `figures_axes/`: per axis a time-response figure (combined scenario, all 6 controllers), a bar chart of RMS
   deviation per scenario, a Monte Carlo box plot; one summary figure across the 3 axes. 200 dpi, presentation-sized.
3. `results_axes.json`: per axis, per scenario, per controller rms_ref/peak_ref/effort/diverged; MC medians.
4. `.agent-ops/out/sim-axes.md` (<= 30 lines: STATUS, firmware gains with file:line, assumptions, key numbers table)

## Verification
- `python sim/adaptive_compare/run_axes.py` -> exit 0; `ls sim/adaptive_compare/figures_axes/*.png | wc -l` >= 10
- `python -c "import json;d=json.load(open('sim/adaptive_compare/figures_axes/results_axes.json'));print(list(d))"`
SUBSTITUTIONS: / NOT RUN: lines required in the digest.
