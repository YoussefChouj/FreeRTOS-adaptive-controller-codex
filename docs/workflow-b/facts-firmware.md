# Workflow B: firmware facts

Source: code reading only, worktree `.worktrees/wfb` (branch `workflow-b`, HEAD 2e20b6f), 2026-09-30.
Nothing here was measured on hardware. Every fact is `file:line`; paths are relative to the worktree unless noted.
Items I could not confirm are marked UNCONFIRMED. "Derived" means arithmetic on cited numbers.
GS-side facts are in `facts-gs.md`, not repeated here.

## 1. GS command framing

Two framings share one queue `gs_cmd_queue[16]` (ring, uint8 head/tail mod 16, drop counter `gs_cmd_drop_count`).
Commands are executed in `Send_Task` via `Process_GroundStation_Command` (`TASK/send_data.c:1350-2028`), not in the ISR.

Legacy frame, 9 bytes: `CC DD id idx v0 v1 v2 v3 xor`
- value = float32 little-endian via union; xor = XOR of bytes [2..7] compared to byte [8]: `BSP/usart4.c:104-135` (UART4), `BSP/usart5.c:345-372` (`handle_command_frame`, shared UART5/USART3).
- On checksum mismatch the parser resyncs by 1 byte: `BSP/usart4.c:151-153`, `BSP/usart5.c:466-469`.
- The legacy path does no id-range check and ignores `CommandSafetyReject`; only in-handler gates apply (`TASK/send_data.c:1448-1464` reports the reject reason for transaction frames only).

S3 transaction envelope, `CC DF` (`firmware/command_protocol.c:74-96`, `firmware/command_protocol.h:6-9`):
- `[0..1]=CC DF [2]=version(must be 1) [3]=flags [4..5]=txid LE [6]=cmd id [7]=idx [8..9]=payload_len LE [10..]=payload, last byte = XOR of buf[2..len-2]`. Frame length = 11 + payload_len; `PLATFORM_COMMAND_MAX_PAYLOAD` = 64.
- The firmware accepts only `payload_len == 4` (one float32 LE): `BSP/usart5.c:419-421`; so a command frame is 15 bytes. Queued at `BSP/usart5.c:423-431`.
- Replies are `AA BB` result frames: ACK=0, REJECTED=1, APPLIED=2 (`firmware/command_protocol.h:19-21`), sent at `TASK/send_data.c:1448-1466` and `2022-2026`.
- Duplicate txid returns APPLIED "duplicate" from a 16-entry history: `TASK/send_data.c:1354-1372`.
- `CommandSafetyReject` (`TASK/send_data.c:1375-1393`): id==0 or id>0x1E -> reason 4 (unknown). 0x06, 0x0A, 0x0B, 0x0C, 0x11 -> reason 6 when FlyMode != SDK. 0x18 -> reason 6 unless GROUND_IDLE and DisArmed. The 0x1E idx0 armed check is at 1441-1444.
- Subscribe protocol is a third framing, `CC DE` (not covered here).

Ingress: UART4 `Handle_UART4_GroundStation_Command` (`BSP/usart4.c:100`); UART5 gated by `SUBSCRIBE_UART5_ENABLED` (`BSP/usart5.c:479-491`); USART3/WiFi `Handle_USART3_GroundStation_Command` (`BSP/usart5.c:502-506`). Call sites `TASK/stm32f4xx_it.c:269-270`, `USER/main.c:318`, `USER/main.c:349`.

Param-write scheme: every command is `(id, idx uint8, val float32)`. There is no typed param table. Booleans are `(uint8_t)(val+0.5f) != 0`.

### CMD id table (handler line in `TASK/send_data.c`)

| id | line | meaning (idx: field) |
| --- | --- | --- |
| 0x01 | 1468 | PID gain. idx = axis*3+gain (0 Kp, 1 Ki, 2 Kd). Axes 0 pitch, 1 roll, 2 yaw, 3 gyrox, 4 gyroy, 5 gyroz, 6 Z_rate. val in 0..200 |
| 0x02 | 1490 | MRAC gamma. idx high nibble = axis 0-3, low nibble = element |
| 0x05 | 1490 | MRAC What_limit (same handler/idx scheme) |
| 0x08 | 1490 | MRAC What_tol (same handler/idx scheme) |
| 0x03 | 1538 | idx0-3 mrac_to_mixer (val>1); 4-7 u_max; 8 `gs_throttle_min_pct` (0..1); 9 `gs_throttle_max_pct` (0.5..1) |
| 0x04 | 1569 | idx0 `GroundStation_AbortAllPaths` + `GS_KeySDKflag=0`; idx1 `RECOVER_SDK` |
| 0x06 | 1518 | virtual stick, idx0 thr, 1 pitch, 2 roll, 3 yaw; val clamped [-1,1]; needs FlyMode_SDK. This is the RC keepalive |
| 0x07 | 1530 | idx0 `bench_mode_active` |
| 0x09 | 1556 | idx0 `gs_max_horizontal_speed_mps` (0.05..20), idx1 `gs_max_vertical_speed_mps` (0.05..10), idx2 max pitch deg (3..60), idx3 max roll deg (3..60). Defaults 1.0 m/s each: `Global_file/global_declare.c:32-33` |
| 0x0A | 1579 | TWC. idx0 target_x, 1 target_y, 2 target_z, 3 set_yaw, 4 execute. SDK only |
| 0x0B | 1596 | sinusoid. idx0-2 center xyz, 3 amplitude, 4 frequency, 5 duration, 6 axis 0-2, 7 start/stop |
| 0x0C | 1630 | circle. idx0-2 center, 3 radius, 4 angular_speed, 5 duration, 6 start/stop |
| 0x0D | 1703 | idx0 `GroundStation_AbortAllPaths` (same as 0x04 idx0) |
| 0x0E | 1948 | arm/idle. See section 2 |
| 0x0F | 1727 | idx0-12 MRAC feature flags; idx100/101/102 telemetry mode LEGACY/MIXED/SUBSCRIBE_ONLY |
| 0x10 | 1854 | idx0 `Reset_World_Origin` |
| 0x11 | 1660 | figure-8. idx0-2 center, 3 amplitude, 4 angular_speed, 5 duration, 6 type (0 Bernoulli, 1 Gerono), 7 start/stop |
| 0x12 | 1695 | `waypoint_spacing` in cm, 0 = continuous. Default 5.0 (`Global_file/global_declare.c:50`) |
| 0x13 | 1758 | reference-model type 0/1/2 |
| 0x14 | 1800 | SysID. idx0 axis, 1 signal, 2 f0, 3 f1, 4 amplitude, 5 duration, 6 start/abort, 7 geofence enable (1832). Start zeroes the OF origin itself (1815-1826) |
| 0x15 | 1840 | gyro LPF, idx0 enable, idx1 cutoff Hz |
| 0x16 | 2003 | motor bench. idx0 enable/heartbeat, 1 motor 1-4, 2 CCR in [2000,4000]. Dead-man 100 ticks = 500 ms (`TASK/StabilizerTask.c:35-37`) |
| 0x17 | 1865 | one-shot OF bias capture |
| 0x18 | 1911 | force recalibration; GROUND_IDLE and DisArmed only |
| 0x19 | 1773 | Simplex |
| 0x1E | 1886 | OF bias mode. idx0 mode (disarmed only), 1 EMA freeze, 2 tau (1..300 s), 3 handheld test |

FREE ids (no hit in `TASK/send_data.c`, `BSP/usart3.c`, `BSP/usart4.c`, `BSP/usart5.c`, `firmware/*`, `API/subscribe.c`): **0x1A, 0x1B, 0x1C, 0x1D**. They pass the transaction-path range check (<=0x1E). Ids above 0x1E are rejected on the transaction path only (see above); the legacy path would queue them and the handler would ignore them. UNCONFIRMED: whether any id is reserved in `docs/` or GS code for these four.

Also: a repo-wide grep was limited to the firmware directories above, not to Python.

## 2. RC logic

Channels (`TASK/RemoterTask.h:15-23`): ROLL `sbus_channel[0]`, PITCH `[1]`, THR `[2]`, YAW `[3]`, MODE_CH `[4]` (ch5), OFHOLD_CH `[5]` (ch6), FLYUP_CH `[6]` (ch7), PATH_EXEC_CH `[7]` (ch8), kill `sbus_channel[9]` (ch10).
- SBUS scaling `(raw-1000)/800*1000+3000`: `TASK/RemoterTask.c:13-60`. Decode `BSP/usart1.c:55-100`; valid-tick refresh at `BSP/usart1.c:91`.
- `sbus_lost` (`TASK/RemoterTask.c:43-56`): if `sbus_last_valid_tick==0` and now > 500 ms, lost = 1 forever; otherwise lost when the gap > 500 ms. `sbus_last_valid_tick` starts at 0 (`Global_file/global_declare.c:17`).
- Kill / failsafe (`TASK/RemoterTask.c:155-172`): `sbus_channel[9] <= 500 || sbus_lost` increments `DangerousStop_cnt`; when > 10 (about 50 ms at the 5 ms tick, derived) `FlightFSM_Event(DANGEROUS_STOP)` fires, otherwise `RECOVER_SDK` fires every tick.
- ch5 land: rising edge of `MODE_CH > 1300` while `flight_phase == FLYING` sets `flight_phase = LANDING` and `TWC.execute = 0` (`TASK/RemoterTask.c:174-186`).
- ch7 fly-up: rising edge of `FLYUP_CH > 500` (`TASK/RemoterTask.c:188-207`). If DISARMED it sends ARM_REQUEST; if then ARMED and `g_motor_idle_enabled` it sets `sbus_flyup_trigger = 1`. The trigger is dropped, not pended, when idle is not enabled.
- ch8 path trigger: rising edge arms, `RCInput_SetAuthority(1)`, `sbus_path_trigger = 1` (needs ARMED and idle) (`TASK/RemoterTask.c:209-230`). The consumer is a TODO stub that only clears the flag: `TASK/StabilizerTask.c:1158-1164`.
- Stick gestures, `Check_Stick_Motion` (`TASK/RemoterTask.c:62-135`): thresholds MAX > 0.75, MIN < -0.75 (62-66). Arm = THR MIN + YAW MAX held `ARM_Delay_time`=150 ticks (`Global_file/global_declare.c`-side define at `Global_file/global_declare.h:53`; event at `TASK/RemoterTask.c:113`). Disarm = THR MIN + YAW MIN held `DISARM_Delay_time`=50 (`global_declare.h:54`; event 118). Idle-enable = pitch MIN + roll MAX held `IDLE_ENABLE_Delay_time`=150 (`global_declare.h:55`; 125-129). Gestures read raw `Remoter.*Ctrler`, not `RCInput_Get`, so GS authority does not block them (`TASK/RemoterTask.c:71-73`).

GS authority (`API/rc_input.c`, `API/rc_input.h`):
- `RCInput_Get(axis)` returns virtual sticks `s_virtual[]` if `s_authority`, else normalized physical sticks (`API/rc_input.c:78-107`). Clamped to [-1,1].
- `RCInput_SetAuthority(1)` sets virtual THR = -1.0, seeds the takeover snapshot, arms a grace of `RC_AUTHORITY_GRACE_TICKS`=10 (`rc_input.c:28`, `144-160`). `SetAuthority(0)` zeros virtual sticks and clears heartbeat flags (`163-170`).
- Keepalive is CMD 0x06 only (`rc_input.c:137-140`). Watchdog (`rc_input.c:231-251`): after the first 0x06, a gap > `RC_HEARTBEAT_TIMEOUT_MS` = 500 ms (`rc_input.h:40`) revokes authority (`s_authority=0`, virtual sticks zeroed, `s_heartbeat_lost=1`). `RCInput_IsHeartbeatLost` at `rc_input.h:84` / `rc_input.c:301`. Comment says GS keepalive is 50 Hz (`rc_input.c:233`); the GS side is not verified here.
- Heartbeat loss does NOT land or disarm: virtual sticks return to centre and the drone falls back to hold (`rc_input.c:161-162` comment). UNCONFIRMED: what hold does with `TWC.execute` after a heartbeat loss (PathArbitrate stops presets when authority is gone: `TASK/AutoflyTask.c:17-48`).
- Physical takeover (`rc_input.c:185-227`): any axis moving more than `RC_PHYSICAL_RATE_DELTA` = 0.05 per 10 ms tick (`rc_input.c:20-24`), after the 10-tick grace, sets `s_authority = 0` and `GS_KeySDKflag = 0` (line 225).
- `RC_IDLE_THR_THRESHOLD` = -0.85 (`rc_input.h:37`).

CMD 0x0E (`TASK/send_data.c:1948-1991`):
- idx0 val != 0: ARM_REQUEST; if then ARMED, `GS_KeySDKflag = 1` and `RCInput_SetAuthority(1)`; if `Z_posPID.FB > 0.35` virtual THR is set to 0 instead of -1.
- idx0 val == 0: drops authority only; the drone stays ARMED (1965-1971). It is not a disarm.
- idx1 val != 0 (motor idle enable): needs ARMED, phase GROUND_IDLE or LANDED, `RCInput_Get(THR) < -0.85`, idle not yet enabled (1974-1984). idx1 val == 0 clears idle only in GROUND_IDLE (1985-1991).
- A GS-only disarm command: none found other than 0x04 idx0 / 0x0D (which is DANGEROUS_STOP, section 3). UNCONFIRMED whether a plain DISARM_REQUEST is reachable from any CMD.

## 3. Flight phase enum, FSM, LANDING

Enums (`API/flight_fsm.h`): states DISARMED=0, ARMED=1, EMERGENCY=2 (6-9); phases GROUND_IDLE=0, FLYING=1, LANDING=2, LANDED=3 (17-21); events ARM_REQUEST, DISARM_REQUEST, DANGEROUS_STOP, RECOVER_SDK (32-36). Globals `flight_phase`, `g_motor_idle_enabled`: `API/flight_fsm.c:8-9`.

Transitions (`API/flight_fsm.c:34-57`):
- DISARMED + ARM_REQUEST -> ARMED only if `IMU_EstimatorReady()` (line 42). Ready = sensor_ok and (settled or 30 s boot timeout): `API/imu_update.c:44, 151, 215-221`.
- DISARMED + DANGEROUS_STOP -> EMERGENCY (43). ARMED + DISARM -> DISARMED; ARMED + DANGEROUS_STOP -> EMERGENCY (46-47). Both set phase GROUND_IDLE and `g_motor_idle_enabled = 0`.
- EMERGENCY + RECOVER_SDK or DISARM -> DISARMED (50-51). EMERGENCY ignores ARM_REQUEST.
- `s_sync` (`flight_fsm.c:11-23`): ARMED -> `ARM_Status=Armed`, FlyMode_SDK; EMERGENCY -> DisArmed, FlyMode_DangerousStop; DISARMED -> DisArmed, FlyMode_SDK.
- Consequence (code reading, not tested): with no SBUS receiver ever seen, `sbus_lost` stays 1, DANGEROUS_STOP fires every tick after 11 ticks, and the FSM sits in EMERGENCY, so a GS-only arm is impossible (`TASK/RemoterTask.c:43-56, 155-172`; `flight_fsm.c:43, 52`).
- Consequence: GS abort (CMD 0x04 idx0 or 0x0D) sends DANGEROUS_STOP, so it is a motor kill, not a soft stop; it also clears authority, TWC and path flags (`TASK/send_data.c:1416-1426`). EMERGENCY returns to DISARMED via RECOVER_SDK on the next `Check_Fly_Mode` tick unless the kill condition persists.

Stabilizer loop (`TASK/StabilizerTask.c:228-306`): 200 Hz (5 ms). While `!g_estimator_ready` it pins the world origin every tick (231); then `Check_Fly_Mode`, `Update_Data`, `Compute_Motor`, `Update_Motor`.
- DISARMED branch: `TWC.execute=0`, `sbus_flyup_trigger=0`, `Clear_Structure`, zero motors (795-806). EMERGENCY branch: `Clear_Structure`, zero motors (779-794).
- GROUND_IDLE -> FLYING (736-775, transition 756-758) needs `g_motor_idle_enabled`, `Z_posPID.FB > 0.2` m, and (`TWC.execute` or `RCInput_Get(THR) >= 0.2`). With idle off, motors are zero and the structure is cleared. Idle hold applies when THR < 0.2 and no TWC (763-770).
- Fly-up consumer (1166-1176): `RCInput_SetAuthority(0)`, `TWC.target_x/y = TWC.world_x/y`, `target_z = 0.5f` m, `execute = 1`. The Z setpoint ramps 0.005 m per `Update_Des` call (1213-1221); `Update_Des` is called once per `Compute_Motor` (842), so at 200 Hz that is about 1.0 m/s (derived). The code comment claims 0.5 m/s at about 100 Hz. UNCONFIRMED which is true on hardware. Z position PID itself runs every 2nd tick (844-855).

LANDING (`TASK/StabilizerTask.c:694-735`):
- Z setpoint decrements `LAND_DES_STEP` = 0.0015 m per tick (about 0.30 m/s at 200 Hz, derived) (29-32); setpoint snapped to min(Des, FB), floored at 0 (1178-1189).
- Rate setpoint `Z_ratePID.Des = Z_posPID.U` plus a sink bias: `s_land_sink_bias += 0.001` per tick while |Zrate FB| < 0.10 and Des <= 0.01, capped 0.40 m/s (1226-1242).
- Touchdown: |`Z_ratePID.FB`| < rate_thr (0.08 if `Z_posPID.FB < 0.20`, else 0.02) for 10 consecutive ticks and `Z_posPID.FB < 0.15` (714-725).
- Timeout `LAND_MAX_TICKS` = 2000 (10 s at 200 Hz, derived) forces disarm (33-34).
- Result: phase LANDED, `FlightFSM_Event(DISARM_REQUEST)`, `Set_Zero_Motors` (731-733). The FSM then resets the phase to GROUND_IDLE, so LANDED is transient (`flight_fsm.c:46`). CMD 0x0E idx1 can re-idle from LANDED (`send_data.c:1977-1982`).
- Landing trigger paths: ch5 (section 2). UNCONFIRMED: any GS command that sets `flight_phase = LANDING` directly (none found in `send_data.c`).

## 4. Setpoint plumbing

Controller structs (`Global_file/robot_types.h:47-62`, `API/pid.h:16`): `Ctrler.locxPID/locyPID` (cm), `Ctrler.locxsPID/locysPID` (velocity cm/s), `Ctrler.Z_posPID` (m), `Ctrler.Z_ratePID` (m/s), `Ctrler.yawPID` (deg). `PIDTypeDef` fields include Des, FB, U, E, SumE (`robot_types.h:25-43`).
- TWC: xy in cm, z in m (`TASK/StabilizerTask.c:1131-1136`: dx/dy scaled 0.01, arrival distance < 0.15 m). `extern TargetSet_WorldReal_Coordinate TWC` at `TASK/StabilizerTask.h:57`.
- TWC -> setpoint: when `TWC.execute == 1`, `locx/yPID.Des = TWC.target_x/y` (`StabilizerTask.c:1271`): a step, no XY ramp. Any active roll/pitch stick clears `TWC.execute` (1267-1270). Velocity setpoint from `locxy PID.U` clamped to +/-120 cm/s (1274-1290). Stick velocity scale = `gs_max_horizontal_speed_mps*100`; vertical stick scale = `RCInput_Get(THR)*gs_max_vertical_speed_mps` (1246-1247).
- `Reset_World_Origin` (`StabilizerTask.c:203-226`): zeros `ano_of` earth/DISTANCE, `locx/yPID.FB` and `.Des`, `locxs/ys.Des`, OF bias, and EKF-OF position in mode 2. It does NOT reset Z. Called on the ARM edge (914-951, which also clears loc SumE and re-snaps OF bias), on CMD 0x10 (`send_data.c:1854-1858`), and while the estimator is not ready (231). CMD 0x14 start zeroes the origin itself (`send_data.c:1815-1826`).
- `AutoflyTask_PathArbitrate` (`TASK/AutoflyTask.c:17-48`): if `!RCInput_GetAuthority()` it stops sinusoid/circle/figure8 and sets `TWC.execute=0` (this is the takeover behaviour). Otherwise the paths are mutually exclusive and a `TWC.execute` conflict clears them. Presets also need `DroneStatus.FlyMode == FlyMode_SDK` (`AutoflyTask.c:107, 136, 178`; TWC via `send_data.c:1580`).
- `AutoflyTask()` runs at 200 Hz, dt = 0.005 (`TASK/AutoflyTask.c:214-249`; `USER/main.c:478-485`).
- Presets write through `AutoflyTask_CommitRef` (`AutoflyTask.c:64-101`): reference quantized by arc length `waypoint_spacing` (cm; z scaled x100, line 89) before writing `locx/yPID.Des` and `Z_posPID.Des`. Circle 103-130 (theta += angular_speed*dt; yaw Des = theta*RAD2DEG), sinusoid 132-171, figure-8 173-212. A preset ends at `t_elapsed >= duration` (duration > 0) and clears `TWC.execute`. Start sets `active=1`, `t_elapsed=0`, `AutoflyTask_WaypointReset` in a critical section (`send_data.c:1618-1622`).
- Global limits: 1.0 m/s horizontal and vertical by default (`global_declare.c:32-33`).
- UNCONFIRMED: there is a legacy SDK state machine at `AutoflyTask.c:226-248` triggered by SBUS ch5 = ch6 = 1800 for 2 s; not investigated.

## 5. State variable names

| quantity | symbol | units / note | cite |
| --- | --- | --- | --- |
| Battery voltage | `real_voltage` (float), `voltage`, `adc_value` | volts, 4S pack; `real_voltage = 7.0663f*voltage + 0.8930f` (2-pt cal 2026-06-24) | `TASK/StabilizerTask.c:1546-1557`, `TASK/StabilizerTask.h:59` |
| Voltage refresh | `Get_Voltage()` | 1 Hz from `SystemMonitor_Task`; also per frame in the motor-test path | `USER/main.c:243-262`, `TASK/send_data.c:812` |
| Roll / pitch / yaw (deg) | `imu_data.rol`, `imu_data.pit`, `imu_data.yaw` (`_imu_st`) | Mahony | `Global_file/robot_types.h:340-351`, `API/imu_update.h:8` |
| Control-side attitude | `Ctrler.pitchPID.FB = -imu_pit`, `rollPID.FB = imu_rol`, `yawPID.FB = -imu_yaw` | sign flips on pitch and yaw | `TASK/StabilizerTask.c:613-615` |
| Tilt | none as a single variable. Compute from `rol`/`pit`; `Cos_pitch_01`, `Sin_pitch_01` exist | | `StabilizerTask.c:322-323` |
| Altitude | `Ctrler.Z_posPID.FB` | m (ToF/OF height) | `StabilizerTask.c:1226-1247`, `API/sysid.c:107` |
| Vertical rate | `Ctrler.Z_ratePID.FB = ano_of.of2_h_f2_v` | m/s | `StabilizerTask.c:600` |
| XY position | `Ctrler.locxPID.FB`, `locyPID.FB` | cm; from `ano_of.earth_x_ture = earth_y`, `earth_y_ture = -earth_x` (axes swapped) | `StabilizerTask.c:429-432` |
| Motor outputs | `mymotor.motor1..motor4` (`short`, `MOTORTypeDef`) | timer CCR, `Motor_PWM_ZERO`=2000, `Motor_PWM_IDLE`=2150, `Motor_PWM_MAX`=4000 | `BSP/pwm.h:12-25`, `StabilizerTask.c:630-684` |
| Base throttle | `Throttle_th` = 3150 (3200 in bench mode) | comment: hover about 3135-3145 | `StabilizerTask.c:1038-1046` |
| Arm state / mode | `DroneStatus.ARM_Status`, `.FlyMode` | | `flight_fsm.c:11-23` |
| Phase / idle | `flight_phase`, `g_motor_idle_enabled` | | `flight_fsm.c:8-9` |
| Estimator ready | `g_estimator_ready` | | `API/imu_update.c:51,151`, `Global_file/global_declare.h:166` |

EKF (two different filters, read carefully):
- `s_ekf` (`Ekf9_t`, static in `TASK/send_data.c:61`) is the 9-state IMU EKF, state `x[9] = [v_body(3), b_a_body(3), b_g_body(3)]` (`API/ekf.h:19-35`). It has NO position state. Its output reaches control only through `g_ekf_gate` (`API/ekf.h:82-93`, defined `send_data.c:64`): `StabilizerTask.c:524-526` uses `g_ekf_gate.vx_cms/vy_cms` (cm/s) when `ctrl_enable && healthy`.
- `s_ekf_of` (`EkfOf_t`, `TASK/StabilizerTask.c:138`) is the 6-state OF filter `x[6] = [pos_x, vel_x, bias_x, pos_y, vel_y, bias_y]`, metres (`API/ekf_of.h:8-40`). Its position feeds `locx/yPID.FB` only when `g_of_bias_mode == 2` (x100 to cm): `StabilizerTask.c:410-432`.
- `g_of_bias_mode` default = `OF_BIAS_MODE_DEFAULT` = 0 (FIXED, bias at boot): `StabilizerTask.c:103-105`. In modes 0 and 1, position is the OF integrator `ano_of.earth_x/earth_y` (cm) (`API/Ano_OF.h:55-58`).
- UNCONFIRMED: there is no exported "position" symbol from the EKF that is independent of `locx/yPID.FB`; `locx/yPID.FB` is the single position variable in all three modes.

## 6. Memory

CCM placement syntax (only one user, `API/mrac.c`):
- `#define MRAC_CCM __attribute__((section("MRAC_CCM"), zero_init))` under `#ifdef __CC_ARM`, empty otherwise: `API/mrac.c:16-21`. Use: `MRAC_State_t mrac_state MRAC_CCM;` (`mrac.c:25`), also 29-32, 184, 191-194, and a function-static `static float grad[MAX_NUM_BASIS] MRAC_CCM;` (242).
- Rule in the file header: CPU-only 64 KB CCM at 0x10000000, no DMA may touch a `MRAC_CCM` object (`mrac.c:13-14`).
- Scatter file: `USER/JX_FLY.sct` (project setting `<ScatterFile>.\JX_FLY.sct</ScatterFile>`, `USER/JX_FLY.uvprojx:369`). Region: `RW_IRAM2 0x10000000 0x00010000 { *(MRAC_CCM) }` (`USER/JX_FLY.sct:14-16`); main SRAM `RW_IRAM1 0x20000000 0x00020000 { .ANY (+RW +ZI) }`; flash `ER_IROM1 0x08000000 0x00100000`.
- `OBJ/JX_FLY.sct` (uVision-generated, main tree) does NOT have the RW_IRAM2 block; the build uses `USER/JX_FLY.sct`.
- Subscribe address allowlist covers SRAM `0x20000000..0x2001FFFF` and CCM `0x10000000..0x1000FFFF` (`API/subscribe.h:41, 73`), so CCM objects are readable by the subscribe protocol.

Numbers exactly as printed in `OBJ/JX_FLY.map`. CAVEAT: the map is gitignored (`.gitignore:15`) and is NOT present in this worktree; I read the main-tree file `C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex\OBJ\JX_FLY.map` (mtime 2026-09-30 08:51). It is a build of the main tree, not of `workflow-b`; use it only as the BEFORE baseline and re-measure.
- `Total RO  Size (Code + RO Data)               101932 (  99.54kB)` (map line 6673)
- `Total RW  Size (RW Data + ZI Data)            126368 ( 123.41kB)` (6674)
- `Total ROM Size (Code + RO Data + RW Data)     102428 ( 100.03kB)` (6675)
- Grand Totals row: Code 97588, RO Data 10268, RW Data 4344, ZI Data 2760 (map line 6667; the row also shows Total 123608 and ELF Image 730592).
- `Execution Region RW_IRAM1 (Base: 0x20000000, Size: 0x0001e758, Max: 0x00020000, ABSOLUTE, COMPRESSED[0x000001f0])` (map 6391).
- `Execution Region RW_IRAM2 (Base: 0x10000000, Size: 0x00000648, Max: 0x00010000, ABSOLUTE)` holding only `MRAC_CCM mrac.o` (map 6384-6388).
- `Load Region LR_IROM1 (Base: 0x08000000, Size: 0x000198f4, Max: 0x00100000, ABSOLUTE, COMPRESSED[0x0001901c])` (map 5779).
- Derived: main SRAM free = 0x20000 - 0x1e758 = 0x18A8 = 6312 bytes; CCM free = 0x10000 - 0x648 = 65536 - 1608 = 63,928 bytes.
- CCM symbols and sizes (map 5515-5524): `mrac_state` 464, four `mrac_config_*` 172 each, `mrac_bus` 128, `mrac_g_gamma/sigma/phi` 96 each, `mrac_u_ff` 16, `grad` 24 (4637-4638).
- Note: main SRAM has only about 6 KB headroom (derived). `heap_4` and task stacks live in it. UNCONFIRMED: what `configTOTAL_HEAP_SIZE` is; not looked up.

## 7. PROTECTED markers, safety, low voltage, fence, crash

PROTECTED markers: **none exist in any source file yet**. A grep for `PROTECTED` outside `stm32_lib/`, `FreeRTOS/`, `OBJ/` hits only `.agent-ops/grill-autonomous-flight-loop.md:199, 206, 247, 282` and `docs/workflow-b/build-plan.md:19`. Tasks 1-2 must add them.
- Intended protected set (spec, not code): RC input + ch10 kill + takeover (`rc_input.c`, `RemoterTask.c`, `AutoflyTask_PathArbitrate`), arm/disarm and landing transitions (`flight_fsm.c`), heartbeat-loss auto-land, low-V backstop, geofence/ceiling, trajectory bounds check, flash-when-armed block, motor driver + mixer saturation (`pwm.c`), IMU driver, init (`main.c`); mixed files `StabilizerTask.c`, `send_data.c` need markers (`.agent-ops/grill-autonomous-flight-loop.md:199-207`).
- The spec lists "heartbeat-loss auto-land" and "low-V backstop" as protected items. Neither exists in firmware today (see below).

Low voltage: the only handling is a buzzer. `Get_Voltage()` calls `SetBeep(1)` when `real_voltage < 15.0f` (`TASK/StabilizerTask.c:1554-1557`); `SetBeep(1)` sets TIM4 CCR3 = 1000 (`BSP/pwm.c:303-313`). It is never switched off by code in this path (the only `SetBeep(0)` I did not look for; UNCONFIRMED). No cutoff, no forced landing, no threshold variable, no telemetry flag other than the voltage value. `Get_Voltage` is commented out of `stabilizer_Task` (`StabilizerTask.c:304`).

Geofence: exists only inside SysID. `s_geofence_en` default 1, set by CMD 0x14 idx7 (`API/sysid.c:63, 291-293`; `send_data.c:1832`). `sysid_abort_condition` (`API/sysid.c:98-113`) aborts on: not armed, FlyMode != SDK, any pitch/roll/yaw stick active, altitude outside `SYSID_ALT_MIN_M` 0.30 to `SYSID_ALT_MAX_M` 1.50 m (`sysid.c:22-23`), XY farther than `SYSID_SOFT_XY_CM` 50 cm from the start point (`sysid.c:18`), |pitch| or |roll| > `SYSID_ANGLE_LIM_DEG` 30 (`sysid.c:24`). None of this is active outside a SysID run. No general fence or ceiling exists for TWC/presets (TWC target is not bounds-checked in `send_data.c:1579-1596`; UNCONFIRMED for the preset handlers, only the parameter ranges in section 1 were seen).

Altitude sensor limit (not a ceiling): Z loops hold the last valid FB and freeze integrators when the ToF sample is out of the 5 m band or 0xFFFF (`StabilizerTask.c:163-167`).

Crash / tilt detection: **no firmware crash detect exists** (no tilt-over-limit -> motors-off path in `TASK/`, `API/`; grep of `fabsf(...rol|pit) >` finds only `API/mrac.c:487-488` and `API/sysid.c:110-111`). `mrac.c:487-488` is the MRAC Simplex trigger (reason codes 1/2 for roll/pitch over `mrac_simplex.roll_max/pitch_max`); it freezes/fades the MRAC injection, it does not stop motors. The spec says the operator must write crash detect (`.agent-ops/grill-autonomous-flight-loop.md:247, 250`).

Other safety facts:
- PWM ceiling comment: 4000, "anything above risks ESC saturation" (`StabilizerTask.c:1044`); `Motor_PWM_MAX` 4000 (`BSP/pwm.h:14`). UNCONFIRMED whether every motor path clamps to it; I did not read the mixer.
- Flash-when-armed block is in the Python flashtool, not firmware (AGENTS.md; `ground_station/flashtool/rebuild_and_flash.py:34` lists exit code 5 target dark). Not inspected further.
- Motor bench dead-man 500 ms (`StabilizerTask.c:35-37`); CMD 0x16 only in bench use.
- `SystemMonitor_Task` (1 Hz, `USER/main.c:243-262`) reads voltage, task stats, heap; `SystemErrorDetect` (`TASK/systemmonitor_task.c:8`) counts fps and lights LEDs when `Z_posPID.FB == 0` (line 30); it takes no flight action.

## 8. Keil build

- Project file: `USER/JX_FLY.uvprojx`; target name `JX_FLY` (`USER/JX_FLY.uvprojx:10`); output `..\OBJ\JX_FLY` with HEX (`uvprojx:50-54`); scatter `.\JX_FLY.sct` (369).
- Compiler flags: `<uC99>1</uC99>` and `<MiscControls>--C99</MiscControls>`, defines `STM32F40_41xxx,USE_STDPERIPH_DRIVER` (`uvprojx:325, 335-336`). So the compiler accepts C99 even though the project rule (AGENTS.md, build-plan G1) is C89-style. The C99 flag is a fact; the C89 style is convention only.
- UV4 path is hard-coded: `_UV4 = Path(r"C:\Keil_v5\UV4\UV4.exe")`, `_PROJECT = _ROOT/"USER"/"JX_FLY.uvprojx"` (`ground_station/flashtool/safe_flash.py:51-52`).
- Invocation: `subprocess.run([str(_UV4), flag, str(_PROJECT), "-o", str(log)], timeout=timeout)` with flag `-b` (incremental) or `-r` (rebuild) (`safe_flash.py:157-192`; callers `rebuild_and_flash.py:165, 169`). No `-t` and no `-j0`; the code comment says NOT to pass `-j0` because armcc jobs race on `.crf` files (`safe_flash.py:177`). This contradicts the `UV4 -b -t JX_FLY -j0` line in AGENTS.md.
- The `-o` log goes to `C:\tmp`-style `LOG_DIR` because UV4 mangles paths with spaces (`safe_flash.py:172-181`). Empty log is treated as failure rc=2 (`safe_flash.py:157-168, 194-200`). UV4 exit: 0 ok, 1 warnings, >=2 errors (`safe_flash.py:178`).
- Build hazards: resident `UV4.exe` blocks the run (`rebuild_and_flash.py:64-73`, preflight `preflight.py:102`); silent link no-op (`rebuild_and_flash.py:134`); the build injects `build_id.c` into the uvprojx temporarily (`build_id.py:29, 418`).
- Other: `python -m ground_station.flashtool.compile_commands` regenerates clangd's db from the uvprojx (`compile_commands.py:3, 189`).
- Worktree note: `OBJ/` in this worktree has no `JX_FLY.map`/`JX_FLY.axf` (map is gitignored); a Keil build here needs the supervisor's tooling. UNCONFIRMED whether UV4 can build a worktree path (path contains no space: `C:\Users\Acer\Desktop\UAV_lab\FreeRTOS-adaptive-controller-codex\.worktrees\wfb`).
