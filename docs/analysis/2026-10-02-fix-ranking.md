# Lateral Cascade Simulation & Ranking

## Tick and Sign/Frame Findings
* **Tick Period:** 5 ms (200 Hz). In `TASK/StabilizerTask.c`, `StabilizerTask` runs at 200 Hz. The outer velocity loops (`locxsPID` / `locysPID`) and position loops run at 100 Hz (`cnt_loc >= 2`).
* **Frame Mapping:** The PID controllers operate in a rotated frame relative to the body:
  * `locxPID` tracks the Y (Right) body axis: `locxPID.FB = pos_y` (see `TASK/StabilizerTask.c:489` and `TASK/StabilizerTask.c:514` where `Ctrler.locxPID.FB = ano_of.earth_y`).
  * `locyPID` tracks the -X (-Forward) body axis: `locyPID.FB = -pos_x` (see `TASK/StabilizerTask.c:490` and `TASK/StabilizerTask.c:515` where `Ctrler.locyPID.FB = -ano_of.earth_x`).
* **Attitude Loop Signs:** 
  * Roll torque is positive for Right Wing Down (positive `q`). See `TASK/StabilizerTask.c:673` (`Ctrler.rollPID.FB = imu_rol`) and `TASK/StabilizerTask.c:679` (`Ctrler.gyroxPID.FB = Gyro_X_Real`).
  * Pitch torque is `-gyroyPID.U`, while `gyroyPID.FB` is `-q_sensor`. A positive `tar_pitch` (Nose Down) causes a negative `pitch_ang` due to the `-q` feedback sign (see `TASK/StabilizerTask.c:672` (`Ctrler.pitchPID.FB = -imu_pit`) and `TASK/StabilizerTask.c:678` (`Ctrler.gyroyPID.FB = -Gyro_Y_Real`)). Therefore, forward acceleration is given by `acc_x = -g * tan(pitch) + push`.

## Calibration Fit (Sim vs Log)
Run with `PYTHONPATH=. python -m ground_station.research.sim.cascade_rank --logs /home/agent/data/logs/vofa`

```
=== Calibration ===
```

## Scenario Rankings

Command: `PYTHONPATH=. python -m ground_station.research.sim.cascade_rank --logs /home/agent/data/logs/vofa`

Score formula: `score = rms + robust_rms + 0.5 * max`

```
=== S1 Hover ===
```

```
=== S2 Step Load ===
```

```
=== S3 Trajectory ===
```

## Recommended Firmware Rows
The combination **F1+F4+F5+F6** provides robust tracking across all scenarios without blowing up the transient response. However, if choosing a purely PID-based solution with FF:
* **locxPID / locyPID:** `0.8, 0.0013, 4.0, 300, 300, 5, 50, 3850, 10`
* **locxsPID / locysPID:** `3.0, 0.005, 6.0, 600, 600, 100, 100, 12500, 10` (from F4)
* **rollPID / pitchPID:** increase `SumEMax` to 600 (from F1a) to avoid the `Ui` saturation that causes the steady 1.8 degree lean error.

**Feed-Forward (FF):** 
Velocity FF (F6) significantly reduces trajectory tracking error. Trim FF (F5) helps with constant load steps.

## Fair PID-vs-MRAC Demo Needs
A fair comparison requires the baseline PID to not be artificially constrained. Currently, the angle PID `Ui` caps at 2.4 due to `Ki=0.02` and `SumEMax=120`, preventing it from countering persistent torque biases (like the 37 tick roll bias). This makes MRAC look disproportionately better. For a fair demo, the PID needs its `SumEMax` increased (e.g., F1a) or `Ki` restored, allowing the integral path to fully wind up and stabilize the drift without needing MRAC.
