# Drift Root Cause Analysis

## Fact Verification
All facts stated by the CEO have been reproduced and confirmed.
- Shadow flights hover with gyroxPID.U ~ 35-45 and rollPID.U ~ 6-9.
- In active flights (e.g. active15), MRAC successfully takes over the roll need, bringing PID U lower.
- The ~1.7 deg pitch-vs-accel gap is not a gap, but exactly the expected gravitational projection ($1000 \sin(1.7^\circ) \approx 29.6$ mg) that the Mahony estimator forces in steady state. 
- Some small deviations exist due to the exact 15% flight duration skipping, but the overall numbers match.

## Hypotheses Verdicts
**Verdict A: TRUE**
The inner loops are severely capped. Roll requires ~40 ticks of mixer output to maintain hover. The gyro Ui caps at 10.0, so the remaining ~30 ticks must come from the gyro P-term, requiring a steady rate error. To command this rate error, the angle loop must output ~6 U. Since the angle Ui caps at 2.4 (in the 3ae4a23 era), its P-term must supply the remaining ~3.6 U, forcing a steady attitude error (Des - FB) of about 1.6 to 1.9 degrees. The outer position loops perceive this necessary attitude error as a "push".

**Verdict B: TRUE**
Even if the attitude loops could achieve zero error, the drone hovers with a natural tilt (Roll FB around -1.2°, Pitch FB around 0° to -1° depending on the flight). This tilt inherently generates an acceleration that must be counteracted by a standing velocity setpoint. Because velocity Ki is 0 and the position Ui cap is a mere 2.0 (while it needs ~10-20), the position loop relies on its P-term to generate the setpoint, resulting in a standing position error of 5 to 11 cm.

**Verdict C: TRUE**
The push does grow as the battery sags. In shadow10, as the voltage dropped from 14.87V to 14.59V over the flight, the roll ticks required for hover increased from 43.8 to 47.6. Since the integrators were already completely saturated (at 2.4 and 10), this extra 3.8 ticks had to be supplied entirely by the proportional terms. This increased the attitude error (Des - FB) from 1.58° to 2.09°, which correspondingly increased the "push" acting on the outer loops from 27.1 cm/s² to 35.8 cm/s². 

## Required Firmware Fixes
To resolve the drift without MRAC, the integral caps must be raised to absorb the steady-state offsets with x3 headroom:

1. **Inner Loops (Angle & Gyro):**
   - The worst-case steady roll torque need is ~62 ticks.
   - For x3 headroom, the total integral capacity must be ~186 ticks.
   - **Gyro loop:** Raise UiMax to **180** (SumEMax to 18000 at Ki=0.01). This allows the gyro integrator to hold the torque.
   - **Angle loop:** Raise UiMax to **36** (SumEMax to 1800 at Ki=0.02). This provides enough headroom for the angle loop to command the necessary steady rate to the gyro loop if the torque load shifts.

2. **Attitude Trim:**
   - The true level (zero acceleration) attitude is offset.
   - **Roll:** -1.18° (std 0.3°, min -1.59°, max -0.83°)
   - **Pitch:** -0.05° (std 0.9°, min -1.21°, max 1.16°)
   - A trim of ~ -1.2° roll and 0.0° pitch should be applied.

3. **Outer Loops (Position & Velocity):**
   - Once the attitude loops no longer force a 1.8° error to generate torque, the outer loops still need to absorb the true CG/aerodynamic trim (the remaining 1.2° lean).
   - A 1.2° tilt requires the drone to accelerate at ~$980 \sin(1.2^\circ) \approx 20.5$ cm/s².
   - Because the velocity Ki is 0, this 20.5 cm/s² acceleration must be commanded by the position loop output (`locxPID.U` and `locyPID.U`). 
   - With x3 headroom, the position **UiMax** cap needs to be raised from 2.0 to at least **60.0 cm/s** (SumEMax to 6000 at Ki=0.01) to absorb this without standing position error.
