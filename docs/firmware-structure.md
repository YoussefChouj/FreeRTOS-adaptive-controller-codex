# Firmware structure (WP-37, 2026-10-04)

One page for people and agents: where each concept lives, how to add a tunable, how to add a telemetry group,
and what a subscribe slot costs. The style reference is `API/pid.c`; the table rules are in
`docs/firmware-table-pattern.md`; the quality gate is `bash tools/check.sh` (see `docs/firmware-quality.md`).

## Module map (control path)
| Module | Owner task, rate | Purpose | Key state | Tunable tables |
|---|---|---|---|---|
| `TASK/StabilizerTask.c` | Stabilizer_Task, 200 Hz | Attitude/position cascade, mixer call, motor output, `Tlm_Snapshot` | `Ctrler` (PID rows), `g_tlm` | named constants at the top |
| `API/pid.c` | Stabilizer_Task, 200 Hz | PID step, guards, `ComputePID_GatedHold`, gain lease | `Ctrler.*PID` | `PID_ROW`, `GAIN_LEASE_ROW` |
| `API/controller.c` | Stabilizer_Task, 200 Hz | Runtime controller select on top of PID; quad-X mixer | `g_mix`, `g_ctrl_*` | `MIX_ROW` |
| `API/mrac.c` | Stabilizer_Task, 200 Hz (`MRAC_DT`) | MRAC per axis (`MRAC_UpdateAxis` split into 6 steps), simplex | `mrac_state`, `mrac_simplex`, `mrac_inj` | `MRAC_SET`, `MRAC_BASIS` (in `MRAC_Init`) |
| `API/thrust_estimators.c` | Stabilizer_Task, 200 Hz | Shadow thrust estimates (LUT, blade element, IMU) | `g_thrust_est` | PWM knot row + 2-row thrust curve |
| `API/ekf_of.c` | called from `TASK/StabilizerTask.c` | 8-state OF position / velocity-bias / accel-bias KF | `g_ekf_of_*` | named state indices |
| `TASK/send_data.c` | Send_Task, 200 Hz nominal (~80 Hz in MIXED telemetry) | Telemetry frames, 9-state EKF step, GS commands | `k_gs_cmds` (27 handlers) | named reject reasons |
| `API/subscribe.c` | Send_Task | UART5 subscribe parser, slot streams on USART3/UART5 | `s_streams[SUBSCRIBE_MAX_SLOTS]` (CCM) | `SUBSCRIBE_*` defines in `subscribe.h` |
| `API/wfb_*.c` | called from `TASK/StabilizerTask.c`, `send_data.c`, `RemoterTask.c` | Workflow-B glue, primitives, safety, trajectory | | `WFB_*_ROW` |

Other tasks (`USER/main.c`): SystemMonitor_Task, IMU_DataDeal_Task, IMUSample_Task, Remoter_Task, Autofly_Task,
dbg_report_task. Every refactored file starts with a header: module, owner task and rate, purpose, inputs, outputs.

Not yet at the pid.c standard (no header or tables yet): `API/` Ano_OF, GPS, bmi088_driver, delay, fw_identity,
sys, tf_mini_plus; `TASK/` AutoflyTask, RemoterTask, led, stm32f4xx_it, systemmonitor_task. WP-41 brought the other
`API/` files listed there before to the standard (report: `docs/agent/reports/WP-41-cte.md`).

## How to add a tunable
1. Put it in the module's `*_ROW` table (one row per loop or object, aligned columns), never as a loose literal.
2. Add the `@name unit [min,max]` line to the table legend; `tools/row_meta.py` checks every cell against it
   (step `row-meta` of `tools/check.sh`). Bounds that were not measured on the drone are PROPOSED.
3. If the ground station writes it, add the command field to `k_gs_cmds` in `TASK/send_data.c` and to
   `ground_station/platform/firmware_contract.py` `COMMAND_TABLE` (a test pins ids on both sides).
4. Run `bash tools/check.sh`. For a refactor, also prove it is behaviour-preserving:
   `python tools/fw_equiv.py --base <git-ref> <file>` (same normalized -O2 code and data) or
   `python tools/fw_trace.py --base <git-ref> stab|mrac|cmd` (differential trace).

## How to add a telemetry group
`g_tlm` (`API/flight_telemetry.h`) is one contiguous block of float32 groups in CCM, written once per control tick by
`Tlm_Snapshot()` at the end of the Stabilizer_Task tick, so every value in it belongs to the same tick.
1. Add the field (float32 only) to its group in `API/flight_telemetry.h` and fill it in `Tlm_Snapshot()`.
2. Mirror it in `ground_station/platform/telemetry_groups.py` in the same commit (its test parses the header).
3. After the Keil build, regenerate the manifest: `python -m ground_station.platform.capability_manifest`.
Today: 7 groups, 60 floats, 240 B. One subscribe range (size 4, count `FLIGHT_TLM_FLOATS`) streams all of it.
Nothing was renamed: the existing symbols the ground station reads are still where they were.

## Subscribe slot budget (measured on the host build unless marked)
| Item | Value | Source |
|---|---|---|
| `sizeof(Subscribe_Stream_t)` | 508 B | host build (WP-37) |
| 4 slots + staging (default) | 2540 B of CCM, 0 B of SRAM | 5 x 508 |
| 8 slots + staging | 4572 B of CCM, 0 B of SRAM | 9 x 508; harness `subscribe_8_slots` passes |
| Ranges per slot | up to 62 | `API/flight_telemetry.h` |
| USART3 guard | 95% of the wire ceiling = 87552 B/s | `API/subscribe.h` |
| UART5 guard | 20% (frames A/B/C already use 74%) | `API/subscribe.h` |
The budget guard sums all slots on a transport, so more slots never oversubscribe a link; they only let more
groups run at different rates. Frames are built in `stream_buf`/`tx_buf` (SRAM, DMA-safe); the slot table is
CPU-only, so CCM (no DMA access) is safe for it.
Raising the default to 8 is one line in each of `API/subscribe.h`, `ground_station/platform/firmware_contract.py`
and `ground_station/livewatch/stream.py` (`MAX_SLOTS`). It is PROPOSED, not done: the per-tick CPU cost of 8 busy
slots and the stream behaviour on the drone are not measured.
