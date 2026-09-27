# Firmware Inventory

## 1. Task/loop rates
* **SystemMonitor_Task**: `pdMS_TO_TICKS(1000)` (1 Hz) via `vTaskDelayUntil` (USER/main.c:246).
* **IMU_DataDeal_Task** (attitude/Mahony): `pdMS_TO_TICKS(1)` (1000 Hz) via `vTaskDelayUntil` (USER/main.c:372).
* **IMUSample_Task** (sensors): `pdMS_TO_TICKS(1)` (1000 Hz) via `vTaskDelayUntil` (USER/main.c:391).
* **Stabilizer_Task** (rate loop, pos/vel, OF EKF): `pdMS_TO_TICKS(5)` (200 Hz) via `vTaskDelayUntil` (USER/main.c:420).
* **Remoter_Task**: `pdMS_TO_TICKS(10)` (100 Hz) via `vTaskDelayUntil` (USER/main.c:464).
* **Autofly_Task**: `pdMS_TO_TICKS(5)` (200 Hz) via `vTaskDelayUntil` (USER/main.c:481).
* **Send_Task** (EKF9, telemetry): 100 Hz via `vTaskDelayUntil(..., pdMS_TO_TICKS(10))` (USER/main.c:357) or 200 Hz via `vTaskDelay(pdMS_TO_TICKS(5))` when `id_frame_on` / `of_frame_on` (USER/main.c:322). EKF dt uses measured DWT cycle time.

## 2. Gyro/accel filter chain
* **Accel**: Hardware set to 1600 Hz (API/bmi088_driver.c:144). Software 2nd-order Butterworth LPF at 30 Hz: `Butterworth30HzLPF` (API/bmi088_driver.c:447-449), called at 1000 Hz.
* **Gyro**: Hardware set to 2000 Hz ODR / 532 Hz BW (API/bmi088_driver.c:164). Software 3rd-order Butterworth LPF at 50 Hz: `Butterworth50HzLPF` (API/bmi088_driver.c:402-404), called at 1000 Hz.
* **Rate Feedback Filter**: 2nd-order Butterworth (Direct-Form-II) at 40 Hz: `GYRO_FILT_DEFAULT_FC   40.0f` (API/gyro_filter.c:16), disabled by default (pass-through).
* **Attitude (Mahony)**: Nominal `Kp = 0.5f` (API/imu_update.c:20), `Ki = 0.001f` (API/imu_update.c:21). Boosted at boot: `IMU_KP_BOOST 4.0f`, `IMU_KI_BOOST 0.02f` (API/imu_update.c:32-33).

## 3. PID structure
* **Update Equation** (Legacy mode): (API/pid.c:134-149)
  * P: `pPID->Up = pPID->Kp * pPID->E;`
  * I: `pPID->SumE += pPID->E;` (gated by `ABS(pPID->E) < pPID->EMin`). `pPID->Ui = pPID->Ki * pPID->SumE;`
  * D: `pPID->Ud = pPID->Kd * ( pPID->E - pPID->PreE );` (on error, no D filter).
  * Output: `pPID->U = pPID->Up + pPID->Ui + pPID->Ud;`, clamped by `UMax`.
* **Gains (Kp, Ki, Kd, UMax, SumEMax, EMin)**: (API/pid.c:10-24)
  * `pitchPID`/`rollPID`: `2.6`, `0.1`, `9.5`, UMax `200`, SumEMax `120`, EMin `3`
  * `yawPID`: `6.5`, `0.04`, `1.5`, UMax `160`, SumEMax `50`, EMin `2`
  * `gyroxPID`/`gyroyPID`: `5`, `0.01`, `10`, UMax `300`, SumEMax `1000`, EMin `2`
  * `gyrozPID`: `4.0`, `0.005`, `2.0`, UMax `650`, SumEMax `100000`, EMin `1000`
  * `Z_posPID`: `0.7`, `0.005`, `0.1`, UMax `1.0`, SumEMax `30`, EMin `0.3`
  * `Z_ratePID` (h rate): `400`, `0.435`, `1.5`, UMax `300`, SumEMax `30`, EMin `0.1`
  * `locxPID`/`locyPID`: `0.8`, `0.01`, `4.0`, UMax `300`, SumEMax `200`, EMin `30`
  * `locxsPID`/`locysPID`: `3.0`, `0`, `6.00`, UMax `600`, SumEMax `200`, EMin `10`

## 4. Cascade wiring
* **Z-axis**: `TWC.target_z` feeds `Z_posPID.Des` (rate-limited by `0.005f` per cycle, TASK/StabilizerTask.c:1202). `Z_posPID.U` feeds `Z_ratePID.Des` (TASK/StabilizerTask.c:1221).
* **XY-axis**: `TWC.target_x/y` feeds `locxPID.Des`/`locyPID.Des` (TASK/StabilizerTask.c:1243). `locx/yPID.U` feeds `locxs/ysPID.Des`, clamped to +/- `120.0f` (TASK/StabilizerTask.c:1252-1262).
* **Attitude**: `locxs/ysPID.U` (if OF-hold ON) rotated to body frame -> `des_pitch = -(Ctrler.locysPID.U)*Cos_Yaw_01 - (Ctrler.locxsPID.U)*Sin_Yaw_01`, `des_roll = -(Ctrler.locxsPID.U)*Cos_Yaw_01 + (Ctrler.locysPID.U)*Sin_Yaw_01` (TASK/StabilizerTask.c:1282-1283). `accel_to_lean_angles` yields `pitch/rollPID.Des`. If OF-hold OFF, pilot stick feeds `des_pitch/roll` directly.
* **Rate**: `pitch/rollPID.U` feeds `gyroy/xPID.Des` (TASK/StabilizerTask.c:1342-1343). `yawPID.U` feeds `gyrozPID.Des` (clamped to +/- `60.0f`, TASK/StabilizerTask.c:1347-1350).
* **Generators**: `AutoflyTask` provides `CirclePath_t`, `SinusoidPath_t`, `Figure8Path_t` (TASK/AutoflyTask.c:218-224), which discretize paths using `waypoint_spacing` (TASK/AutoflyTask.c:66).

## 5. Thrust and mixer
* **Hover throttle**: `bench_mode_active ? (short)3200 : (short)2950;` (TASK/StabilizerTask.c:1062).
* **Collective**: `Throttle_out = Controller_Update(CTRL_AXIS_Z, Ctrler.Z_ratePID.U) + Throttle_th;` (TASK/StabilizerTask.c:1066). No battery/tilt compensation in C.
* **Mixer** (TASK/StabilizerTask.c:1078-1087):
  * `M1 = Throttle_out - u_gyroy - u_gyrox + g_yaw_mix_dir * u_gyroz;` (CW)
  * `M2 = Throttle_out + u_gyroy + u_gyrox + g_yaw_mix_dir * u_gyroz;` (CW)
  * `M3 = Throttle_out - u_gyroy + u_gyrox - g_yaw_mix_dir * u_gyroz;` (CCW)
  * `M4 = Throttle_out + u_gyroy - u_gyrox - g_yaw_mix_dir * u_gyroz;` (CCW)
* **Yaw mix direction**: `g_yaw_mix_dir` defaults to `-1` (TASK/StabilizerTask.c:1088).
* **Clamps/PWM**: `Throttle_out` constrained by `2000.0f + gs_throttle_min_pct * 2000.0f` to `max_pct` (TASK/StabilizerTask.c:1074). `Set_PWM_Motors()` enforces `[2000,4000]` clamp (TASK/StabilizerTask.c:658). Idle defaults to zeroing motors or `Set_IDLE_Motors()` (TASK/StabilizerTask.c:764).

## 6. MRAC
* **Reference model**: 2nd-order configuration available (e.g. `ref_model_bw = 44.0f`, `ref_model_zeta = 0.8f`, API/mrac.c:499). Default is `DEFAULT_REF_MODEL_TYPE 0` (passthrough, API/mrac.h:62).
* **Regressor (Phi)**: 6 features since `INCLUDE_CONTROL_IN_REGRESSOR == 1` (API/mrac.c:91-109): `1.0f` (Bias), `x` (Damping), `x * tanhf(x)` (Drag), cross-coupling/0, `u_nom`, `xm`.
* **Adaptation law**: `grad[i] = (-s * state->Phi[i]) / denom;` (API/mrac.c:297).
* **Parameters**: `sigma = 0.01f`, `e_deadzone = 0.05f` (API/mrac.c:515-516), e-mod proportional to error `k_e = 0.05f` (API/mrac.c:509). `What_limit` bounds and `What_lower_limit` (API/mrac.c:487).
* **Controller_Update**: `float Controller_Update(uint8_t axis, float u_nom)` (API/controller.h:35). Returns `u_nom + correction`. Called in `Compute_Motor` for Z, ROLL, PITCH, YAW.
* **Status**: Shadow mode default `mrac_flags.output_injection_on = 0;` (API/mrac.c:587).

## 7. EKF
* **9-state (Ekf9)**: `x[9]` = `[v_body(3), b_a_body(3), b_g_body(3)]` (API/ekf.h:21).
  * Measurements: OF (`Ekf9_UpdateOf`), Z-rate (`Ekf9_UpdateZRate`), Accel (`Ekf9_UpdateAccXY`, API/ekf.c:243).
  * Noise: `Q_v = 1e-3f`, `Q_ba = EKF_Q_BA` (1e-6), `Q_bg = EKF_Q_BG` (5e-9) (API/ekf.c:44-46).
  * Controllers use `g_ekf_gate.vx_cms` / `vy_cms` for `locxsPID.FB` (TASK/StabilizerTask.c:517).
* **6-state (EkfOf)**: `x[6]` = `[pos_x, vel_x, bias_x, pos_y, vel_y, bias_y]` (API/ekf_of.h:17).
  * Measurements: OF (`EkfOf_Update`).
  * Noise: `Q_pos = 1e-6f`, `Q_vel = 2e-4f`, `Q_bias = 5e-5f` (API/ekf_of.c:57-59).
  * Active if `g_of_bias_mode == 2U` (TASK/StabilizerTask.c:416). Used for X/Y position FB.

## 8. Delays
* **Control Lag**: Pitch pole lag `~2.6Hz`, delay `~12ms`; Roll pole `~3.2Hz`, delay `~15ms` identified (API/mrac.c:500, 519).
* **SINS/GPS Buffer**: `LocXY_SINS_Delay_Cnt=4` history lookup for OF/GPS fusion (API/SINS.c:149).
* **Telemetry**: UART5 DMA frame delays causing >5ms blocking when logging (TASK/send_data.c:725).

## 9. Firmware gaps
* **Accel measurement**: `Ekf9_UpdateAccXY` deliberately unused (`NO accelerometer measurement update — deliberate, do not re-add`, TASK/send_data.c:753).
* **Yaw MRAC model**: `ref_model_bw = 30.0f` is `PROVISIONAL. ... unvalidated; needs a yaw closed-loop BW measurement` (API/mrac.c:536).
* **Gyro low-pass filter**: Pass-through by default, must be enabled via CMD 0x15 (TASK/StabilizerTask.c:618).

END-OF-INVENTORY
