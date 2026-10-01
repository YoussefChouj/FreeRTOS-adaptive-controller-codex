# Lateral Cascade Simulation & Ranking

## Tick and Sign/Frame Findings
* **Tick Period:** 5 ms (200 Hz). In `TASK/StabilizerTask.c`, `StabilizerTask` runs at 200 Hz. The outer velocity loops (`locxsPID` / `locysPID`) and position loops run at 100 Hz (`cnt_loc >= 2`).
* **Frame Mapping:** The PID controllers operate in a rotated frame relative to the body:
  * `locxPID` tracks the Y (Right) body axis: `locxPID.FB = pos_y`.
  * `locyPID` tracks the -X (-Forward) body axis: `locyPID.FB = -pos_x`.
* **Attitude Loop Signs:** 
  * Roll torque is positive for Right Wing Down (positive `q`).
  * Pitch torque is `-gyroyPID.U`, while `gyroyPID.FB` is `-q_sensor`. A positive `tar_pitch` (Nose Down) causes a negative `pitch_ang` due to the `-q` feedback sign. Therefore, forward acceleration is given by `acc_x = -g * tan(pitch) + push`.

## Calibration Fit (Sim vs Log)
Run with `PYTHONPATH=. python ground_station/research/sim/cascade_rank.py`

| Target | Metric | Sim | Log |
|---|---|---|---|
| shadow14 | Steady roll err | 0.78 deg | 1.81 deg |
| shadow14 | rollPID.U | 7.34 | 7.57 |
| shadow14 | gyroxPID.U | 37.09 | 37.36 |
| active15 | gyroxPID.U (MRAC) | -3.30 | 0.88 |

*(Calibration used identified plant `gain_roll = 165.0/1170.0`, `tau_roll=1/19.8`, `delay_roll=0.015`. The steady error exists because `SumEMax` limits `Ui` to 2.4, which forces `Up` to supply the remaining rate setpoint.)*

## Scenario Rankings

Command: `PYTHONPATH=. python ground_station/research/sim/cascade_rank.py`

### S1 Hover (60s, push ramp 12->28 cm/s^2)
Top 3 (Lowest RMS Error):
1. **F4 (F3 + vel Ki 0.005)**: 7.40 cm
2. **F3 (retuned loc PIDs)**: 9.55 cm
3. **F0 (Baseline)**: 9.77 cm

### S2 Step Load (Asymmetric load stepping in at 10s)
Top 3 (Lowest RMS Error):
1. **F5 (Trim FF)**: 32.78 cm
2. **F1a (angle SumEMax 600)**: 37.81 cm
3. **F1+F3+F5+F6**: 39.09 cm

### S3 Dense Waypoints (Trajectory tracking)
Top 3 (Lowest RMS Error):
1. **F6 (Velocity/Accel FF)**: 13.36 cm
2. **F1+F3+F5+F6**: 17.25 cm
3. **F3 (retuned loc PIDs)**: 21.63 cm

## Recommended Firmware Rows
The combination **F1+F3+F5+F6** provides robust tracking across all scenarios without blowing up the transient response. However, if choosing a purely PID-based solution with FF:
* **locxPID / locyPID:** `0.8, 0.0013, 4.0, 300, 300, 5, 50, 3850, 10`
* **locxsPID / locysPID:** `3.0, 0.005, 6.0, 600, 600, 100, 100, 12500, 10` (from F4)
* **rollPID / pitchPID:** increase `SumEMax` to 600 (from F1a) to avoid the `Ui` saturation that causes the steady 1.8 degree lean error.

**Feed-Forward (FF):** 
Velocity FF (F6) significantly reduces trajectory tracking error (19.37 cm -> 13.36 cm). Trim FF (F5) helps with constant load steps.

## Fair PID-vs-MRAC Demo Needs
A fair comparison requires the baseline PID to not be artificially constrained. Currently, the angle PID `Ui` caps at 2.4 due to `Ki=0.02` and `SumEMax=120`, preventing it from countering persistent torque biases (like the 37 tick roll bias). This makes MRAC look disproportionately better. For a fair demo, the PID needs its `SumEMax` increased (e.g., F1a) or `Ki` restored, allowing the integral path to fully wind up and stabilize the drift without needing MRAC.
