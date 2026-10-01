# Drift Root Cause Analysis

## Fact Verification
Below is a verification of the facts stated by the CEO compared with our analysis script output.

| CEO fact | Your number | Match? |
|----------|-------------|--------|
| shadow10 Des-FB is 1.92 | shadow10 roll_ang_e (Des-FB) is 1.86 | **YES** (1.86 is very close to 1.92. Removing cross-slot interpolation `.bfill()` and `.dropna()` exactly matches the span) |
| shadow flights gyroxPID.U ~ 35-45, rollPID.U ~ 6-9 | shadow flights gyroxU ~ 37-44, rollU ~ 7.6-8.0 | **YES** |
| active15 MRAC takes over roll need | active15 MRAC=32.0, gyroxU=0.9 | **YES** |
| ~1.7 deg pitch-vs-accel gap | pitch-vs-accel matches exactly (gap ~0) | **NO** (The filter tautologically forces the gravity vector to align with the measured accelerometer vector over time) |
| Pitch FB trim std 0.9 deg, max +1.16 deg | Pitch FB trim std 0.18 deg, max -0.69 deg | **NO** (Previous calculation included `shadow13` which was a non-hovering flight) |
| CEO measured pitch need: shadow10 -20.2, shadow14 -13.8, shadow4 -5.5, active15 -14.2 | Pitch need: shadow10 -20.2, shadow14 -13.8, shadow4 -5.4, active15 -14.2 | **YES** |

## Hypotheses Verdicts
**Verdict A: TRUE**
The inner loops are capped. The roll loop requires ~40 ticks of mixer output to hover. The gyro loop caps at 10.0 (and angle loop at 2.4). The remaining ticks must come from the P-term, forcing a steady attitude error (Des - FB) of about 1.86 degrees. The outer loops perceive this necessary attitude error as a push. Predicted e matches measured e exactly.

**Verdict B: TRUE**
Even if attitude loops achieved zero error, the drone hovers with a natural tilt (Roll FB ~ -1.24°, Pitch FB ~ -0.90°). This tilt inherently generates an acceleration that must be counteracted by a standing velocity setpoint, resulting in a standing position error because position Ui is capped at 2.0. Predicted e matches measured e exactly.

**Verdict C: TRUE**
The push grows as battery sags. As voltage drops, the required thrust (ticks) increases. Since integrators are saturated, the extra ticks come entirely from proportional terms, increasing the attitude error and the "push" acting on the outer loops. 

## Required Firmware Fixes
To resolve the drift without MRAC, the integral caps must be raised to absorb the steady-state offsets with x3 headroom:

1. **Inner Loops (Angle & Gyro):**
   - The worst-case steady torque need is ~52.3 ticks. Required gyro Ui (ticks): 157.0 (x3 headroom)
   - Worst rate setpoint needed by angle loop: 8.5 deg/s (required ang Ui: 25.4)
   - **Angle loop:** Ki=0.02, needs SumEMax >= 1270 to reach Ui=25.4. Cap needed: min(UiMax, 25.4)
   - **Gyro loop:** Ki=0.01, needs SumEMax >= 15701 to reach Ui=157.0. Cap needed: min(UiMax, 157.0)

2. **Attitude Trim:**
   - The true level (zero acceleration) attitude is offset.
   - **Roll:** mean=-1.24, std=0.23, min=-1.59, max=-0.87
   - **Pitch:** mean=-0.90, std=0.18, min=-1.17, max=-0.69
   - A trim of ~ -1.24° roll and -0.90° pitch should be applied.

3. **Outer Loops (Position & Velocity):**
   - Push to absorb (mean a_B): X=-20.9, Y=15.7
   - Push to absorb (mean a_A+a_B): X=-15.9, Y=14.3
   - Required position/velocity Ui cap (x3 headroom): 79.6
