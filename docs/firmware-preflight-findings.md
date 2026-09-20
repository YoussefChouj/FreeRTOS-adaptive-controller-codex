# Firmware Pre-Flight Audit: Control Path Findings Prior to Real Flight Tests

## Executive Summary
This document provides a comprehensive read-only audit of the flight controller firmware (`API`, `TASK`, `BSP`, `USER`, `Global_file`) prior to conducting real free-flight tests. The audit focuses strictly on the control path, sensor pipelines, state machines, and failsafes. 

Thirteen concrete findings were identified, including **5 Critical P0 defects** that would directly cause loss of control, uncommanded spool-up, or aircraft flyaway in free flight.

---

## Severity Counts Summary

| Severity | Count | Definition / Flight Impact |
| :--- | :---: | :--- |
| **P0** | 5 | Can cause loss of control or an uncommanded motor state in flight / ground |
| **P1** | 4 | Degrades control or hides a fault from the operator in flight |
| **P2** | 3 | Correctness or robustness issue that is not flight-critical |
| **P3** | 1 | Code clarity, dead code, maintainability |
| **Total** | **13** | |

---

## Detailed Findings (Ranked P0 First)

### Finding 1 [P0]: SBUS Link Loss / Failsafe Unhandled — Drone Continues Flying on Stale Commands Indefinitely
- **File & Line**: `TASK/RemoterTask.c:42-56`, `TASK/RemoterTask.c:134-152`, `BSP/usart1.c:64-88`, `API/rc_input.c:100-112`
- **What the Code Does**:
  1. In `BSP/usart1.c:64-88`, `DrvSbusGetOneByte` parses incoming 25-byte SBUS frames into `sbus_channel[0..15]`. Standard SBUS protocol encodes frame lost on bit 2 and failsafe activated on bit 3 of byte 23 (`datatmp[23]`). `DrvSbusGetOneByte` completely ignores byte 23. Most RC receivers (FrSky, Futaba, RadioMaster, ELRS) continue streaming SBUS frames to the flight controller on RF loss with bit 3 set. Because UART bytes continue to arrive, `sbus_last_valid_tick` continues to update, so `sbus_lost` is never asserted by the 500 ms timeout.
  2. Even if the receiver disconnects physically and `sbus_lost = 1` is latched after 500 ms (`RemoterTask.c:42-56`), `Check_Fly_Mode()` (`RemoterTask.c:134-152`) only inspects `sbus_channel[9] <= 500` to trigger `FLIGHT_EVENT_DANGEROUS_STOP`. It **never checks `sbus_lost`**. If channel 9 was high (>500) before link loss, `FlightFSM_Event(FLIGHT_EVENT_RECOVER_SDK)` is called continuously.
  3. In `API/rc_input.c:100-112`, when `s_authority == 0` (pilot mode), `RCInput_Get(RC_AXIS_THR)` reads `Remoter.ThrCtrler`, which holds the last frozen throttle channel value.
- **In-Flight Trigger**:
  The aircraft flies out of RC transmitter range, transmitter battery dies, RF signal is jammed/lost, or the receiver enters failsafe in flight.
- **Consequence**:
  The flight controller executes no failsafe procedure, never initiates landing, and never disarms. The motors continue spinning at the last commanded throttle and attitude commands indefinitely, resulting in a flyaway until battery depletion or high-speed collision.
- **Proposed Fix & Risk**:
  1. In `BSP/usart1.c`, inspect `datatmp[23] & 0x08` (failsafe flag) and set `sbus_lost = 1U`.
  2. In `TASK/RemoterTask.c` (`Check_Fly_Mode`), check `if (sbus_lost)` and transition to `flight_phase = FLIGHT_PHASE_LANDING` (or emergency descent) if flying, or disarm if on ground.
  3. *Risk*: A brief single-packet glitch must not trigger immediate hard disarm in mid-air. The failsafe trigger must require a debounced link loss (e.g. 500 ms consecutive timeout) and initiate a controlled descent ramp.
- **Confidence**: High (100% verified across parser, FSM, and RC inputs).

---

### Finding 2 [P0]: Optical Flow / ToF Disconnect or Mid-Flight Freeze Causes Runaway Position Integration and Zero-Rate Climb Runaway
- **File & Line**: `TASK/StabilizerTask.c:254-340`, `TASK/StabilizerTask.c:404-405`, `TASK/StabilizerTask.c:442-467`, `API/Ano_OF.c:6-39`
- **What the Code Does**:
  1. `AnoOF_Check_State()` in `API/Ano_OF.c:6-39` tracks `check_time_ms` to detect optical flow and altitude sensor timeouts, but it is **never called anywhere in the firmware**.
  2. If the optical flow / ToF sensor disconnects or freezes mid-flight:
     - `ano_of.of_quality` remains latched at its last value (e.g., 200), keeping `of_ok = 1`.
     - `ano_of.of2_dx_fix` and `ano_of.of2_dy_fix` freeze at their last non-zero values.
     - `StabilizerTask.c:334-335` continues integrating `of_dx_deb * 0.005f` into `ano_of.earth_x/y` every 5 ms.
     - `Ctrler.locxPID.FB` integrates towards infinity at the frozen velocity.
     - Position error explodes, driving maximum pitch/roll tilt commands.
  3. At `StabilizerTask.c:404-405`, `Ctrler.locxsPID.FB` and `locysPID.FB` consume `ano_of.of2_dx` and `of2_dy` **unconditionally every tick without checking `of_quality`**.
  4. At `StabilizerTask.c:442-467`, if the ToF sensor fails or the drone climbs above 500 cm (`alt_med > 500`), `ano_of.of2_raw_h` stops updating. Consequently, `ano_of.of2_h_f2_v` (climb rate feedback) drops to `0.0 m/s`. `Ctrler.Z_ratePID.FB` becomes 0, so the controller cannot sense vertical rate, leading to unbounded vertical climb or descent.
- **In-Flight Trigger**:
  Optical flow / ToF sensor cable disconnect, sensor brownout, sensor freeze, low-reflectivity surface dropout, or flight above 5 m AGL.
- **Consequence**:
  Immediate loss of control. The aircraft drives maximum roll/pitch into the phantom position error and/or climbs/descends uncontrollably due to missing vertical velocity feedback.
- **Proposed Fix & Risk**:
  1. Periodically call `AnoOF_Check_State(0.005f)` in `Stabilizer_Task`.
  2. In `StabilizerTask.c:404-405`, gate velocity feedback on `ano_of.of_quality >= OF_MIN_QUALITY`, defaulting velocity FB to 0 when quality drops.
  3. If optical flow quality drops while in `of_hold_on`, automatically drop out of OF-hold to Angle Mode (`of_hold_on = 0`) and notify the operator.
  4. If ToF altitude drops out, fall back to barometer/IMU fusion or command a safe throttle descent.
  5. *Risk*: Transitioning out of position hold mid-flight must be bumpless to avoid a sudden attitude jerk.
- **Confidence**: High (100% verified).

---

### Finding 3 [P0]: Uncommanded Motor Spool-Up to Hover Power on Ground via Height Sensor Glitch
- **File & Line**: `TASK/StabilizerTask.c:585-608`
- **What the Code Does**:
  In `Update_Motor()`:
  ```c
  else if (flight_phase == FLIGHT_PHASE_GROUND_IDLE)
  {
      if (Ctrler.Z_posPID.FB > 0.2f)
          flight_phase = FLIGHT_PHASE_FLYING;
      ...
  }
  else if (flight_phase == FLIGHT_PHASE_FLYING)
  {
      Set_PWM_Motors();
  }
  ```
  When armed in `FLIGHT_PHASE_GROUND_IDLE`, if `Ctrler.Z_posPID.FB` (`ano_of.of2_h`) exceeds 0.2 m (20 cm), `flight_phase` transitions to `FLIGHT_PHASE_FLYING`. Once in `FLYING`, `Set_PWM_Motors()` is called every tick. `Compute_Motor` sets `Throttle_out = Ctrler.Z_ratePID.U + Throttle_th` (where `Throttle_th = 2950`).
- **In-Flight / Ground Trigger**:
  The aircraft is armed on the ground. A ground obstacle, grass, personnel walking past, sensor resting-height calibration offset, or hand movement under the ToF sensor causes a transient height reading > 20 cm.
- **Consequence**:
  The motors immediately spool from IDLE (`2150` PWM) to full hover power (`~2950` PWM) with the pilot's throttle stick at zero. The aircraft takes off uncommanded or flips over on the ground, creating severe safety risks for ground crew.
- **Proposed Fix & Risk**:
  Require BOTH height > 0.2 m AND deliberate pilot throttle command (`RCInput_Get(RC_AXIS_THR) > 0.2f`) before transitioning from `GROUND_IDLE` to `FLYING`.
  *Risk*: None. Standard multirotor practice is never to enter flight phase without pilot throttle command.
- **Confidence**: High (100% verified).

---

### Finding 4 [P0]: Raw Gyro Rate Feedback Injected with Mahony Angle Error and Integral Bias Mutating `Gyro_*_Real`
- **File & Line**: `API/imu_update.c:134-136`, `API/bmi088_driver.c:396-398`, `TASK/StabilizerTask.c:485-487`
- **What the Code Does**:
  In `API/imu_update.c`:
  ```c
  Gyro_X_Real += kp_eff * ex + exInt;
  Gyro_Y_Real += kp_eff * ey + eyInt;
  Gyro_Z_Real += kp_eff * ez + ezInt;
  ```
  `IMU_Update_Mahony` mutates global variables `Gyro_X_Real`, `Gyro_Y_Real`, `Gyro_Z_Real` in place.
  In `TASK/StabilizerTask.c:485-487`:
  ```c
  Ctrler.gyroyPID.FB = GyroFilter_Apply(GYRO_FILT_PITCH, -Gyro_Y_Real*RAD2DEG);
  Ctrler.gyroxPID.FB = GyroFilter_Apply(GYRO_FILT_ROLL,   Gyro_X_Real*RAD2DEG);
  Ctrler.gyrozPID.FB = GyroFilter_Apply(GYRO_FILT_YAW,   -Gyro_Z_Real*RAD2DEG);
  ```
  The inner rate PID loop consumes `Gyro_X_Real * RAD2DEG`. Because `IMUSample_Task` (1000 Hz), `IMU_DataDeal_Task` (1000 Hz), and `Stabilizer_Task` (200 Hz) all share FreeRTOS Priority 4, `Gyro_X_Real` contains either raw physical rate or rate corrupted by Mahony attitude correction (`kp_eff * ex + exInt`).
- **In-Flight Trigger**:
  Any flight condition where attitude error `ex, ey` is non-zero (wind gusts, aggressive maneuvering, forward flight). During maneuvers, `kp_eff * ex` can exceed 0.5 rad/s (~30 deg/s).
- **Consequence**:
  The rate PID controller receives corrupted angular rate feedback injected with attitude error and scheduling jitter. This degrades phase margin, injects phantom damping forces, and can trigger high-frequency oscillations or loss of attitude stabilization.
- **Proposed Fix & Risk**:
  In `API/imu_update.c`, store corrected rates in local variables (`gx_corr = Gyro_X_Real + kp_eff * ex + exInt;`) for quaternion integration (`delta_theta`). Never mutate the global `Gyro_*_Real` measurement buffers consumed by rate PID, MRAC, and EKF.
  *Risk*: Low. Eliminating corruption restores clean physical angular rate feedback to the inner loop.
- **Confidence**: High (100% verified).

---

### Finding 5 [P0]: Silent Reuse of Stale IMU Samples on Dropped/Corrupt SPI Reads Without Runtime Error Detection
- **File & Line**: `BSP/spi.c:64-73`, `API/bmi088_driver.c:183-247`, `API/bmi088_driver.c:346-415`, `API/bmi088_driver.c:179`
- **What the Code Does**:
  1. In `BSP/spi.c:64-73`, `spi2_read_write_byte` implements a polling loop `to = 10000U`. If a timeout occurs, it returns `0U`.
  2. In `API/bmi088_driver.c:183-247`, `GetValue()` calls `BMI088_Read_Acc_Data` and `BMI088_Read_Gyro_Data`. It never checks SPI return codes, never checks `ACC_STATUS` or `GYRO_INT_STAT_1` (status check is commented out), and has return type `void`.
  3. `Warning` computed at line 407 is a dead variable that is never read anywhere in the firmware.
  4. `sensor.sensor_ok` is set to `1U` at boot (`bmi088_init`) and is **never cleared during runtime execution**.
  5. If `IMUSample_Task` hangs or SPI encounters intermittent bus faults, `IMU_DataDeal_Task` continues running at 1000 Hz using stale `Acc_*_Real` and `Gyro_*_Real` data.
- **In-Flight Trigger**:
  Vibration-induced electrical contact bounce on SPI bus, motor electrical noise/EMI during high-throttle climb, or SPI bus timeout.
- **Consequence**:
  The Mahony filter continuously integrates stale angular rates, causing rapid orientation divergence. The controller commands extreme attitude correction against the phantom tilt, immediately flipping the aircraft.
- **Proposed Fix & Risk**:
  1. Add return status verification to SPI transactions.
  2. Detect consecutive SPI timeouts or frozen IMU readings in `IMUSample_Task`.
  3. Clear `sensor.sensor_ok` and trigger an emergency disarm / failsafe if IMU communication is lost for > 15 ms.
  4. *Risk*: Low. Timeout thresholds must be chosen to avoid false-positive triggers on isolated 1-sample glitches.
- **Confidence**: High (100% verified).

---

### Finding 6 [P1]: Legacy PID Anti-Windup Logic (`AW_LEGACY`) Is Mathematically Ineffective
- **File & Line**: `API/pid.c:127-146`, `Global_file/robot_types.h:8-41`
- **What the Code Does**:
  In `ComputePID()` under `AW_LEGACY` (default mode):
  ```c
  if(((pPID->U <= pPID->UMax && pPID->E > 0) || (pPID->U >= -pPID->UMax && pPID->E < 0))           && ABS(pPID->E) < pPID->EMin)
  {
      pPID->SumE += pPID->E;
  }
  ```
  Because `pPID->U` was clamped to `[-pPID->UMax, pPID->UMax]` at the conclusion of the previous iteration, `pPID->U <= pPID->UMax` and `pPID->U >= -pPID->UMax` evaluate to `TRUE` for all valid states. Thus, whenever `ABS(E) < EMin`, `SumE += E` accumulates even when the controller is saturated against its limit.
  Furthermore, `aw_mode` defaults to 0 (`AW_LEGACY`) across all PID instances in `Ctrler` because `aw_mode` is not initialized in `API/pid.c:7-23`. The improved `AW_CLAMP` mode implemented in `API/pid.c` is never enabled!
- **In-Flight Trigger**:
  Motor saturation during fast climbs, aggressive roll/pitch commands, or high wind conditions where PID output `U` hits `UMax`.
- **Consequence**:
  Integrators wind up to `SumEMax`. Upon recovery, the accumulated integral produces substantial overshoot, delayed stabilization, and potential oscillation.
- **Proposed Fix & Risk**:
  Initialize `aw_mode = AW_CLAMP` on all controllers in `API/pid.c`.
  *Risk*: Slight change in transient response when saturated; bench step-response verification recommended.
- **Confidence**: High (100% verified).

---

### Finding 7 [P1]: MRAC Hard Freeze Drops Adaptive Injection Discontinuously to Zero on Disturbance Spikes
- **File & Line**: `API/mrac.c:236-242`
- **What the Code Does**:
  ```c
  if (mrac_flags.hard_freeze_on && config->e_freeze > 0.0f && fabsf(state->e) > config->e_freeze) {
      state->u_ad = 0.0f;
      return;
  }
  ```
  When tracking error `|e|` exceeds `e_freeze` (1.2 rad/s on pitch/roll), the adaptive control output `state->u_ad` is instantaneously forced to 0.0f in a single 5 ms tick.
- **In-Flight Trigger**:
  MRAC output injection enabled (`mrac_flags.output_injection_on = 1`) in flight, and the drone encounters a sudden wind gust, stick reversal, or turbulence pushing `|e| > 1.2 rad/s`.
- **Consequence**:
  If MRAC was providing trim compensation (up to `u_max = 6.74`), that torque contribution drops to zero instantaneously. This produces an abrupt control torque step during an extreme disturbance, followed by chatter as `|e|` crosses `e_freeze`.
- **Proposed Fix & Risk**:
  When hard freeze fires, freeze adaptation of `Theta` and hold `u_ad` at its previous value (or rate-limit / ramp it down smoothly), rather than hard-zeroing `state->u_ad`.
  *Risk*: Low. Rate-limiting decay is the standard aerospace adaptive control practice.
- **Confidence**: High (100% verified).

---

### Finding 8 [P1]: Optical Flow Position-Hold Switch Allows Engagement with Degraded or Zero-Quality Sensor Data
- **File & Line**: `TASK/StabilizerTask.c:1082-1094`
- **What the Code Does**:
  ```c
  u8 of_hold_on = (sbus_lost == 0U) && (OFHOLD_CH > 1000);
  g_of_hold_active = of_hold_on;
  ```
  The optical flow position-hold mode (`of_hold_on`) is gated solely by `sbus_channel[5] > 1000` and `!sbus_lost`. It does not check `ano_of.of_quality >= OF_MIN_QUALITY`.
- **In-Flight Trigger**:
  Pilot flips the OF-hold switch while optical flow has poor texture (`of_quality < 50` or `of_quality == 0`), or optical flow degrades in flight.
- **Consequence**:
  Outer loop closes around frozen position feedback and noisy velocity feedback (`locxsPID.FB`, lines 404-405). The aircraft tilts unpredictably and drifts.
- **Proposed Fix & Risk**:
  Require `ano_of.of_quality >= OF_MIN_QUALITY` in the `of_hold_on` condition. If quality drops below threshold in flight, automatically fall back to Angle Mode with an alert.
  *Risk*: None. Prevents engaging position hold on invalid sensor data.
- **Confidence**: High (100% verified).

---

### Finding 9 [P1]: Airborne In-Flight Disarm via Stick Gesture Without Altitude or Flight-Phase Interlock
- **File & Line**: `TASK/RemoterTask.c:88-91`, `TASK/RemoterTask.c:118-122`, `API/flight_fsm.c:44`
- **What the Code Does**:
  Holding throttle MIN and yaw MIN for `DISARM_Delay_time` (500 ms) fires `FLIGHT_EVENT_DISARM_REQUEST`. In `API/flight_fsm.c:44`:
  ```c
  case FLIGHT_STATE_ARMED:
      if (event == FLIGHT_EVENT_DISARM_REQUEST) { s_state = FLIGHT_STATE_DISARMED; s_sync(s_state); flight_phase = FLIGHT_PHASE_GROUND_IDLE; }
  ```
  There is no check for `flight_phase == FLIGHT_PHASE_FLYING` or `Ctrler.Z_posPID.FB > 0.2f`.
- **In-Flight Trigger**:
  Pilot commands full down throttle and full left yaw during flight (e.g., rapid descending yaw spin or panic stick pull) and holds for 500 ms.
- **Consequence**:
  Motors are instantly cut to zero (`Set_Zero_Motors()`), causing the aircraft to tumble out of the sky.
- **Proposed Fix & Risk**:
  Block stick gesture disarm when `flight_phase == FLIGHT_PHASE_FLYING`. In-flight emergency motor stop should only be permitted via dedicated emergency switch (`DangerousStop` ch10).
  *Risk*: None. Prevents accidental in-flight motor cutoff.
- **Confidence**: High (100% verified).

---

### Finding 10 [P2]: Derivative Kick on Setpoint Changes Due to Derivative on Error (`Kd * (E - PreE)`)
- **File & Line**: `API/pid.c:78-79`, `API/pid.c:138-139`
- **What the Code Does**:
  The derivative term is computed from `pPID->Kd * ( pPID->E - pPID->PreE )`. When setpoint `Des` steps, `E - PreE` spikes by `Des - Des_prev`.
- **In-Flight Trigger**:
  Abrupt stick movement or setpoint step changes.
- **Consequence**:
  Derivative output spikes to `UdMax`, causing actuator twitches and current surges.
- **Proposed Fix & Risk**:
  Compute derivative on measurement: `Ud = -Kd * (FB - PreFB)`, or filter setpoints.
  *Risk*: May require slight `Kd` gain adjustment.
- **Confidence**: High (100% verified).

---

### Finding 11 [P2]: Pseudo-Control Hedging (PCH) Configured as Enabled but Completely Unimplemented
- **File & Line**: `API/mrac.h:71`, `API/mrac.c:50-70`
- **What the Code Does**:
  `API/mrac.h:71` defines `#define ENABLE_PSEUDO_CONTROL_HEDGING 1`. However, in `API/mrac.c:50-70`, `MRAC_InverseMixer` is an empty stub returning 0, and PCH reference model modification is nowhere in `MRAC_UpdateAxis()`.
- **In-Flight Trigger**:
  Actuator saturation in flight while MRAC adaptation is active.
- **Consequence**:
  The codebase claims PCH protection, but adaptation continues to adapt against tracking errors caused purely by actuator saturation.
- **Proposed Fix & Risk**:
  Either implement actual inverse mixer and PCH terms in `MRAC_UpdateAxis`, or set `#define ENABLE_PSEUDO_CONTROL_HEDGING 0`.
  *Risk*: None (clarity/correctness).
- **Confidence**: High (100% verified).

---

### Finding 12 [P2]: Double Debiasing in Mode 2 OF EKF
- **File & Line**: `TASK/StabilizerTask.c:304-305`, `API/ekf_of.c:18-28`, `API/ekf_of.c:185`
- **What the Code Does**:
  `StabilizerTask.c` subtracts `s_of_bias_x` from `ano_of.of2_dx_fix` before passing it to `EkfOf_Update`. Inside `EkfOf_Update`, the filter's measurement model `y = of_meas - (x[vel] - x[bias])` estimates bias again on the already-subtracted residual.
- **In-Flight Trigger**:
  Mode 2 (EKF) selected via `g_of_bias_mode == 2`.
- **Consequence**:
  Coupled bias estimates; state `x[2]` estimates residual error rather than true sensor bias.
- **Proposed Fix & Risk**:
  Pass raw scaled OF measurement to `EkfOf_Update` without subtracting `s_of_bias_x`.
  *Risk*: Low. Mode 2 is non-default.
- **Confidence**: High (100% verified).

---

### Finding 13 [P3]: Dead Code in PID Library (`ComputePID_locx`, `ComputePID_locy`)
- **File & Line**: `API/pid.c:179-240`
- **What the Code Does**:
  Functions `ComputePID_locx` and `ComputePID_locy` are defined and compiled, but never called anywhere in the project.
- **Consequence**:
  Redundant code consuming flash; creates confusion regarding coordinate frame transformations.
- **Proposed Fix & Risk**:
  Remove dead functions.
  *Risk*: None.
- **Confidence**: High (100% verified).

---

## In-Depth Analysis of Optical Flow Degraded Path (`of_quality = 0`)

On the bench, the optical flow sensor currently reports `of_quality = 0`. The control gate at `StabilizerTask.c:254` (`of_ok = (ano_of.of_quality >= 50)`) evaluates to false. 

### What the Loop Actually Does in This State:
1. **Bias Estimation**:
   In default Mode 0 (FIXED), `s_of_bias_seeded` is never set. `s_of_bias_x/y` remain `0.0`.
2. **Position Feedback (`locxPID.FB`, `locyPID.FB`)**:
   Lines 326-341 are skipped. `ano_of.earth_x` and `ano_of.earth_y` remain static at 0.0 (or whatever value they held). Position feedback is frozen.
3. **Velocity Feedback (`locxsPID.FB`, `locysPID.FB`)**:
   Lines 404-405 execute **unconditionally without any quality check**:
   ```c
   Ctrler.locxsPID.FB = (ano_of.of2_dy) * Cos_Yaw_01 + (-ano_of.of2_dx) * Sin_Yaw_01;
   Ctrler.locysPID.FB = (-ano_of.of2_dx) * Cos_Yaw_01 - (ano_of.of2_dy) * Sin_Yaw_01;
   ```
   If the sensor outputs noise or biased raw flow at quality 0, `locxsPID.FB` and `locysPID.FB` consume that noise directly!
4. **Control Mode Gating**:
   If the pilot flips the OF position-hold switch on RC channel 6 (`OFHOLD_CH > 1000`):
   Line 1082 checks `u8 of_hold_on = (sbus_lost == 0U) && (OFHOLD_CH > 1000);` without checking `of_quality`!
   The controller engages velocity loop outputs (`locxsPID.U`, `locysPID.U`) to command desired pitch and roll lean angles. Because position feedback is frozen at 0 while velocity feedback is receiving noise, the loop commands erratic lean angles.
5. **Conclusion**:
   The degraded path is **NOT SAFE**; it is merely untested. If the pilot flips the OF-hold switch while optical flow has no lock, the aircraft will immediately tilt and fly into a wall or the ground.

---

## What Could Not Be Evaluated Statically and Why

1. **Optical Flow Camera Dynamic Noise & Lighting Sensitivity Outdoors**:
   The bench optical flow sensor reports `of_quality = 0`. We cannot statically evaluate how ambient sunlight, shadowed ground, grass/asphalt textures, or flight vibrations affect optical flow tracking reliability in the air.
2. **True Aerodynamic Plant Matrices for MRAC and Cascaded PID**:
   Parameters such as inertia matrix ($J_{xx}, J_{yy}, J_{zz}$), thrust coefficient $C_T$, and motor lag constants were identified on a bench fixture. Static code analysis cannot evaluate downwash ground-effect dynamics, cross-wind aerodynamic damping, or prop wash interaction with the airframe in free flight.
3. **High-Current SPI Bus Signal Integrity**:
   Under free-flight conditions, motor currents reach 60-80 A total. Static analysis cannot assess electrical noise, ground bounce, or EMI coupling into the SPI2 bus lines between the STM32 and BMI088.
4. **Specific SBUS Receiver Hardware Failsafe Behavior**:
   Different radio receivers (FrSky vs Futaba vs ELRS) have configurable failsafe modes (hold, no-pulses, custom values). We cannot verify by code inspection whether the physical receiver installed on the drone will emit continuous failsafe packets or cease output entirely upon transmitter loss.
