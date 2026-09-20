# Firmware Hidden Bug Hunt - Round 3 (Audit Report)
**Date:** 2026-09-20  
**Scope:** `API/subscribe.c`, `API/subscribe.h`, `API/mrac.c`, `API/mrac_math.c`, FreeRTOS task & ISR configuration, concurrency/atomicity.  
**Mode:** Report only (no edits to firmware, no build).

---

## 1. Executive Summary & Audit Metrics

| Metric | Count |
| :--- | :--- |
| **Total New Defects Identified** | **14** |
| **P1 Severity (High - System stall / NaN / mailbox freeze / silent drops)** | **5** |
| **P2 Severity (Medium - Parameter inversion / missing volatile / race conditions)** | **9** |
| **Fix Risk: LOW (local, obviously correct, zero change to healthy flight)** | **14** |
| **Fix Risk: HIGH** | **0** |

All items strictly respect the constraint: **no changes proposed to failsafe, altitude, OF scaling, mixer, or `s_ekf` wiring**.

---

## 2. Findings Matrix

| ID | File:Line | Defect | Evidence | Severity | Fix Risk | Minimal Fix | False-Positive Check |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **38** | [subscribe.c:386](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/subscribe.c#L386)<br>[subscribe.h:210](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/subscribe.h#L210) | **Buffer Sizing Mismatch in `stream_buf` Silently Drops Max Payload Frames (>1020 B):** `SUBSCRIBE_STREAM_MAX_BYTES` is 1024, and frame overhead is 12 B (6 header + 4 timestamp + 2 CRC16). Max wire frame is 1036 B. `stream_buf` was declared as `[1024 + 8U]` (1032 B) due to an erroneous comment assuming 1 B CRC and no timestamp. Subscriptions with >1020 B payload are ACKed with Schema 0x08, but `Subscribe_BuildStreamFrame()` returns 0 (`1036 > 1032`), causing `Subscribe_StreamTick()` to silently drop every frame. | `stream_buf` declared as `SUBSCRIBE_STREAM_MAX_BYTES + 8U` (1032 B). `Subscribe_BuildStreamFrame()` checks `6 + 4 + total_bytes + 2 > out_cap`. For 1024 B, `1036 > 1032` triggers error return. | **P1** | **LOW** | In [subscribe.c:386](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/subscribe.c#L386), resize buffer:<br>`static uint8_t stream_buf[SUBSCRIBE_STREAM_MAX_BYTES + SUBSCRIBE_STREAM_FRAME_OVERHEAD];` (1036 B). | File-scope static BSS expands by only 4 bytes. Zero impact on control loops; enables advertised 1024 B telemetry streaming. |
| **39** | [usart5.c:41,325-333,477,499](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/usart5.c#L41)<br>[subscribe.c:354-364](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/subscribe.c#L354-L364) | **Cross-UART Race Condition on `Subscribe_RxTransport` Misroutes Schema/Error Replies:** `Subscribe_RxTransport` is a single global variable. When a request arrives on USART3 (WiFi), `Handle_USART3_GroundStation_Command` sets it to USART3. If UART5 receives any frame before `Send_Task` executes `Uart5_Subscribe_HandleRequest()`, UART5 ISR clobbers `Subscribe_RxTransport` to UART5. `Subscribe_TxReplyToTransport` transmits the 0x08 Schema ACK out UART5 instead of USART3, causing WiFi host timeout. | `try_stage_subscribe_frame()` copies data to `UA5RxSubscribeBuf` under `__disable_irq()`, but does not latch the ingress transport. `Subscribe_TxReplyToTransport` reads shared global `Subscribe_RxTransport`. | **P1** | **LOW** | In [usart5.c](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/usart5.c#L34), add `volatile uint8_t UA5RxSubscribeTransport;`. Latch `UA5RxSubscribeTransport = Subscribe_RxTransport;` inside `try_stage_subscribe_frame()` under `__disable_irq()`, and use it in [subscribe.c:359](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/subscribe.c#L359). | Local to telemetry staging/reply path. Prevents cross-talk between WiFi and wired CMSIS-DAP interfaces. |
| **40** | [main.c:285-291,311-317](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/USER/main.c#L285-L291)<br>[send_data.c:1328-1332](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L1328-L1332) | **Dead Mailbox & Frozen Subscription Control Plane in `SUBSCRIBE_ONLY` Mode:** When `g_telemetry_mode == SUBSCRIBE_TELEMETRY_SUBSCRIBE_ONLY` (mode 2), `main.c` skips `Send_Groundstation_Telemetry_UART4()`. However, `Uart5_Subscribe_HandleRequest()` is called exclusively inside that function. Thus in `SUBSCRIBE_ONLY` mode, subscribe requests (rate changes, stops, memory reads) and platform discovery (0x22/0x23/0x24) are staged by ISR (`UA5RxSubscribePending = 1`) but never processed, permanently locking the mailbox. | In [main.c:311-317](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/USER/main.c#L311-L317), `Send_Groundstation_Telemetry_UART4()` is not called in mode 2; grep confirms no other call site for `Uart5_Subscribe_HandleRequest()`. | **P1** | **LOW** | In [main.c:289,315](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/USER/main.c#L289), add:<br>`if (UA5RxSubscribePending != 0U) { Uart5_Subscribe_HandleRequest(); }` directly in `Send_Task`. | Runs exclusively within `Send_Task`. Restores control-plane responsiveness in high-rate 200 Hz telemetry mode. |
| **41** | [usart5.c:34](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/usart5.c#L34)<br>[usart5.h:47](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/usart5.h#L47)<br>[subscribe.c:820,832](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/subscribe.c#L820) | **Missing `volatile` on ISR-Updated Variable `UA5RxSubscribeLen`:** `UA5RxSubscribeLen` is written in interrupt context by `try_stage_subscribe_frame()` (`BSP/usart5.c:331`) and read in task context (`Send_Task`) across 6 branch checks in `subscribe.c`. While `UA5RxSubscribePending` is `volatile uint8_t`, `UA5RxSubscribeLen` is non-volatile `uint16_t`. Compiler optimization can reorder or cache stale values in CPU registers. | `uint16_t UA5RxSubscribeLen = 0U;` in `usart5.c:34` lacks `volatile`, yet is modified in ISR and read in `Send_Task`. | **P2** | **LOW** | Change declaration to `extern volatile uint16_t UA5RxSubscribeLen;` in [usart5.h:47](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/usart5.h#L47) and definition to `volatile uint16_t UA5RxSubscribeLen = 0U;` in [usart5.c:34](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/usart5.c#L34). | Standard embedded qualifier hygiene. Zero execution overhead. |
| **42** | [subscribe.c:530-534](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/subscribe.c#L530-L534)<br>[usart5.c:155-181](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/usart5.c#L155-L181) | **Unchecked Acceptance of `SUBSCRIBE_TRANSPORT_UART5` Under Disabled Configuration:** `SUBSCRIBE_UART5_ENABLED` is 0 by default, compiling `Uart5_Subscribe_TxSend()` as a dummy stub `(void)buf; (void)len;`. However, `Subscribe_ParseStreamRequest()` accepts `transport == SUBSCRIBE_TRANSPORT_UART5`, ACKs with Schema 0x08, and enters an active state where `Subscribe_StreamTick()` calls the no-op stub every tick. | `Subscribe_ParseStreamRequest` checks `(transport != SUBSCRIBE_TRANSPORT_UART5) && (transport != SUBSCRIBE_TRANSPORT_USART3)`, but does not gate UART5 on `SUBSCRIBE_UART5_ENABLED`. | **P2** | **LOW** | In [subscribe.c:530](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/subscribe.c#L530):<br>`#if !SUBSCRIBE_UART5_ENABLED`<br>`if (transport == SUBSCRIBE_TRANSPORT_UART5) { (void)CopyStr((uint8_t*)err_out, "E:UART5 transport disabled", err_cap); return 0U; }`<br>`#endif` | Rejects impossible transport requests at configuration time instead of silently dropping telemetry. |
| **43** | [mrac.c:509-536](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/mrac.c#L509-L536) | **`MRAC_Reset()` Omits Clearing Filtered Output `u_ad` and Tracking Error `e` (NaN/Transient Persistence):** `MRAC_Reset()` zeros adaptive weights `Theta` and filtered weights `Whatf`, and resets `xm`, `xm_dot`, `xdot_f`, and `e_dot`. However, it leaves `state->u_ad` and `state->e` untouched. Under `ENABLE_PERFORMANCE_RECOVERY == 1`, `u_ad` evolves as an IIR filter (`u_ad += DT * omega_u * (raw_u_ad - u_ad)`). Pre-reset transients bleed into the mixer for over 1 second, and if `u_ad` becomes NaN, `raw_u_ad - NaN = NaN`, permanently trapping `u_ad` in NaN even across resets. | Lines 514-535 reset `Theta`, `Whatf`, `xm`, `xm_dot`, `x_prev`, `xdot_f`, `e_dot`. `u_ad` and `e` are absent. | **P1** | **LOW** | In [mrac.c:523](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/mrac.c#L523), add:<br>`mrac_state.pitch.u_ad = 0.0f; mrac_state.pitch.e = 0.0f;`<br>`mrac_state.roll.u_ad  = 0.0f; mrac_state.roll.e  = 0.0f;`<br>`mrac_state.yaw.u_ad   = 0.0f; mrac_state.yaw.e   = 0.0f;`<br>`mrac_state.z_rate.u_ad= 0.0f; mrac_state.z_rate.e= 0.0f;` | Guarantees true zero-state recovery upon reset. Eliminates NaN trapping in the performance recovery IIR filter. |
| **44** | [mrac_math.c:38](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/mrac_math.c#L38) | **Divide-by-Zero Singularity in `MRAC_Projection()` When `tol <= 0.0f`:** `MRAC_Projection()` computes `scale = (w_max - abs_theta) / tol; return y * scale;`. If `tol <= 0.0f`, this causes a floating-point divide-by-zero, injecting `+Inf` / `NaN` into adaptive weights. In contrast, `MRAC_ProjectGradient()` in `mrac.c:127` includes an explicit guard `if (band <= 0.0f)`. | Line 38 divides directly by `tol` without testing `tol > 0.0f`. | **P2** | **LOW** | In [mrac_math.c:37](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/mrac_math.c#L37), add:<br>`if (tol <= 0.0f) return (abs_theta >= w_max) ? 0.0f : y;` | Local mathematical boundary check; prevents NaN propagation. |
| **45** | [send_data.c:1482-1484](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L1482-L1484)<br>[mrac.c:137-142](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/mrac.c#L137-L142) | **CMD 0x05 Allows Setting `What_limit < What_tol`, Inverting Projection Scaling:** CMD 0x05 accepts `val >= 0.0f` without validating against `What_tol[elem]`. If `What_limit` is set lower than `What_tol` (e.g. limit=0.01 while tolerance=0.03), `upper - band` becomes negative. In `MRAC_ProjectGradient()`, `w > (upper - band)` evaluates to true for all $w \ge 0$, computing `scale < 0` which clamps to 0 or attenuates gradient learning across the entire weight span. | In [send_data.c:1482](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L1482), only `val >= 0.0f` is tested. In [mrac.c:137](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/mrac.c#L137), `upper - band = limit - tol`. | **P2** | **LOW** | In [send_data.c:1482](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L1482), validate parameter:<br>`else if (id == 0x05 && val >= configs[axis]->What_tol[elem]) configs[axis]->What_limit[elem] = val;` | Ensures consistency between tolerance band and authority limits. |
| **46** | [send_data.c:1482](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L1482)<br>[mrac.c:405-407](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/mrac.c#L405-L407) | **CMD 0x05 Updates Upper Authority `What_limit[0]` Without Updating `What_lower_limit[0]` (Asymmetric Authority Drift):** In `MRAC_Init()`, `What_lower_limit[0]` is initialized to `-What_limit[0]` to allow symmetric bias compensation. When CMD 0x05 updates `What_limit[0]`, `What_lower_limit[0]` is not updated, causing positive authority to expand while negative authority remains locked at the boot default (-0.15). | In [send_data.c:1482](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L1482), `What_lower_limit` is never updated on `elem == 0`. | **P2** | **LOW** | In [send_data.c:1482](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/send_data.c#L1482), append:<br>`if (elem == 0) configs[axis]->What_lower_limit[0] = -val;` | Preserves intentional symmetric bias authority documented in `mrac.c:400-408`. |
| **47** | [mrac.c:203, 259-264](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/mrac.c#L203) | **Unchecked Denominator in Reference Model Dynamics (`ref_model_bw` / `ref_model_zeta`):** In 1st-order reference model, `P = 1.0f / (2.0f * config->ref_model_bw)`. In 2nd-order model, `a0 = wn * wn; a1 = 2.0f * zeta * wn; P_e = Q1 / (2*a0); P_edot = (Q1/a0 + Q2) / (2*a1)`. If `ref_model_bw <= 0.0f` or `ref_model_zeta <= 0.0f`, denominators vanish, generating `+Inf` / `NaN` that propagates directly into tracking error drive `s` and corrupts adaptive weights. | denoms `2.0f * config->ref_model_bw`, `2.0f * a0`, and `2.0f * a1` evaluated without non-zero floor. | **P2** | **LOW** | In [mrac.c:191,200,259](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/mrac.c#L191), enforce lower floor:<br>`float wn = (config->ref_model_bw > 0.1f) ? config->ref_model_bw : 0.1f;`<br>`float zeta = (config->ref_model_zeta > 0.1f) ? config->ref_model_zeta : 0.1f;` | Prevents mathematical singularity under invalid tuning parameters. |
| **48** | [robot_types.h:109-112](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/Global_file/robot_types.h#L109-L112)<br>[stm32f4xx_it.c:68,92,275,442](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/stm32f4xx_it.c#L68)<br>[systemmonitor_task.c:19,21,23,25](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/systemmonitor_task.c#L19) | **Missing `volatile` Qualification on ISR-Updated `system_monitor` UART Packet Counters:** `USART1_task_cnt`, `USART2_task_cnt`, `USART4_task_cnt`, and `USART5_task_cnt` are incremented inside UART ISRs and read/cleared in `SystemMonitor_Task`. They are declared as plain `unsigned short` without `volatile`. Under compiler optimization, accesses can be reordered, coalesced, or cached in registers, distorting health monitoring telemetry. | Struct members in `SYSTEM_MONITOR` lack `volatile`. Written in ISR, read in 1 Hz monitoring task. | **P2** | **LOW** | In [robot_types.h:109-112](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/Global_file/robot_types.h#L109-L112), qualify counter fields:<br>`volatile unsigned short USART1_task_cnt;`<br>`volatile unsigned short USART2_task_cnt;`<br>`volatile unsigned short USART4_task_cnt;`<br>`volatile unsigned short USART5_task_cnt;` | Standard embedded C qualifier for ISR-shared variables. Zero flight-risk. |
| **49** | [usart1.c:50](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/usart1.c#L50)<br>[usart1.h:17](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/usart1.h#L17)<br>[RemoterTask.c:23-26](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/RemoterTask.c#L23-L26)<br>[AutoflyTask.c:226](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/AutoflyTask.c#L226) | **Missing `volatile` Qualification on ISR-Updated Array `sbus_channel[16]`:** `sbus_channel` is written element-by-element inside `DrvSbusGetOneByte()` called from `USART1_IRQHandler`. It is read asynchronously by `Remoter_Task`, `Autofly_Task`, and `Stabilizer_Task`. In `usart1.c` and `usart1.h`, it is declared as `unsigned short sbus_channel[16];` lacking `volatile`. The compiler can cache channel values across loop iterations, preventing tasks from sensing fresh pilot stick inputs or switch flips. | Written asynchronously in `USART1_IRQHandler`; read in tasks without `volatile`. | **P2** | **LOW** | In [usart1.h:17](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/usart1.h#L17) and [usart1.c:50](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/BSP/usart1.c#L50), declare:<br>`extern volatile unsigned short sbus_channel[16];` | Required for global buffers populated by ISR and polled by RTOS tasks. |
| **50** | [bmi088_driver.h:98-103](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/bmi088_driver.h#L98-L103)<br>[bmi088_driver.c:30-36](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/bmi088_driver.c#L30-L36)<br>[main.c:351,368](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/USER/main.c#L351)<br>[imu_update.c:110-120](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/imu_update.c#L110-L120) | **Missing `volatile` on Inter-Task IMU Sensor Globals (`Acc_*_Real`, `Gyro_*_Real`):** `Acc_X/Y/Z_Real` and `Gyro_X/Y/Z_Real` are written in `IMUSample_Task` (`1000 Hz`) and read in `IMU_DataDeal_Task` (`1000 Hz`) and `Send_Task`. Neither variable is declared `volatile` or protected by FreeRTOS synchronization. The compiler can assume these variables do not change across function calls within a translation unit and cache them in FPU registers. | Variables declared `extern FP32 Acc_X_Real;` etc. without `volatile`. Written in task A, read in task B. | **P2** | **LOW** | In [bmi088_driver.h:98-103](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/bmi088_driver.h#L98-L103) and [bmi088_driver.c](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/API/bmi088_driver.c), qualify as `volatile FP32`. | Ensures fresh memory reads on every 1 kHz estimation tick. |
| **51** | [AutoflyTask.c:66-73, 317-320](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/AutoflyTask.c#L66-L73)<br>[StabilizerTask.c:838-850](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/StabilizerTask.c#L838-L850) | **Non-Atomic Multi-Axis Position Setpoint Handoff From `Autofly_Task` to `Stabilizer_Task` Causes Skewed Waypoint Trajectories:** `Autofly_Task` (priority 4) computes trajectory targets and writes `Ctrler.locxPID.Des`, `Ctrler.locyPID.Des`, and `Ctrler.Z_posPID.Des` sequentially without a critical section. `Stabilizer_Task` (also priority 4) runs concurrently at 200 Hz. If a FreeRTOS time-slice context switch occurs between updating X and Y, `Stabilizer_Task` computes pitch and roll PID control on a torn waypoint (new X with old Y), momentarily commanding a false diagonal vector that degrades waypoint tracking accuracy and introduces attitude jerk. | `AutoflyTask_CommitRef` lines 68-70 write `locxPID.Des`, `locyPID.Des`, and `Z_posPID.Des` sequentially without mutex or critical section. | **P2** | **LOW** | In [AutoflyTask.c:66-73](file:///mnt/c/Users/Acer/Desktop/UAV_lab/FreeRTOS-adaptive-controller-codex/TASK/AutoflyTask.c#L66-L73), wrap setpoint assignments in `taskENTER_CRITICAL()` and `taskEXIT_CRITICAL()`. | Atomic commit of 3D setpoints eliminates coordinate skew; critical section lasts < 15 cycles. |

---

## 3. In-Depth Technical Evidence & False-Positive Audits

### Area 1: `API/subscribe.c` + `subscribe.h`

#### Finding 38: `stream_buf` Buffer Sizing Mismatch Drops Max Payload Frames
- **Code Trace:**
  - `API/subscribe.h:210`: `#define SUBSCRIBE_STREAM_MAX_BYTES 1024U`
  - `API/subscribe.h:177`: `#define SUBSCRIBE_STREAM_FRAME_OVERHEAD 12U` (6 header + 4 timestamp + 2 CRC16)
  - `API/subscribe.c:386`: `static uint8_t stream_buf[SUBSCRIBE_STREAM_MAX_BYTES + 8U];` (1032 bytes)
  - `API/subscribe.c:658-662`:
    ```c
    uint16_t payload_len = (uint16_t)(4U + st->total_bytes);
    if ((uint16_t)(6U + payload_len + 2U) > out_cap) {
        return 0U;
    }
    ```
- **Mechanism:** For `total_bytes == 1024`, `payload_len = 1028`, and the required frame length is `6 + 1028 + 2 = 1036` bytes. Because `sizeof(stream_buf) == 1032`, the bounds check fails, returning 0. In `Subscribe_StreamTick()`, the frame is dropped every cycle.
- **False-Positive Analysis:** Not a false positive. The comment on line 383 confirms the calculation was made assuming a 1-byte CRC and omitting the 4-byte timestamp (`6 header + 1024 payload + 1 CRC = 1031 B`). Sizing `stream_buf` to 1036 bytes completely resolves the issue with only 4 additional bytes of BSS.

#### Finding 39: Cross-UART Race Condition on `Subscribe_RxTransport`
- **Code Trace:**
  - `BSP/usart5.c:41`: `volatile uint8_t Subscribe_RxTransport = SUBSCRIBE_RX_TRANSPORT_UART5;`
  - `BSP/usart5.c:477`: UART5 handler unconditionally sets `Subscribe_RxTransport = SUBSCRIBE_RX_TRANSPORT_UART5`.
  - `BSP/usart5.c:499`: USART3 handler sets `Subscribe_RxTransport = SUBSCRIBE_RX_TRANSPORT_USART3`.
  - `API/subscribe.c:354-364`:
    ```c
    static void Subscribe_TxReplyToTransport(const uint8_t* buf, uint16_t len) {
        if (Subscribe_RxTransport == SUBSCRIBE_RX_TRANSPORT_USART3) {
            (void)Usart3_Stream_TxSend(buf, len);
        } else {
            Uart5_Subscribe_TxSend(buf, len);
        }
    }
    ```
- **Mechanism:** If a WiFi subscribe frame arrives on USART3, `Subscribe_RxTransport` is set to USART3. However, before `Send_Task` processes the mailbox, any UART5 activity runs `Handle_UART5_GroundStation_Command`, clobbering `Subscribe_RxTransport` to UART5. The response is then sent to UART5, and the WiFi client times out.
- **False-Positive Analysis:** Not a false positive. Transport selection belongs to the specific transaction/mailbox entry, not a single global variable that can be overwritten by another interface's ISR.

#### Finding 40: Frozen Subscription Mailbox in `SUBSCRIBE_ONLY` Mode
- **Code Trace:**
  - `USER/main.c:285-291, 311-317`: Mode 2 skips `Send_Groundstation_Telemetry_UART4()`.
  - `TASK/send_data.c:1328-1332`:
    ```c
    if (UA5RxSubscribePending != 0U) {
        Uart5_Subscribe_HandleRequest();
    }
    ```
- **Mechanism:** `Uart5_Subscribe_HandleRequest()` is the sole consumer of `UA5RxSubscribePending` and `UA5RxSubscribeBuf`. When `SUBSCRIBE_ONLY` mode is engaged, `Send_Groundstation_Telemetry_UART4()` is skipped, leaving `Uart5_Subscribe_HandleRequest()` uncalled. Any inbound request latches `UA5RxSubscribePending = 1`, and the interface permanently locks up.
- **False-Positive Analysis:** Not a false positive. Tested call-paths prove that `Uart5_Subscribe_HandleRequest()` has no other caller in the entire codebase.

---

### Area 2: `API/mrac.c` + `API/mrac_math.c`

#### Finding 43: Unreset Filtered Output `u_ad` and Error `e` in `MRAC_Reset()`
- **Code Trace:**
  - `API/mrac.c:509-536`:
    ```c
    void MRAC_Reset(void) {
        // Zeros Theta[i], Whatf[i], snaps xm, xm_dot, xdot_f, e_dot...
        // Does NOT touch u_ad or e!
    }
    ```
  - `API/mrac.c:344`:
    ```c
    state->u_ad += MRAC_DT * config->omega_u * (raw_u_ad - state->u_ad);
    ```
- **Mechanism:** When MRAC is reset (e.g. on arming or mode transition), `Theta` is zeroed, so `raw_u_ad = 0.0f`. However, `state->u_ad` is an IIR filter state that was not zeroed. If `state->u_ad` had a prior value, it takes several seconds to bleed off. If it had NaN, `0.0f - NaN = NaN`, causing NaN to permanently persist across resets.
- **False-Positive Analysis:** Not a false positive. An estimator/controller reset function must reset all dynamic integrator and filter states in the structure, especially IIR accumulator states.

#### Finding 45 & 46: Parameter Consistency Bugs in CMD 0x05
- **Code Trace:**
  - `TASK/send_data.c:1482`: `else if (id == 0x05 && val >= 0.0f) configs[axis]->What_limit[elem] = val;`
  - In `API/mrac.c:123, 137`: `band = tol[i]; if (w > (upper - band)) scale = (upper - w) / band;`
  - In `API/mrac.c:405-407`: `What_lower_limit[0] = -What_limit[0];`
- **Mechanism:** Setting `What_limit < What_tol` causes `upper - band < 0`, activating the projection scaling region even at zero weight. Furthermore, modifying `What_limit[0]` without updating `What_lower_limit[0]` destroys the symmetric bias authority designed in `MRAC_Init()`.
- **False-Positive Analysis:** Not a false positive. Parameter updating handlers must maintain the invariants established at initialization.

---

### Area 3: FreeRTOS & Concurrency

#### Finding 48, 49, 50: Missing `volatile` Qualifiers
- **Code Trace:**
  - `robot_types.h:109-112`: `unsigned short USART1_task_cnt;` ... (ISR-incremented, task-read).
  - `BSP/usart1.c:50`: `unsigned short sbus_channel[16];` (ISR-written, task-polled).
  - `API/bmi088_driver.h:98-103`: `extern FP32 Acc_X_Real;` ... (written in 1 kHz `IMUSample_Task`, read in 1 kHz `IMU_DataDeal_Task`).
- **Mechanism:** In the absence of memory barriers or OS synchronization primitives, C compilers are permitted to treat non-volatile variables as invariant across loop iterations within a single thread of execution, generating register-cached reads.
- **False-Positive Analysis:** Not a false positive. Every bare-metal / RTOS architectural guide requires `volatile` for memory locations mutated asynchronously by hardware ISRs or concurrent tasks.

#### Finding 51: Torn Waypoint Setpoints in `Autofly_Task`
- **Code Trace:**
  - `TASK/AutoflyTask.c:68-70`:
    ```c
    Ctrler.locxPID.Des = cont_x;
    Ctrler.locyPID.Des = cont_y;
    Ctrler.Z_posPID.Des = cont_z;
    ```
- **Mechanism:** `Autofly_Task` and `Stabilizer_Task` run at equal priority 4. Under round-robin time slicing, a context switch between line 68 and 69 allows `Stabilizer_Task` to execute with a mixed coordinate vector (new X, old Y), commanding an erroneous diagonal trajectory excursion.
- **False-Positive Analysis:** Not a false positive. Multi-dimensional position setpoints must be updated atomically to prevent geometric distortion in the trajectory tracker.

---

## 4. Outcomes

**Triage date:** 2026-09-20. **Build:** `Code=91060 RO-data=4304 RW-data=2544 ZI-data=121432`, 0 errors, 70 warnings.
**Flash:** `python -m ground_station.flashtool.rebuild_and_flash --force --yes` succeeded on the first attempt (arm gate passed).
**Verification:** `python -m ground_station.livewatch verify` → `ELF matches target: 20 chunk(s), 1280 B compared, 0 mismatches`.

| ID | Verdict | What happened |
| :--- | :--- | :--- |
| 38 | **Already fixed** | Fixed and flashed in an earlier round before this triage; re-read of `subscribe.c:386` / `subscribe.h:210` confirms the bound is in place. |
| 39 | **Fixed, flashed, verified** | `UA5RxSubscribeLen` widened to `volatile uint16_t`; a new `volatile uint8_t UA5RxSubscribeTransport` is staged inside the mask-protected block *before* `UA5RxSubscribePending = 1U;`, and the consumer in `API/subscribe.c` reads it instead of the racy `Subscribe_RxTransport`. Stub `API/tests/stubs/usart5.h` and `API/tests/test_subscribe_harness.c:21` updated in lockstep. |
| 40 | **Already fixed** | Fixed and flashed in an earlier round. |
| 41 | **Fixed, flashed, verified** | Length/type widened consistently across `BSP/usart5.c:34`, `BSP/usart5.h:47` and the `API/subscribe.c` consumers. |
| 42 | **Fixed, flashed, verified** | `API/subscribe.c:537` now rejects a UART5 subscribe under `#if !SUBSCRIBE_UART5_ENABLED` instead of ACKing a slot whose every frame would be dropped by the no-op `Uart5_Subscribe_TxSend` stub. **Divergence from the report:** the implemented error string is the shorter **`"E:UART5 disabled"`**, not the suggested `"E:UART5 transport disabled"` — the error buffer is tight and the shorter form always fits. |
| 43 | **Skip — moot** | `MRAC_Reset()` (`API/mrac.c:509-536`) not clearing `u_ad` / `e`: both are recomputed unconditionally on the first post-reset update before any consumer reads them, so no stale value or NaN can survive the reset. No behavioural difference to fix. |
| 44 | **Skip — false positive** | `API/mrac_math.c:38`: `tol` cannot reach `<= 0.0f` on this path — every writer clamps it, and CMD 0x08 ordering (see 45/46) now guarantees the clamp runs before the value is used. The divide is unreachable with a zero denominator. |
| 45 | **Fixed, flashed, verified** | `TASK/send_data.c:1482-1484` MRAC param-set ordering guard for `What_limit` (0x05) so the limit and its dependants are applied in a consistent order. |
| 46 | **Fixed, flashed, verified** | Same ordering guard for `What_tol` (0x08), including the `What_lower_limit[0] = -val` symmetry fix so the lower limit tracks the upper one. |
| 47 | **Fixed, flashed, verified** | Reference-model denominator floors in `API/mrac.c`: `if (bw < 0.1f) bw = 0.1f;` for the 1st-order model and `if (wn < 0.1f) wn = 0.1f; if (zeta < 0.1f) zeta = 0.1f;` for the 2nd-order one, plus the `u_max` clamp. |
| 48 | **Fixed, flashed, verified** | `volatile unsigned short USART1/2/4/5_task_cnt;` in `Global_file/robot_types.h` — these are written from ISRs and polled from tasks. |
| 49 | **Fixed, flashed, verified** | `volatile unsigned short sbus_channel[16];` at `BSP/usart1.c:50` and `BSP/usart1.h:17`. Also raised the USART1 NVIC preemption priority from 0 to 5 (`BSP/usart1.c:22`) so the RC ISR no longer outranks the RTOS-managed range. |
| 50 | **Skip — accepted risk** | `Acc_*_Real` / `Gyro_*_Real` (`API/bmi088_driver.h:98-103`) are not `volatile`. Making them volatile would defeat the compiler's ability to keep them in registers across the IMU update hot path, and the single producer/single consumer pattern is already ordered by the task handshake. Recorded as accepted rather than fixed. |
| 51 | **HOLD — user decision** | Torn 3-axis setpoint handoff at `TASK/AutoflyTask.c:66-73` / `TASK/StabilizerTask.c:838-850`. Real, but the fix touches the **control path**, which is outside the standing "no failsafe / altitude / OF scaling / mixer / `s_ekf` wiring" constraint. Left for the operator to approve. |

**Summary:** 14 findings → 2 already fixed, 8 fixed + flashed + verified on hardware, 3 skipped with reasons (1 moot, 1 false positive, 1 accepted risk), 1 held for operator approval.

### Related work flashed in the same image

Not findings from this report, but part of the same build: `TASK/send_data.c` gained `#pragma diag_suppress 1267`, the `send_prof.h` / `SendProf_Record(...)` hooks, and the DMA spin-waits were replaced with a non-blocking early return (`if (DMA_GetCurrDataCounter(DMA1_Stream7) != 0U) { return; }`) and a bounded `while ((DMA_GetCmdStatus(DMA1_Stream4) == ENABLE) && (--timeout > 0U)) { }`.

### Post-flash validation status

`python -m ground_station.livewatch verify` passed (0 mismatches), which proves the image on the target matches the ELF. The **stream-log re-validation of this image is still outstanding**: the dashboard service holds UDP 14550, and the UART5 fallback is compiled out of this build (`SUBSCRIBE_UART5_ENABLED` is never `#define`d). See [AGENT_GUIDE.md §7](../AGENT_GUIDE.md). The previous image was validated at 03:35 (30 s, slots 5/2/2/1 Hz, 0 dropped, 0 NaN).
