# R1 — Web research digest (no code changes)

Research only. Use web search. Every claim needs a source URL plus a short verbatim quote (under 25 words) from it.
No quote means the claim is dropped. Prefer primary sources: official docs, project source/wiki, papers.

Context: STM32F407 quadrotor, FreeRTOS, Keil ARMCC 5, SWD probe (pyOCD) and a WiFi UDP telemetry link to a Python
ground station with a web dashboard. Controllers: cascaded PID plus an MRAC; planned variants are structured MRAC,
RBF-NN MRAC and a 3-layer adaptive stack. Estimator: 9-state EKF (body velocity, accel bias, gyro bias) on optical flow + height.

## Topics (one section each)
1. FreeRTOS run-time stats: `configGENERATE_RUN_TIME_STATS`, `portCONFIGURE_TIMER_FOR_RUN_TIME_STATS`,
   `uxTaskGetSystemState`, `uxTaskGetStackHighWaterMark`, `xPortGetMinimumEverFreeHeapSize`, trace hook macros
   (`traceTASK_SWITCHED_IN/OUT`), and SEGGER SystemView / Percepio Tracealyzer. What does each cost (CPU, RAM, flash)
   on a Cortex-M4 at 168 MHz? Which can be read by a debugger with no firmware-side printing (DWARF/probe reads)?
   How do you measure loop period/jitter and count deadline overruns cheaply (DWT CYCCNT)? How is the reset/fault
   cause read (RCC_CSR flags, CFSR/HFSR/MMFAR/BFAR)?
2. Autopilot patterns: PX4 (uORB, `perf_counter`, `top`/load_mon, ekf2 innovation checks and fallbacks, commander
   arming checks), ArduPilot (scheduler perf/overrun reporting, EKF lane switching, arming checks, motor spin-arm
   vs spin-min, `MOT_SPIN_ARM`), Betaflight (task stats `tasks` CLI, gyro overflow, arming disable flags).
   Focus on: arm vs idle-spin separation, estimator health gating before arm, fallback on estimator failure.
3. MRAC / RBF-NN adaptive control practice: projection operator vs sigma-modification vs e-modification,
   dead-zones, adaptation-rate limits, parameter bounds, fallback to baseline PID, L1 adaptive as alternative,
   how people compare adaptive controllers in flight tests (metrics: tracking RMSE, control effort, adaptation
   transient). Cite Lavretsky & Wise, Hovakimyan & Cao, or flight-test papers where possible.
4. Controller plug-in interfaces in autopilots: how PX4/ArduPilot let you swap controllers at runtime
   (PX4 module selection, ArduPilot custom controller `CC_TYPE` in AC_CustomControl). Interface shape
   (init/reset/update/output), bumpless transfer on switch, and logging.
5. Ground-station design: QGroundControl / Mission Planner / PlotJuggler / Foxglove patterns for live plotting,
   recording, replay and flight-test workflow. Which few features give the most value for a research lab?

## Output
`.agent-ops/out/r1.md`, under 150 lines. For each topic: 3-6 patterns, each as
`pattern | cost | benefit for this drone | source URL | "quote"`. End with a ranked top-10 of things worth doing here,
each with a one-line reason. Do not propose rewrites; prefer small additions.
Commit the digest on your branch.
