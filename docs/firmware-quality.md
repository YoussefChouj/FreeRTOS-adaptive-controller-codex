# Firmware quality: gate, budgets, open items (WP-36, 2026-10-04)

## The gate: `bash tools/check.sh`
One command, no Keil, no hardware. Every step runs; exit 1 if any fails. `.github/workflows/check.yml` runs the
same script on push/PR (ubuntu, apt gcc + gcc-multilib + clang-tidy + gcc-arm-none-eabi). Not yet run on GitHub.

| Step | What | Command |
|---|---|---|
| host-tests | 14 host C tests (API/tests, tests/firmware_host), one table row each | `python tools/host_tests.py [name]` |
| mrac-equiv | MRAC bit-exact vs the reference tree, plain + sigma-prior | `python API/tests/run_mrac_equiv.py` |
| c-pytest | pytest suites that compile firmware C (subscribe harness, MRAC variants) + tools tests | pytest |
| sil-smoke | `sim/sil/test_sil.py` (firmware controllers closed-loop), minus its EQUIV case | pytest |
| clang-tidy | `.clang-tidy` on the 11 firmware files the host tests build, with their flags | `python tools/host_tests.py --tidy` |
| row-meta | unit and [min, max] of every `*_ROW` tunable; every row value in range | `python tools/row_meta.py [--json]` |
| fw-lint | ASCII-only, file header, no new double libm call; allow-list shrinks only | `python tools/fw_lint.py` |
| stack | task stacks and nested MSP from the Keil call graph; ISR above the syscall ceiling calls no kernel code | `python tools/stack_budget.py` |
| doc-paths | every repo path named in an agent-facing doc exists; allow-list shrinks only | `python tools/doc_paths.py` |
| arm-syntax | `arm-none-eabi-gcc -fsyntax-only`, Cortex-M4F flags, same files; SKIP if absent | `python tools/host_tests.py --arm` |

clang-tidy checks: `bugprone-*`, `clang-analyzer-*`, `readability-non-const-parameter`, warnings are errors. Off:
swappable-parameters, reserved-identifier (vendor `__FOO_H` guards), implicit-widening (int and ptrdiff_t are both
32-bit on the M4). On Windows the runner borrows MinGW gcc's include dirs; `bugprone-signed-bitwise` ignores
positive literals (`x << 16`). Scope limit: TASK/, BSP/, USER/ are not covered (Keil headers, no host build).

## Budgets (Keil map `OBJ/JX_FLY.map` + `.htm` of the main checkout, link of 2026-10-04 04:13; source commit not recorded)
| Region | Used | Size | Use |
|---|---|---|---|
| Flash ER_IROM1 | 115 028 B (0x1c154) | 1 MiB | 11.0 % |
| SRAM RW_IRAM1 | 124 880 B (0x1e7d0) | 128 KiB | **95.3 %**, 6 192 B free |
| CCM RW_IRAM2 | 13 888 B (0x3640) | 64 KiB | 21.2 % |

Largest modules (map "Image component sizes"; flash = Code + RO, RAM = RW + ZI, bytes):

| Module | Flash | RAM | Note |
|---|---|---|---|
| bmi088_driver.o | 4 212 | 80 783 | 62 % of the 128 KiB SRAM in one driver |
| heap_4.o | 784 | 20 504 | FreeRTOS heap 20 KiB: task stacks + TCBs |
| wfb_glue.o | 2 142 | 12 280 | trajectory buffer |
| subscribe.o / usart3.o / usart5.o | 4 180 / 916 / 1 630 | 5 864 / 5 242 / 1 572 | telemetry and radio buffers |
| send_data.o | 10 820 | 1 400 | largest code |
| stabilizertask.o | 10 314 | 522 | control loop |
| mrac.o (+ mrac_math.o) | 6 128 (+56) | 1 823 | the CCM part is in the 13 888 B region |
| pid.o | 3 720 | 1 120 | Ctrler table |

Task stacks (`Global_file/creat_task.h`, `FreeRTOSConfig.h`): 7 app tasks x 500 words = 14 000 B, idle 130 words,
timer 260 words. Keil's static worst case (htm, "Max Depth") is a lower bound only: every task shows
"+ Unknown" (function pointers, cycles). Stabilizer_Task 344 B, Send_Task 432 B, SystemMonitor_Task 256 B, the
other four 208 B, timer 424 B, idle 208 B, against 2 000 B allocated.

**High-water marks are reachable today**, not over radio: `SystemMonitor_Task` (USER/main.c:257) fills
`g_task_snapshot[]` (TaskStatus_t: `usStackHighWaterMark`, `ulRunTimeCounter`) and `g_task_snapshot_total_time`
at 1 Hz, and `g_heap_min_free`; `python -m ground_station.livewatch` reads them over SWD. Not measured in this run.

PROPOSED telemetry var `rtos_budget` (default off: compile flag `RTOS_BUDGET_TELEM 0`, and a subscribe var
that no slot carries until the GS asks), filled once per second right after the snapshot:
`uint16 hwm_min_words` (min `usStackHighWaterMark` over tasks), `uint8 hwm_min_task` (its task number),
`uint8 cpu_pct` (100 - 100 x idle runtime delta / total runtime delta), `uint32 heap_min_free`. 8 B RAM, one
pass over 9 tasks at 1 Hz. Pure reduction in a host-testable `API/rtos_budget.c`; the call sits in USER/main.c
(outside WP-36 scope). Done when the values match the livewatch RTOS view.

## What changed in WP-36 (behaviour-preserving)
- `API/pid.c`: `ComputeYawPID`, the one live PID loop without guards, now zeroes the loop on non-finite
  Des/FB (via E) or a poisoned SumE/PreE, and guards U/SumE on the way out, as `ComputePID` does. Shared helpers
  `PID_Zero/PID_BadState/PID_GuardOut`; named `PID_FINITE_LIMIT`, `PID_YAW_TURN_DEG`, `PID_YAW_HALF_TURN_DEG`.
  Test `API/tests/test_pid_guards.c`: 500 037 checks, bit-identical to verbatim wp/33 copies of `ComputePID`
  (3 anti-windup modes, NaN/Inf injected) and `ComputeYawPID` (finite, incl. headings turns apart); NaN/Inf
  zeroes the loop where wp/33 put NaN/Inf into U; recovery on the next finite tick.
- `API/mrac.c`: `MRAC_DEG2RAD` for the six `0.0174533f`; `API/controller.c`: `MIX_PWM_MIN/MAX`
  (= `Motor_PWM_ZERO/MAX`), `MIX_PER_MOTOR`. Same tokens after preprocessing; EQUIV and SIL.
- `*_ROW` metadata: `@param unit [min, max] description` in the legends of PID_ROW, GAIN_LEASE_ROW and the four
  wfb tables (6 tables, 154 cells). The bounds are PROPOSED plausibility ranges, not flight-derived.
- clang-tidy fixes: `API/sys.h` BITBAND arguments parenthesized (all callers pass digit literals);
  `API/wfb_prim.c` identical HOVER/TRAJ branches merged. `const`: every control-path table was already
  const and `readability-non-const-parameter` finds nothing; the gate now enforces both.

## PROPOSED next (not done: changes behaviour, or out of scope)
1. MRAC input guard: today a NaN rate or command poisons Theta for good and `Controller_Update` falls back to
   PID for the rest of the flight. A guard that skips the tick would let MRAC re-engage: a flight decision.
2. CMD 0x01 accepts gains in 0..200 (`TASK/send_data.c:1535`) but `Z_ratePID` boots with Kp 400: the GS cannot
   write that value back. Raise the bound or make it per axis.
3. `ComputePID_locx/locy` have no caller in API/TASK/USER: delete them.
4. SRAM 95.3 %: look at the 80 KB of `bmi088_driver.o` ZI before adding buffers; CCM has 50 KiB free (no DMA there).
5. Metadata for the assignment-form tables (`MRAC_SET`, `MRAC_BASIS`, `EKF_OF_STATE`), then `row_meta --json`
   cross-checked against `capability_manifest.json` (WP-32 F4).
6. Install arm-none-eabi-gcc locally (the arm step is unverified here); run the workflow once a push happens;
   an opt-in `.git/hooks/pre-commit` that calls `tools/check.sh`.
7. WP-32 F1 pre-arm reason codes and F2 watchdog: behaviour changes, need operator approval.
