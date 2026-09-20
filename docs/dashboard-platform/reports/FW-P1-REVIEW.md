# Firmware P1 Defect Review Report

**Date:** 2026-09-20  
**Target:** STM32F407 FreeRTOS Adaptive Flight Controller Firmware  
**Scope:** P1 Items 16–32 from `FW-FLIGHT-AUDIT-2026-09-19.md`  
**Mode:** REPORT ONLY — No source files modified, no builds executed  
**Reference Toolchain:** Keil ARMCC V5.06 (C89 / C90 standard)  

---

## 1. Executive Summary

A rigorous, line-by-line verification pass was conducted on all 17 **P1 (Major / Degraded Flight)** findings reported in the firmware flight-safety audit (`docs/dashboard-platform/reports/FW-FLIGHT-AUDIT-2026-09-19.md`).

### Summary Metrics:
- **Total P1 Findings Audited:** 17 (Items 16 through 32)
- **Confirmed Defects:** 17 (100%)
- **False Positives:** 0 (0%)
- **Risk Assessment:**
  - **LOW Risk:** 15 items (Items 17, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32)
    *Definition: Local and obviously correct; cannot change the flight behaviour of a healthy system.*
  - **HIGH Risk:** 2 items (Items 16, 18)
    *Definition: Alters flight dynamics, control mixing, or sensor failsafe fallback policies; requires flight tuning/operator decision.*

---

## 2. P1 Findings Review Matrix

| Item | File:Line | Description / Issue | Verdict | Risk | Proposed Minimal Fix (C89/ARMCC V5) |
|---|---|---|---|---|---|
| **16** | [Ano_OF.c:6-48](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/Ano_OF.c#L6-L48)<br>[StabilizerTask.c:254-340](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/StabilizerTask.c#L254-L340) | **Optical Flow Timeout / Disconnection Undetected:** `AnoOF_Check_State()` is dead code (never called). If the OF sensor is disconnected or hangs, `ano_of.of_quality` and `of2_dx_fix` freeze, causing continuous drift integration in position hold. | **CONFIRMED** | **HIGH** | Call `AnoOF_Check_State(0.005f)` in `Stabilizer_Task` and define an explicit fallback policy (e.g. drop to attitude mode `g_of_hold_active = 0` if `ano_of.work_sta == 0`). *High risk: alters mode logic mid-flight.* |
| **17** | [Ano_OF.c:144-153](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/Ano_OF.c#L144-L153) | **Mode 2 Optical Flow Frame Missing Timer Reset & Update Counter:** In `AnoOF_DataAnl()`, Mode 2 frames (`*(data+4) == 2`) parse data but omit `check_time_ms[1] = 0;` and `ano_of.of_update_cnt++;`. If `AnoOF_Check_State` were called, `work_sta` would falsely stay 0. | **CONFIRMED** | **LOW** | In `API/Ano_OF.c` inside the `*(data + 4) == 2` branch (line 152), add:<br>`check_time_ms[1] = 0;`<br>`ano_of.of_update_cnt++;` |
| **18** | [pwm.c:273-284](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/pwm.c#L273-L284) | **Independent Motor PWM Saturation Distorts Attitude Moments:** `Set_PWM_Motors()` independently clamps each motor to `[2000, 4000]`. If one motor saturates during high throttle or aggressive roll/pitch commands, moment balance is lost, causing uncommanded yaw/pitch. | **CONFIRMED** | **HIGH** | Implement mixer desaturation logic before independent clipping (e.g., subtract `M_max - 4000` from all motors to preserve differential torque). *High risk: changes motor mixing and flight dynamics near saturation limits.* |
| **19** | [mrac.c:501-525](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/mrac.c#L501-L525)<br>[StabilizerTask.c:638](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/StabilizerTask.c#L638)<br>[pid.c:244](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/pid.c#L244) | **`MRAC_Reset()` Never Called on Disarm / Mode Switch:** `MRAC_Reset()` is only invoked in `MRAC_Init()` at boot. While disarmed on the bench, `StabilizerTask` continues updating MRAC at 200 Hz, accumulating sensor noise into weights $\Theta$. | **CONFIRMED** | **LOW** | Call `MRAC_Reset();` inside `Clear_Structure()` in `API/pid.c:270` (which is already called in `StabilizerTask.c` on disarmed/emergency ticks). Ensures clean zero weights at arming. |
| **20** | [StabilizerTask.c:676-685](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/StabilizerTask.c#L676-L685) | **Z-Axis Position & Rate PIDs Run While Disarmed:** In `Compute_Motor()`, `Z_posPID` and `Z_ratePID` execute unconditionally every tick even when disarmed (`ARM_Status == Disarmed`), whereas horizontal PIDs are gated on `ARM_Status != 0U`. Spurious `SumE` accumulates and creates transient motor jumps at arming. | **CONFIRMED** | **LOW** | In `TASK/StabilizerTask.c:676`, wrap the height PID computation in `if (DroneStatus.ARM_Status != 0U) { ... }`, exactly matching the locx/y gating at line 747. |
| **21** | [global_declare.h:28](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/Global_file/global_declare.h#L28) | **Dangling `else` Bug in `value_limit` Macro:** Macro lacks parentheses and `do { ... } while(0)` block: `#define value_limit(x,small,big) if(x<small)x=small;if(x>big)x=big;`. If used in `if (...) value_limit(...); else ...`, the `else` binds incorrectly to the second `if`. | **CONFIRMED** | **LOW** | In `Global_file/global_declare.h:28`, redefine as:<br>`#define value_limit(x,small,big) do { \`<br>`  if((x)<(small)) (x)=(small); \`<br>`  else if((x)>(big)) (x)=(big); \`<br>`} while(0)` |
| **22** | [pid.c:125-129](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/pid.c#L125-L129) | **`AW_LEGACY` Defeated at Exact Saturation Boundary:** Condition `if (((pPID->U <= pPID->UMax && pPID->E > 0) ...))` uses `<=` and `>=`. Because `U` is clamped to `[-UMax, UMax]`, `U <= UMax` is always true, so error continues accumulating into `SumE` at saturation ceiling. | **CONFIRMED** | **LOW** | In `API/pid.c:125`, replace `<=` and `>=` with strict `<` and `>`:<br>`if (((pPID->U < pPID->UMax && pPID->E > 0) \|\| (pPID->U > -pPID->UMax && pPID->E < 0)) && ABS(pPID->E) < pPID->EMin)` |
| **23** | [usart4.c:32,136-145](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/usart4.c#L32)<br>[usart5.c:76,363-372](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/usart5.c#L76)<br>[usart5.c:413-422](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/usart5.c#L413-L422) | **Unsynchronized Multi-UART ISR Contention on `gs_cmd_queue`:** `UART4_IRQn` preemption priority is 0, while `UART5_IRQn` and `USART3_IRQn` are 5. UART4 can preempt UART5/USART3 mid-enqueue, corrupting `gs_cmd_head` and dropped command counts. | **CONFIRMED** | **LOW** | In `BSP/usart4.c:32`, change preemption priority to 5 (matching UART5/USART3 and `configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY`), and wrap `gs_cmd_queue` operations in `taskENTER_CRITICAL_FROM_ISR()` / `taskEXIT_CRITICAL_FROM_ISR()`. |
| **24** | [gyro_filter.c:72](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/gyro_filter.c#L72)<br>[send_data.c:1787-1789](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L1787-L1789) | **Non-Atomic Live Biquad Cutoff Update Can Cause Filter Explosion:** CMD 0x15 idx 1 triggers `GyroFilter_SetCutoff()` from `Send_Task` (prio 2), rewriting 5 biquad coefficients in-place. `Stabilizer_Task` (prio 4) can preempt mid-write, executing with mismatched numerator/denominator poles outside unit circle ($|z| \ge 1$). | **CONFIRMED** | **LOW** | In `API/gyro_filter.c:72`, wrap `biquad_design(&s_filt[axis], fc_hz);` in `taskENTER_CRITICAL()` and `taskEXIT_CRITICAL()`. |
| **25** | [ADC.c:49-65](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/USER/ADC.c#L49-L65)<br>[main.c:238](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/USER/main.c#L238)<br>[send_data.c:794](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L794) | **Unsynchronized Concurrent Access to ADC1 Hardware:** `ADC_Read()` is invoked from both `SystemMonitor_Task` (1 Hz, prio 1) and `Send_Task` (100 Hz, prio 2, bench frame). Preemption during conversion causes interleaved start/status commands and false `eoc_timeout` triggers. | **CONFIRMED** | **LOW** | In `USER/ADC.c:49`, wrap the conversion sequence in `ADC_Read()` with `taskENTER_CRITICAL()` / `taskEXIT_CRITICAL()`. |
| **26** | [usart5.c:161-163](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/usart5.c#L161-L163) | **Unbounded DMA Busy-Wait in `Uart5_Subscribe_TxSend()` Stalls `Send_Task`:** `while (DMA_GetCurrDataCounter(DMA1_Stream7));` spins until DMA transfer completes. *(Note: in current build `SUBSCRIBE_UART5_ENABLED` is 0, so stub is dormant, but present if compiled).* | **CONFIRMED** | **LOW** | In `BSP/usart5.c:161`, bound the polling loop with a cycle counter timeout: `uint32_t to = 50000U; while (DMA_GetCurrDataCounter(DMA1_Stream7) && --to); if (!to) return;`. |
| **27** | [StabilizerTask.c:163-164](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/StabilizerTask.c#L163-L164)<br>[send_data.c:1799](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L1799) | **`Reset_World_Origin()` Mid-Flight Erases Calibrated OF Velocity Biases:** `Reset_World_Origin()` explicitly zeroes `s_of_bias_x` and `s_of_bias_y`. When triggered in flight via CMD 0x10, calibrated sensor velocity biases are lost, injecting raw sensor DC drift directly into position integration. | **CONFIRMED** | **LOW** | In `TASK/StabilizerTask.c:163-164`, remove `s_of_bias_x = 0.0f; s_of_bias_y = 0.0f;` from `Reset_World_Origin()`. Origin reset must only zero position coordinates. |
| **28** | [stm32f4xx_it.c:225-243](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/stm32f4xx_it.c#L225-L243) | **Spurious Zero-Byte IDLE Interrupt Generates Phantom Full-Buffer Packet:** In `USART_Receive()`, when `rxBufferPtr == rxConter` (no new data), code enters `else` branch, computing `rxSize = DMALen`. It copies entire stale DMA buffer and returns `rxSize = DMALen`, causing spurious command processing. | **CONFIRMED** | **LOW** | In `TASK/stm32f4xx_it.c:225`, add early exit:<br>`if (USARTx->rxBufferPtr == USARTx->rxConter) { USARTx->rxSize = 0U; return 0U; }` |
| **29** | [Ano_OF.c:157](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/Ano_OF.c#L157) | **Unaligned 32-Bit Pointer Dereference `*((u32 *)(data + 7))`:** `data + 7` is an odd address. Dereferencing an unaligned `u32*` is Undefined Behavior in C and will trigger a `UsageFault` on Cortex-M4 if `CCR.UNALIGN_TRP` is enabled. | **CONFIRMED** | **LOW** | In `API/Ano_OF.c:157`, assemble `of_alt_cm` safely with endian-correct byte shifts:<br>`ano_of.of_alt_cm = ((uint32_t)data[7]) \| ((uint32_t)data[8] << 8) \| ((uint32_t)data[9] << 16) \| ((uint32_t)data[10] << 24);` |
| **30** | [send_data.c:1513](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L1513)<br>[mrac.c:572-575](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/mrac.c#L572-L575) | **CMD 0x03 Permits `mrac_to_mixer = 0.0f` (Divide-By-Zero):** In `Process_GroundStation_Command()`, CMD 0x03 indices 0..3 assign `configs[idx]->mrac_to_mixer = val` without bounds check. Sending 0 causes divide-by-zero on next 200 Hz tick in `MRAC_Control()` (`u_nom = gyroyPID.U / 0.0f`), injecting Inf/NaN into MRAC. | **CONFIRMED** | **LOW** | In `TASK/send_data.c:1513`, validate parameter value:<br>`if (idx < 4) { if (val > 1.0f) configs[idx]->mrac_to_mixer = val; }` |
| **31** | [send_data.c:328-331](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L328-L331) | **Missing DMA Stream Enable-Status Check Before Reconfiguring `DMA1_Stream4`:** In `send_to_linux()`, `DMA1_Stream4` is disabled and `NDTR` is written immediately without polling `DMA_GetCmdStatus(DMA1_Stream4) == DISABLE`. Per STM32 RM0090 §10.3.17, writing `NDTR` while `EN == 1` is ignored by hardware, causing dropped telemetry frames. | **CONFIRMED** | **LOW** | In `TASK/send_data.c:328`, wait for stream to disable before writing registers:<br>`DMA_Cmd(DMA1_Stream4, DISABLE);`<br>`while (DMA_GetCmdStatus(DMA1_Stream4) == ENABLE);`<br>`DMA1_Stream4->M0AR = ...;` |
| **32** | [send_data.c:1519](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L1519)<br>[StabilizerTask.c:838-844](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/StabilizerTask.c#L838-L844) | **CMD 0x03 Allows Setting Zero Throttle Ceiling (`gs_throttle_max_pct`):** Telemetry handler allows `gs_throttle_max_pct = 0.0f`. `StabilizerTask.c:838` then constrains `Throttle_out` to `[2000, 2000]` (idle throttle), causing an instant in-flight motor cutoff and freefall plunge. | **CONFIRMED** | **LOW** | In `TASK/send_data.c:1519`, enforce safe minimum ceiling:<br>`else if (idx == 9) { if (val >= 0.50f && val <= 1.0f) gs_throttle_max_pct = val; }` |

---

## 3. Shadow EKF Architecture & Mode 2 Control Leak Investigation

### Specific Verification Question:
> *Can `s_ekf` output reach any control path when the flight mode is 2 (grep `s_ekf` in `TASK/` `API/`; list every reader outside `ekf*.c`)?*

### Detailed Trace & Exhaustive Symbol Analysis:

1. **Definition and Scope of `s_ekf`:**
   - Declared in [TASK/send_data.c:57](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L57):
     ```c
     static Ekf9_t s_ekf; /* ADR-0011 parallel EKF instance */
     static uint8_t s_ekf_inited = 0U;
     ```
   - It is a **file-scope static variable** inside `TASK/send_data.c`. It is NOT exported in any header file and has no `extern` declaration anywhere in the codebase.

2. **Every Reader of `s_ekf` Outside `ekf*.c`:**
   An exhaustive grep across `TASK/`, `API/`, `BSP/`, `USER/`, and `Global_file/` yields exactly the following references to `s_ekf`:
   - [TASK/send_data.c:692-696](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L692-L696): Initialized via `Ekf9_Init(&s_ekf, EKF_RUN_ENABLED)` and gated via `if (s_ekf.active)`.
   - [TASK/send_data.c:726](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L726): `Ekf9_Predict(&s_ekf, ax, ay, az, Gyro_X_Real, Gyro_Y_Real, Gyro_Z_Real, dt);`
   - [TASK/send_data.c:738](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L738): `Ekf9_UpdateOf(&s_ekf, ofx, ofy);`
   - [TASK/send_data.c:755](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L755): `Ekf9_UpdateZRate(&s_ekf, ano_of.of2_h_f2_v);`
   - **[TASK/send_data.c:999-1008](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L999-L1008)** (**The ONLY read site of state estimates**):
     Inside `Send_Groundstation_Telemetry_UART4()`:
     ```c
     _v = (int16_t)(s_ekf.x[0] * 1000.0f); Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
     _v = (int16_t)(s_ekf.x[1] * 1000.0f); Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
     _v = (int16_t)(s_ekf.x[2] * 1000.0f); Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
     _v = (int16_t)(s_ekf.P[0 * 9U + 0U] * 1e3f); Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
     _v = (int16_t)(s_ekf.P[1 * 9U + 1U] * 1e3f); Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
     _v = (int16_t)(s_ekf.P[2 * 9U + 2U] * 1e3f); Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
     _v = (int16_t)(s_ekf.nis * 1e3f);         Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
     _v = (int16_t)(s_ekf.k_last[0] * 1e3f);  Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
     _v = (int16_t)(s_ekf.k_last[1] * 1e3f);  Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
     _v = (int16_t)(s_ekf.k_last[2] * 1e3f);  Buf_Telemetry_UART4[len++] = BYTE0(_v); Buf_Telemetry_UART4[len++] = BYTE1(_v);
     ```
   - [TASK/send_data.c:1862](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L1862): Re-initialized on CMD 0x0E (ground station arm/disarm command).

3. **Conclusion on `s_ekf`:**
   **`s_ekf` CANNOT reach any control path under any flight mode (including Mode 2).**  
   It is strictly confined to `send_data.c` for telemetry packing over UART4.

4. **The Actual Mode 2 Control Path — `s_ekf_of`:**
   What *does* connect to the control loop in Mode 2 is a separate filter instance: `s_ekf_of` (a 6-state optical flow Kalman filter defined in `TASK/StabilizerTask.c:111`):
   - In [TASK/StabilizerTask.c:312-323](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/StabilizerTask.c#L312-L323):
     ```c
     if (g_of_bias_mode == 2U) {
         float ekf_px = s_ekf_of.x[0];
         float ekf_py = s_ekf_of.x[3];
         ano_of.earth_x = ekf_px * Cos_Yaw_01 + ekf_py * Sin_Yaw_01;
         ano_of.earth_y = ekf_py * Cos_Yaw_01 - ekf_px * Sin_Yaw_01;
         ano_of.earth_x_ture =  ano_of.earth_y;
         ano_of.earth_y_ture = -ano_of.earth_x;
         Ctrler.locxPID.FB = ano_of.earth_x_ture;
         Ctrler.locyPID.FB = ano_of.earth_y_ture;
     }
     ```
   - As identified in P0 Finding 6, `s_ekf_of.x[0]` and `s_ekf_of.x[3]` are in **meters**, but `Ctrler.locxPID.FB` and `locyPID.FB` require **centimeters**, resulting in a 100x attenuation in position feedback gain.
   - Therefore, the operator must be aware that while the 9-state EKF (`s_ekf`) is completely safe and isolated in shadow telemetry, the 6-state OF filter (`s_ekf_of`) is wired directly into `locxPID.FB` and `locyPID.FB` when `g_of_bias_mode == 2U`.

---

## 4. Item-by-Item Review Details & Evidence

### Item 16: Optical Flow Timeout / Disconnection Never Detected
- **Files:** [API/Ano_OF.c:6-48](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/Ano_OF.c#L6-L48), [TASK/StabilizerTask.c:254-340](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/StabilizerTask.c#L254-L340)
- **Verdict:** CONFIRMED
- **Risk:** HIGH
- **Analysis:** `AnoOF_Check_State()` is never called in any task (verified by map file: `Removing ano_of.o(i.AnoOF_Check_State), (192 bytes)`). In `Stabilizer_Task`, `of_ok` is evaluated purely on `ano_of.of_quality >= OF_MIN_QUALITY`. If the optical flow cable disconnects or sensor crashes mid-flight, quality and velocity hold their last values indefinitely. Position integration runs away. However, fixing this requires introducing an optical-flow failsafe policy (e.g. falling back to angle mode, triggering landing, or disengaging position hold). If the timeout is too strict or noisy, it could spuriously disengage position hold in healthy flight. User decision required.

### Item 17: Mode 2 Optical Flow Frame Missing Timeout Pet and Update Counter Increment
- **Files:** [API/Ano_OF.c:144-153](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/Ano_OF.c#L144-L153)
- **Verdict:** CONFIRMED
- **Risk:** LOW
- **Analysis:** In `AnoOF_DataAnl()`, Mode 1 (`*(data+4) == 1`) executes `check_time_ms[1] = 0; ano_of.of_update_cnt++;`. Mode 2 (`*(data+4) == 2`, which the firmware actually runs) omits this reset. If `AnoOF_Check_State` were activated, `check_time_ms[1]` would exceed 500 and falsely flag `ano_of.work_sta = 0`. Resetting `check_time_ms[1]` and incrementing `of_update_cnt` in Mode 2 is purely diagnostic bookkeeping and cannot alter healthy flight control.

### Item 18: Independent Motor PWM Saturation Clipping Distorts Attitude Moments
- **Files:** [BSP/pwm.c:273-284](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/pwm.c#L273-L284)
- **Verdict:** CONFIRMED
- **Risk:** HIGH
- **Analysis:** `Set_PWM_Motors()` applies independent `value_limit` clamping to M1, M2, M3, M4. When one motor saturates at 4000 while others remain within bounds, the actuator mixer loses differential attitude moments. Implementing mixer desaturation (e.g. collective throttle reduction or torque prioritization) directly affects flight dynamics during high-throttle maneuvers and requires flight envelope testing.

### Item 19: `MRAC_Reset()` Never Called on Arm / Disarm Transitions
- **Files:** [API/mrac.c:501-525](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/mrac.c#L501-L525), [TASK/StabilizerTask.c:638](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/StabilizerTask.c#L638), [API/pid.c:244](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/pid.c#L244)
- **Verdict:** CONFIRMED
- **Risk:** LOW
- **Analysis:** `MRAC_Reset()` is only invoked at boot in `MRAC_Init()`. `StabilizerTask` calls `MRAC_Control()` at 200 Hz continuously while disarmed, allowing gyro noise to drift adaptive weights $\Theta$ before takeoff. Calling `MRAC_Reset()` in `Clear_Structure()` ensures that whenever the drone is disarmed or in emergency stop, weights and reference models are kept clean at zero. In flight, adaptive weights learn from zero as designed.

### Item 20: Z-Axis Position and Rate PIDs Run Continuously in Disarmed State
- **Files:** [TASK/StabilizerTask.c:676-685](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/StabilizerTask.c#L676-L685)
- **Verdict:** CONFIRMED
- **Risk:** LOW
- **Analysis:** Horizontal `locx/y` PIDs were gated with `if (DroneStatus.ARM_Status != 0U)` in a previous fix (line 747), but height loops `Z_posPID` and `Z_ratePID` (lines 676-685) were left ungated. They accumulate error and integrate `SumE` every tick on the ground. Wrapping lines 676-685 in `if (DroneStatus.ARM_Status != 0U)` ensures height PIDs only run when armed.

### Item 21: Dangling `else` Bug in `value_limit` Macro
- **Files:** [Global_file/global_declare.h:28](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/Global_file/global_declare.h#L28)
- **Verdict:** CONFIRMED
- **Risk:** LOW
- **Analysis:** `#define value_limit(x,small,big) if(x<small)x=small;if(x>big)x=big;` contains two separate statements and unparenthesized arguments. Redefining as a standard `do { if((x)<(small)) (x)=(small); else if((x)>(big)) (x)=(big); } while(0)` provides syntactic isolation with identical mathematical behavior.

### Item 22: `AW_LEGACY` Accumulates Error at Exact Saturation Boundary
- **Files:** [API/pid.c:125-129](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/pid.c#L125-L129)
- **Verdict:** CONFIRMED
- **Risk:** LOW
- **Analysis:** In `pid.c`, line 141 clamps `pPID->U` to `[-UMax, UMax]`. Because `U` never exceeds `UMax`, the condition `pPID->U <= pPID->UMax` is identically true for all non-negative `U`. Hence, anti-windup clamping never stops integration at saturation. Changing `<=` and `>=` to strict `<` and `>` halts integration when saturated at the limit. In unsaturated linear flight, behavior is identical.

### Item 23: Unsynchronized Multi-UART ISR Contention on `gs_cmd_queue`
- **Files:** [BSP/usart4.c:32](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/usart4.c#L32), [BSP/usart5.c:76,363](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/usart5.c#L76)
- **Verdict:** CONFIRMED
- **Risk:** LOW
- **Analysis:** `UART4_IRQn` preemption priority is 0, while `UART5_IRQn` / `USART3_IRQn` are 5. UART4 can preempt UART5/USART3 mid-enqueue into `gs_cmd_queue`, corrupting `gs_cmd_head`. Setting UART4 preemption priority to 5 and protecting queue updates with `taskENTER_CRITICAL_FROM_ISR()` prevents race conditions without affecting flight controls.

### Item 24: Non-Atomic Live Biquad Cutoff Update Can Induce Filter Instability
- **Files:** [API/gyro_filter.c:72](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/gyro_filter.c#L72), [TASK/send_data.c:1787-1789](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L1787-L1789)
- **Verdict:** CONFIRMED
- **Risk:** LOW
- **Analysis:** CMD 0x15 triggers `GyroFilter_SetCutoff()` in `Send_Task` (prio 2), rewriting 5 biquad coefficients in-place. `Stabilizer_Task` (prio 4) preempts mid-write and computes with inconsistent poles. Wrapping `biquad_design()` in `taskENTER_CRITICAL()` / `taskEXIT_CRITICAL()` ensures atomic updates.

### Item 25: Unsynchronized Concurrent Access to ADC1 Hardware
- **Files:** [USER/ADC.c:49-65](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/USER/ADC.c#L49-L65), [USER/main.c:238](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/USER/main.c#L238), [TASK/send_data.c:794](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L794)
- **Verdict:** CONFIRMED
- **Risk:** LOW
- **Analysis:** `ADC_Read()` starts software conversion and polls `ADC_FLAG_EOC`. It is called concurrently by `SystemMonitor_Task` (1 Hz, prio 1) and `Send_Task` (100 Hz, prio 2). Interleaved execution corrupts readings. Wrapping `ADC_Read()` in a critical section prevents collision.

### Item 26: Unbounded DMA Busy-Wait in `Uart5_Subscribe_TxSend()` Stalls `Send_Task`
- **Files:** [BSP/usart5.c:161-163](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/usart5.c#L161-L163)
- **Verdict:** CONFIRMED
- **Risk:** LOW
- **Analysis:** `while (DMA_GetCurrDataCounter(DMA1_Stream7));` busy-waits up to 26 ms at 115200 baud. Adding a loop timeout iteration bound ensures deterministic task execution. (Currently dormant stub under `SUBSCRIBE_UART5_ENABLED = 0`, but clean fix).

### Item 27: `Reset_World_Origin()` Mid-Flight Erases Calibrated OF Velocity Biases
- **Files:** [TASK/StabilizerTask.c:163-164](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/StabilizerTask.c#L163-L164), [TASK/send_data.c:1799](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L1799)
- **Verdict:** CONFIRMED
- **Risk:** LOW
- **Analysis:** `Reset_World_Origin()` zeroes `s_of_bias_x` and `s_of_bias_y`. In flight, CMD 0x10 is intended to reset position coordinates `earth_x/y = 0`, not destroy velocity calibration. Removing lines 163-164 preserves velocity calibration.

### Item 28: Spurious Zero-Byte IDLE Interrupt Generates Phantom Full-Buffer Packet in `USART_Receive`
- **Files:** [TASK/stm32f4xx_it.c:225-243](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/stm32f4xx_it.c#L225-L243)
- **Verdict:** CONFIRMED
- **Risk:** LOW
- **Analysis:** When `rxBufferPtr == rxConter`, 0 bytes were received. The code currently falls into `else` and evaluates `rxSize = rxConter + DMALen - rxBufferPtr = DMALen`, copying the entire circular DMA buffer and triggering spurious parsing. Adding `if (USARTx->rxBufferPtr == USARTx->rxConter) return 0U;` cleanly solves this.

### Item 29: Unaligned 32-Bit Load `*((u32 *)(data + 7))` in `AnoOF_DataAnl`
- **Files:** [API/Ano_OF.c:157](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/Ano_OF.c#L157)
- **Verdict:** CONFIRMED
- **Risk:** LOW
- **Analysis:** `data + 7` is an unaligned odd memory address. Dereferencing as `u32*` violates alignment rules. Safe reconstruction via `(uint32_t)data[7] | ((uint32_t)data[8] << 8) | ...` is fully portable and eliminates `UsageFault` risk.

### Item 30: CMD 0x03 Permits `mrac_to_mixer = 0.0f` (Divide-By-Zero)
- **Files:** [TASK/send_data.c:1513](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L1513), [API/mrac.c:572-575](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/mrac.c#L572-L575)
- **Verdict:** CONFIRMED
- **Risk:** LOW
- **Analysis:** In `send_data.c:1513`, CMD 0x03 assigns `configs[idx]->mrac_to_mixer = val` without checking `val > 0`. Setting 0 causes float division by zero in `MRAC_Control()`. Adding `if (val > 1.0f)` prevents corruption while allowing legitimate gain updates.

### Item 31: Missing DMA Enable-Status Check Before Reconfiguring `DMA1_Stream4`
- **Files:** [TASK/send_data.c:328-331](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L328-L331)
- **Verdict:** CONFIRMED
- **Risk:** LOW
- **Analysis:** Per STM32 RM0090 §10.3.17, writing `NDTR` while `EN == 1` has no effect. Disabling the stream requires polling `while (DMA_GetCmdStatus(DMA1_Stream4) == ENABLE);` before updating `NDTR`.

### Item 32: CMD 0x03 Allows Setting Zero Throttle Ceiling (`gs_throttle_max_pct`)
- **Files:** [TASK/send_data.c:1519](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L1519), [TASK/StabilizerTask.c:838-844](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/StabilizerTask.c#L838-L844)
- **Verdict:** CONFIRMED
- **Risk:** LOW
- **Analysis:** Setting `gs_throttle_max_pct = 0.0f` pins `Throttle_out` to 2000 (idle throttle), cutting power in mid-air. Adding a lower bound `if (val >= 0.50f && val <= 1.0f)` prevents accidental cutoff while permitting normal throttle ceiling adjustments.

---

## 5. Recommendation & Next Steps

1. **Apply the 15 LOW-Risk Fixes as a Batch:**
   Items 17, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32 are all local, mathematically sound, eliminate severe edge-case bugs (divide-by-zero, unaligned faults, spurious packets, race conditions, integrator drift), and do not alter nominal flight handling.
2. **Operator Decisions on the 2 HIGH-Risk Items:**
   - **Item 16 (OF Failsafe):** Operator must decide desired failsafe behavior when optical flow signal drops (switch to manual angle mode vs auto-land).
   - **Item 18 (PWM Mixer Desaturation):** Operator must decide torque prioritization policy (e.g. collective throttle reduction vs roll/pitch priority over yaw) and validate in ground rig.
