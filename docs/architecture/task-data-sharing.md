# Task data sharing: torn reads and torn writes (OPEN feature, not started)

Status: OPEN, recorded 2026-10-06. Nothing built, nothing measured. Do after the 2026-10-06 demo and after the
pending branches (`ram-savings`, `o2-build`, `float-math`, `h0g-port`) merge: they touch the same files.

## Problem (facts checked in the code on 2026-10-06)

- Tasks share data through plain globals (`Global_file/global_declare.h`, 33 `extern` lines). App code has no
  `xQueueSend`/`xQueueReceive`/`xSemaphoreTake` call.
- Priorities (`Global_file/creat_task.h`): IMU_DataDeal, IMUSample, Stabilizer, Remoter, Autofly = 4;
  Send = 2; SystemMonitor = 1. `configUSE_PREEMPTION 1`, `configUSE_TIME_SLICING 1`, tick 1000 Hz.
- Torn read: `Send_Task` (prio 2) copies a multi-field struct (attitude, EKF state) field by field; a prio-4
  writer can preempt it between fields. The frame then mixes two samples (roll from N, pitch/yaw from N+1).
  Same-priority prio-4 tasks can tear each other at a tick (time slicing). A single aligned 32-bit float is
  written by one store on the Cortex-M4, so a field is old or new, never half (no NaN from this mechanism).
- Torn write (the riskier case): `Process_GroundStation_Command()` runs inside `Send_Task` (prio 2) and writes
  gains, setpoints and flags read by prio-4 tasks (CMD 0x01 gain sets, CMD 0x1D MRAC variant fields, workflow B
  glue, livetune gain lease in `API/pid.c`). A prio-4 controller can run between two field writes and use a
  half-applied gain set for one or more ticks.
- How often either happens in flight: NOT measured.

## Proposed design (PX4 uORB-lite, not queues/mutexes everywhere)

| Data kind | Mechanism | Why |
|---|---|---|
| State published by one task, read by others (attitude, EKF, setpoints) | per-topic struct; writer `topic_publish()` copies the whole struct, reader `topic_copy()` takes a whole snapshot; short critical section or seqlock | readers always get one consistent sample; cost is one struct copy |
| Commands from the ground station (gains, modes, flags) | queue of command messages (`xQueueSend` in Send_Task), drained at the top of the consumer's tick | the consumer applies a whole gain set between ticks, never mid-tick |
| Shared peripherals (if any bus is used by two tasks) | mutex | only place a mutex fits; never inside a 1 kHz loop without a priority-inversion check |

## Dependencies that must keep working (check each)

1. Subscribe / stream-log slots (`Subscribe_StreamTick`, skill `stream-log`) read variables by RAM address
   from the ELF symbol table: keep the published globals as named symbols (the topic snapshot can be the
   symbol), then regenerate `capability_manifest.json`.
2. `g_tlm` telemetry groups (WP-37) and `Send_Groundstation_Telemetry_UART4`: fill from `topic_copy()`.
3. DMA: UART5 telemetry uses DMA1_Stream7, and DMA cannot reach CCM RAM on the STM32F4. A snapshot buffer
   handed to DMA must live in main SRAM. Coordinate with the `ram-savings` branch (heap moved to CCM).
4. Host builds (SIL WP-31, log replay WP-34 `mrac_log_replay_host.c`, `h0g-port` host test): stub the
   critical-section / queue calls on the host, the same way PX4 SITL swaps drivers and keeps modules.
5. Timing: critical sections add jitter. Measure `hlth.stab_cpu_pct` and `loop_max_us` before/after.
6. `o2-build` branch: at -O2 the compiler may keep a global in a register; the topic API's critical
   section / barrier also fixes that. Land this before or with -O2.
7. Gates: `bash tools/check.sh` CHECK PASS, fw_lint (coding standard rule set), Keil 0 errors / 0 warnings.

## Plan

0. Inventory: for each global in `global_declare.h`, list writer task(s) and reader task(s); keep only the
   ones crossing a task boundary. Decide per row: topic, command queue, or leave (single-field, single writer).
1. Command queue for `Process_GroundStation_Command` (torn writes first: highest risk).
2. Topics for attitude (`imu_data`), EKF state, setpoints.
3. Bench run: stack high-water marks, CPU %, loop_max_us; then one hover with workflow C.
