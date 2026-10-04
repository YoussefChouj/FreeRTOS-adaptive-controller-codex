# Stack budget (2026-10-05)

`python tools/stack_budget.py` (gate step `stack` in `tools/check.sh`) reads the Keil call graph `OBJ/JX_FLY.htm`
("[Stack] Max Depth" per function) and the task stack sizes, and fails when a task's call-graph depth plus the
context frame exceeds its allocation. Every Keil link rewrites the call graph, so a new deep call chain shows up
at the next build. PX4 does the same check at run time (`top`, stack checks); here it is static, at build time.

## Numbers (call graph of Sun Oct 04 21:58:49 2026, computed, not measured on the drone)

| Task | Allocated | Depth | + frame 204 B | Margin |
|---|---|---|---|---|
| start_task | 512 B | 636+U | 840 | -64 % (malloc-failed path only, below) |
| SystemMonitor_Task | 2000 B | 256+U | 460 | 77 % |
| IMU_DataDeal_Task, IMUSample_Task, Remoter_Task, Autofly_Task | 2000 B | 208+U | 412 | 79 % |
| Stabilizer_Task | 2000 B | 360+U | 564 | 72 % |
| Send_Task | 2000 B | 432+U | 636 | 68 % |
| prvIdleTask | 520 B | 208+U | 412 | 21 % |
| prvTimerTask | 1040 B | 424+U | 628 | 40 % |

The context frame is the worst case for the ARM_CM4F port: hardware frame 8 + 18 FP words, plus r4-r11, r14 and
s16-s31 pushed by PendSV, 51 words in all. "+U" means the linker could not bound part of the tree (function
pointers, assembler), so the depth is a lower bound. `dbg_report_task` is created in `USER/main.c` but not linked.

## Finding: start_task overflows on the malloc-failed path

`start_task` (128 words) creates every task. If a `pvPortMalloc` fails, `vApplicationMallocFailedHook` calls
`FaultCapture_Record` (524 B deep) on that 512 B stack. The overflow lands in the heap block below the stack
while the fault is being recorded, so the record that should explain the failure can be corrupted. The normal
path is about 256 B (start_task 16 + xTaskCreate 72 + prvInitialiseNewTask 168), 460 B with the frame, so boot
is fine today: the heap (20,480 B) has room for every task.

Fix (PROPOSED, committed on branch `ram-savings` 6e7d192, not built, not flashed): `START_STK_SIZE` 128 -> 256
words. start_task deletes itself after boot and heap_4 frees its stack, so the cost is 512 B of peak heap during
boot only. When that branch merges, remove `start_task` from `KNOWN_OVER` in the tool.

## Headroom (PROPOSED, not measured)

The seven 2000 B task stacks are 14,000 B of the 20,480 B heap. The call graph needs at most 636 B of each.
Before shrinking any of them, read `hlth.stack_min_words` (minimum `usStackHighWaterMark` over the tasks, from
`TASK/systemmonitor_task.c`) on the bench through a full flight profile: no session log has it yet.

## Interrupt stack (MSP, partial)

MSP is 1,024 B (`stm32_lib/startup_stm32f40_41xxx.s`, `Stack_Size`). The deepest handlers in the call graph are
USART1 184+U, SysTick 168+U, PendSV 144+U, USART3 136 B; `HardFault_Handler` is 524 B. Handlers at different
preemption priorities nest, and the BSP uses at least four levels (kernel 15, 6, 5, 0). A nesting bound needs
the full priority map of every `NVIC_Init` call: PROPOSED as a second check in the same tool.
