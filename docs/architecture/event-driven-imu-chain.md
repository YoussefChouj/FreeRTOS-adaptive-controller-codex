# Event-driven IMU -> attitude -> stabilizer chain (OPEN feature, not started)

Status: OPEN, recorded 2026-10-06. Nothing built, nothing measured. Measure first (step 0); close this ticket if
the measurement shows no extra latency or jitter. Do after the 2026-10-06 demo, after the pending branches
(`ram-savings`, `o2-build`, `float-math`, `h0g-port`) merge, and together with or after
`docs/architecture/task-data-sharing.md` (same tasks, same globals).

## Problem (facts checked in the code on 2026-10-06)

- Three prio-4 tasks each sleep on their own `vTaskDelayUntil` timer (`USER/main.c`):
  `IMUSample_Task` 1 ms (`Sensor_Data_Prepare`, :389), `IMU_DataDeal_Task` 1 ms (`IMU_Update_Mahony(&imu_data,1e-3f)`,
  :370), `Stabilizer_Task` 5 ms = 200 Hz (:418). Nothing links "sample read" to "sample used".
- When several prio-4 tasks wake on the same tick, FreeRTOS picks the order, not the design. If DataDeal runs
  before Sample in a tick, Mahony uses the previous tick's sample (one extra tick of delay). Actual order: NOT measured.
- The sensor runs on its own clock: BMI088 gyro ODR 2000 Hz (`GYRO_BANDW 0x00`), accel ODR 1600 Hz
  (`ACC_CONF 0xAC`), polled at 1 kHz (`API/bmi088_driver.c:144,164`). Sample age at read time varies; accel samples
  are skipped in a beat pattern. Effect on control: NOT measured.
- No BMI088 data-ready interrupt is configured (no INT3/INT4 writes, no IMU EXTI). The INT pin wiring on the
  board is unknown. RPM already uses EXTI lines 0, 1, 6, 7 (`BSP/rpm.h`).

## Proposed design (PX4 `mc_rate_control` style: run when the data arrives)

| Option | Change | Clock |
|---|---|---|
| A (software only, first) | `IMUSample_Task` keeps the only 1 ms timer; after the read it calls `xTaskNotifyGive(DataDeal)`; DataDeal waits in `ulTaskNotifyTake`; every 5th sample DataDeal notifies Stabilizer | SysTick, one timer instead of three |
| B (hardware, later, only if A is not enough) | BMI088 gyro data-ready -> EXTI ISR -> `vTaskNotifyGiveFromISR(IMUSample)` | the sensor's own clock |

## Dependencies that must keep working (check each)

1. Mahony `dt` is hard-coded `1e-3f` (`main.c:379`, CONSTRAINT comment). Option A keeps 1 ms; option B needs a
   measured dt (DWT cycle counter) or a fixed sensor rate.
2. 200 Hz assumptions in the stabilizer: `LOOP_OVERRUN_CYCLES` (`main.c:413`), `GyroFilter_Init(200.0f)`, PID/MRAC dt.
   Option A must deliver exactly every 5th sample.
3. Stale data (lesson Q5): if a notification never arrives (sensor or task dead), the waiting task must not block
   forever. `ulTaskNotifyTake` with a timeout; on timeout count it in `system_monitor` and take the failsafe path.
   Coordinate with `API/fw_health.c` (IWDG).
4. Loop stats `g_loop_period_cyc_*`, `g_loop_overrun_count` and `system_monitor.*_cnt` stay; they are the
   before/after jitter measurement.
5. Update the comment `PERF: Keep fixed-period scheduling for control-loop determinism` (`main.c:451`): the chain
   keeps a fixed period from one clock.
6. Host builds (SIL WP-31, log replay WP-34, `h0g-port` host test): stub the notify calls.
7. `Remoter`, `Autofly`, `Send` tasks stay timer-driven (they do not consume per-sample data).
8. Gates: `bash tools/check.sh` CHECK PASS, fw_lint, Keil 0 errors / 0 warnings; bench, then one workflow C hover.

## Plan

0. Measure: DWT-stamp the end of `Sensor_Data_Prepare`, the start of `IMU_Update_Mahony`, and the start of
   `stabilizer_Task`; log sample age at use and its spread over a bench run. If the age is always under one tick
   and steady, close this ticket.
1. Option A, then measure again (same stamps, `g_loop_period_cyc_max`, `hlth.stab_cpu_pct`).
2. Option B only if step 1 still shows a problem and the BMI088 INT pin is wired to a free EXTI line.
