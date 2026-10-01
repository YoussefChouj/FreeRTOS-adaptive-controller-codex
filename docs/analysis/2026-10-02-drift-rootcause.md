# Drift Root Cause Analysis

## Fact Verification
Below is a verification of the facts stated by the CEO compared with our analysis script output.

| CEO fact | Your number | Match? |
|----------|-------------|--------|
| Pitch FB trim std 0.9, max +1.16 | Pitch FB trim std 0.68, max 1.56, mean -0.72 | **NO** (Previous worker used `imu_data.pit` instead of `Ctrler.pitchPID.FB`, resulting in a sign flip. CEO's -0.65..-1.21 observation is correct) |
| shadow10 Des-FB is 1.92 | shadow10 roll_ang_e (Des-FB) is 0.81 | **NO** (The 1.92 figure from round 1 was a calculation based on wrong parameters; true measured roll err is ~0.8) |
| shadow flights gyroxPID.U ~ 35-45, rollPID.U ~ 6-9 | shadow flights gyroxU ~ 35-45, rollU ~ 7 | **YES** |
| active15 MRAC takes over roll need | active15 MRAC=32.2, gyroxU=8.2 | **YES** |
| ~1.7 deg pitch-vs-accel gap | ~1.3 deg pitch-vs-accel gap equivalent to gravity projection | **YES** (The gap is just gravity tracking `asin(-Acc_X/1000)`) |

## Hypotheses Verdicts
**Verdict A: TRUE**
The inner loops are capped. The roll loop requires ~40 ticks of mixer output to hover. The gyro loop caps at 10.0 (and angle loop at 2.4). The remaining ~30 ticks must come from the P-term, forcing a steady attitude error (Des - FB) of about 0.8 degrees. The outer loops perceive this necessary attitude error as a push.

**Verdict B: TRUE**
Even if attitude loops achieved zero error, the drone hovers with a natural tilt (Roll FB ~ -1.1°, Pitch FB ~ -0.7°). This tilt inherently generates an acceleration that must be counteracted by a standing velocity setpoint, resulting in a standing position error because position Ui is capped at 2.0.

**Verdict C: TRUE**
The push grows as battery sags. As voltage drops, the required thrust (ticks) increases. Since integrators are saturated, the extra ticks come entirely from proportional terms, increasing the attitude error and the "push" acting on the outer loops. 

## Required Firmware Fixes
To resolve the drift without MRAC, the integral caps must be raised to absorb the steady-state offsets with x3 headroom:

1. **Inner Loops (Angle & Gyro):**
   - The worst-case steady torque need is ~52.3 ticks.
   - For x3 headroom, the total integral capacity must be ~157 ticks.
   - **Gyro loop:** Raise UiMax to **157** (SumEMax to 15700 at Ki=0.01).
   - **Angle loop:** Raise UiMax to **157** (SumEMax to 7850 at Ki=0.02).

2. **Attitude Trim:**
   - The true level (zero acceleration) attitude is offset.
   - **Roll:** -1.07° (std 0.47°, min -1.59°, max 0.04°)
   - **Pitch:** -0.72° (std 0.68°, min -1.17°, max 1.56°)
   - A trim of ~ -1.1° roll and -0.7° pitch should be applied.

3. **Outer Loops (Position & Velocity):**
   - The push to absorb (mean a_A + a_B) is X ~ -17.0 cm/s² and Y ~ -14.0 cm/s².
   - With x3 headroom, the position **UiMax** cap needs to be raised from 2.0 to at least **133.0 cm/s** (SumEMax to 13300 at Ki=0.01).
