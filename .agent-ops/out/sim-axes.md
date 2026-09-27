STATUS: done

**Firmware Gains (file:line)**:
- Pitch (`API/pid.c:7, 17`, `API/mrac.c:498, 506`): J0=0.0023, Kp=2.6, Ki=0.1, Kd=9.5, Umax=200; Rate Kp=5, Ki=0.01, Kd=10, Umax=300; U_TOT_MAX=500.
- Yaw (`API/pid.c:11, 18`, `API/mrac.c:535`): J0=0.0015, Kp=6.5, Ki=0.04, Kd=1.5, Umax=160; Rate Kp=4, Ki=0.005, Kd=2.0, Umax=650; U_TOT_MAX=650.
- Z (`API/pid.c:20, 21`, `API/mrac.c:559`): Mass=1.5kg, Kp=0.7, Ki=0.005, Kd=0.1, Umax=1.0; Rate Kp=400, Ki=0.435, Kd=1.5, Umax=300.

**Assumptions**:
- Pitch `K_EFF = 1170 / 0.37` (same effectiveness as roll).
- Yaw `K_MIX = 1872.0`, `G_EFF_YAW = 0.1` (assumed 10x weaker).
- Z `K_Z = 222.0`, hover thrust = mass * 9.81 (cancels gravity nominally).
- Yaw error wrapping at +-180 implemented in simulation, rate setpoint clamped to +-60 (as in `TASK/StabilizerTask.c:1348`).

**Key Numbers (MC medians of RMS Ref)**:
| Axis  | PID | S6 | S10 | RBF6 | RBF12 | RBF24 |
|---|---|---|---|---|---|---|
| Pitch | 2.00 | 0.86 | 1.02 | 1.06 | 1.03 | 1.03 |
| Yaw | 39.87 | 43.92 | 40.60 | 43.97 | 44.87 | 45.24 |
| Z | 0.48 | 0.42 | 0.42 | 0.40 | 0.40 | 0.40 |

**Verification**:
- `~/venv/bin/python sim/adaptive_compare/run_axes.py` -> exit 0 (pass)
- `ls sim/adaptive_compare/figures_axes/*.png | wc -l` -> 10 (pass)
- `python -c "import json;d=json.load(open('sim/adaptive_compare/figures_axes/results_axes.json'));print(list(d))"` -> ['pitch', 'yaw', 'z'] (pass)

**Open Questions / Risks**:
- Yaw true effectiveness ratio (G_EFF) is assumed 0.1; not measured.

SUBSTITUTIONS: G_EFF_YAW = 0.1
NOT RUN: none
