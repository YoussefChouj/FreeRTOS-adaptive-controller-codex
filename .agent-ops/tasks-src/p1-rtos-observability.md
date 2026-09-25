# P1 — FreeRTOS observability, probe-first (firmware tier-1 + host) — authorized

You cannot build (no Keil) and there is no drone. The supervisor builds, flashes and measures. Keil ARMCC 5 / C89:
declarations at block top, no C99. Minimal diff; match surrounding style.

## Goal
Per-task CPU %, stack high-water, loop period + jitter + overrun count for the control loop(s), heap free /
min-ever-free, and fault/reset cause — all readable by the pyOCD probe by DWARF name (no printf, no new telemetry
frame unless trivially cheap), then surfaced in livewatch, the dashboard service, and the dashboard MCP.

## Known starting points (verify, then inventory what already exists before adding anything)
- `FreeRTOS/include/FreeRTOSConfig.h`: `configGENERATE_RUN_TIME_STATS 0` (line 133), `configUSE_TRACE_FACILITY 1`,
  `configCHECK_FOR_STACK_OVERFLOW 2` (hook in USER/main.c writes g_stack_overflow_marker), heap 20 KB,
  `INCLUDE_uxTaskGetStackHighWaterMark 1`.
- `USER/fault_capture.c` (HardFault_Handler -> FaultCapture_Record) and `ground_station/livewatch/fault_log.py` exist.
- `TASK/send_data.c` already uses `DWT->CYCCNT` for profiling (lines ~698, 771, 1322).
- The service already has `--rtos-bridge` / `--rtos-interval` flags (ground_station/service/__main__.py): find what
  they read and extend that path rather than adding a parallel one.
- Tasks are created in `USER/main.c`. Livewatch groups live in `ground_station/livewatch/manifests.yaml`.
Grep for existing stack-HWM / reset-cause / loop-timing globals first (`grep -rn "HWM\|hwm\|RCC_CSR\|reset_cause\|
jitter\|overrun" TASK USER API BSP`). Reuse; do not duplicate.

## Do
1. Run-time stats: enable `configGENERATE_RUN_TIME_STATS` with the DWT CYCCNT as the counter
   (`portCONFIGURE_TIMER_FOR_RUN_TIME_STATS` enables DWT; `portGET_RUN_TIME_COUNTER_VALUE` returns CYCCNT >> a
   shift chosen so a 32-bit counter does not wrap inside one stats window; justify the shift in a comment).
   Where do the per-task counters live? Prefer: the probe reads TCBs directly (host walks the FreeRTOS task lists via
   DWARF: pxReadyTasksLists, xDelayedTaskList1/2, xSuspendedTaskList, ulRunTimeCounter, pxStack, pxTopOfStack,
   pcTaskName). If walking TCBs from the host is too fragile, add a low-rate (1 Hz) firmware snapshot into a static
   `volatile` array using `uxTaskGetSystemState` in an existing low-priority task — state which and why.
2. Stack high-water per task (probe: scan each task stack for the 0xA5 fill from pxStack; or firmware snapshot).
3. Control-loop period / jitter / overruns: in the StabilizerTask loop (and Send_Task if cheap) record DWT deltas:
   `volatile uint32_t g_loop_period_cyc_last, g_loop_period_cyc_max, g_loop_period_cyc_min, g_loop_overrun_count;`
   overrun = period > nominal * 1.5 (named #define, one-line justification). Resettable by a probe write to a
   `g_loop_stats_reset` flag. Keep it < 20 cycles of work per iteration.
4. Heap: `xPortGetFreeHeapSize` / `xPortGetMinimumEverFreeHeapSize` — probe-readable (heap_4 statics) or snapshot.
5. Reset cause: at boot, latch `RCC->CSR` into `volatile uint32_t g_reset_csr` then clear flags (RMVF); decode on host
   (POR/PIN/SFT/IWDG/WWDG/LPWR/BOR). Include existing fault_capture fields in the same host view.
6. Host: `python -m ground_station.livewatch rtos` subcommand (or extend an existing one) printing a table:
   task | prio | state | CPU% (over a window of two reads) | stack HWM words | + loop/heap/reset summary.
   Add a livewatch group `rtos_health`. Service: a read-only `GET /api/rtos` (probe-backed, cached ≥ 1 s, returns
   `{"available": false, "reason": ...}` when the probe is busy/absent — never block /state). Add an MCP read tool only
   if the dashboard MCP server has an obvious pattern for read tools (find it; `.mcp.json` server `dashboard`).
7. Tests: host unit tests for the TCB/stack parsers and CSR decode using synthetic memory images; a service test for
   /api/rtos with a fake probe. Firmware host test (tests/firmware_host/, gcc -std=c89 -pedantic -Wall -Werror) for the
   loop-stats update function if you factor it out.

## Do not
Touch control math, EKF, arming, motor code. No new telemetry frame types. No patch_*.py, no binaries.
Do not POST to port 8081.

## Deliverable
Commit on your branch. Digest `.agent-ops/out/p1.md` under 80 lines: inventory of what already existed (file:line),
per-file diff summary, exact DWARF names, estimated CPU/RAM/flash cost with reasoning, pytest + gcc summary lines,
and a supervisor checklist (what to read on the live drone to verify each metric, expected ranges).
