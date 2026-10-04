# Firmware safety: pre-arm checks, watchdog, RTOS budget (WP-40)

All three ship **report only**: nothing here can refuse an arm or reset the board until a table row is changed.

## Pre-arm checks (`API/prearm.c`)

`PreArm_Evaluate` runs once per second (SystemMonitor_Task, after the fps counters) and again on every
`FLIGHT_EVENT_ARM_REQUEST`. A NaN input fails its check.

| bit | check | fails when | limit (PREARM_LIMITS_ROW, PROPOSED) |
|---|---|---|---|
| 0 | estimator | `IMU_EstimatorReady()` is 0 | - |
| 1 | vbat | `real_voltage` below the floor | 14.0 V |
| 2 | rc | `sbus_lost != 0` | - |
| 3 | level | abs(roll) or abs(pitch) at or above the tilt | 10 deg |
| 4 | trip | `g_wfb_status.safety_trip != 0` | - |
| 5 | stab_fps | `system_monitor.stabilizerTask_fps` below the floor | 180 Hz |

- `g_prearm_fail_mask`: every failed check. `g_prearm_first_fail`: lowest failed bit (0xFF if none).
- `g_prearm_block_mask` = fail & `g_prearm_enable_mask`. `FlightFSM_Event` arms only if
  `IMU_EstimatorReady() && PreArm_Allows()` (block mask 0).
- `PREARM_ENABLE_ROW` is all 0: the estimator gate still works as before, the other checks only report.
  Enable a check after a bench session shows it never fails on a good pack, one bit at a time.

## Independent watchdog (`API/fw_health.c`)

`FW_HEALTH_ROW(iwdg_enable, iwdg_timeout_ms)` = `(0, 2500)` PROPOSED.

- With `iwdg_enable` = 1, `FwHealth_Tick` starts the IWDG at the first second where IMUSample, IMUUpdate,
  stabilizer and remoter all report a non-zero fps, then reloads it only in seconds where all four are alive.
  A stalled critical task therefore resets the board after the timeout.
- LSI is 17..47 kHz, so 2500 ms nominal is 1.70..4.70 s real. Prescaler 64; reload = timeout/2, clamped 1..4095.
- **Once started the IWDG cannot be stopped** until reset. A reset in flight drops the motors. Enable it only after
  a bench soak with props off, and never with an untested firmware change.
- `DBGMCU_IWDG_STOP` is set, so a debugger halt does not trigger a reset.

## Reset cause and RTOS budget (`g_rtos_budget`)

| field | source |
|---|---|
| reset_cause | `RCC->CSR >> 25` captured at boot (`g_reset_csr`, USER/main.c): BOR 0x01, PIN 0x02, POR 0x04, SFT 0x08, IWDG 0x10, WWDG 0x20, LPWR 0x40 |
| iwdg_on | 1 after the watchdog started |
| alive | 1 when the four critical tasks reported a non-zero fps this second |
| stack_min_words / stack_min_task | lowest `usStackHighWaterMark` and its FreeRTOS task number |
| stab_cpu_pct | stabilizer run-time delta / total run-time delta (DWT CYCCNT, 168 MHz) |
| loop_max_us | `g_loop_period_cyc_max` / 168 |

The task snapshot is taken after `SystemErrorDetect`, so the budget always describes the **previous** second. The
first `stab_cpu_pct` after boot is not valid (the previous counters start at 0).

## Fault in flight: the motors keep the last throttle (finding 2026-10-05, read from code, not tested)

`HardFault_Handler`, `MemManage_Handler`, `BusFault_Handler` and `UsageFault_Handler` (USER/fault_capture.c, the
copies linked per `OBJ/JX_FLY.map`) all call `FaultCapture_Record`, which writes the record and spins in `for (;;)`.
TIM3 keeps generating PWM from `CCR1..4` (`M1..M4`, BSP/pwm.h) without the CPU, so every motor holds its last
command. The RC kill switch and ch5 land run in `Remoter_Task`, which no longer runs. With `iwdg_enable` = 0 nothing
resets the board, so the motors run until the battery is unplugged.

Operator rule for flights on the current image: if the drone stops answering the sticks and the kill switch, it
has probably faulted; cut power (battery strap or tether) rather than waiting for a failsafe.

Fix (PROPOSED, `USER/fault_capture.c`, branch after the demo, bench test props off): first statement of
`FaultCapture_Record` is `Set_Zero_Motors();` (BSP/pwm.c, four register stores of `Motor_PWM_ZERO` = 2000 counts =
1000 us, no kernel calls, legal in a fault handler). PX4 and ArduPilot do the same: a panic stops the outputs.
The record then explains the fall. Related: the record does not survive a reset (docs/firmware-stack-budget.md).

## Telemetry

Group `hlth` (`TlmHealth_t`, 8 floats, `g_tlm.hlth.*`): prearm_fail_mask, prearm_block_mask, reset_cause,
stack_min_words, stack_min_task, stab_cpu_pct, loop_max_us, iwdg_on. The block is now 68 floats; one subscribe
range still streams all of it (`ground_station/platform/telemetry_groups.py`).

## Tests

`tests/firmware_host/test_prearm.c` and `test_fw_health.c` (run by `python tools/host_tests.py`) cover the pure
logic; the glue in `TASK/systemmonitor_task.c` is checked by the Keil build only.
