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

## Interrupt stack (MSP)

The same tool parses every `NVIC_Init` in `BSP/*.c` (priority group 4: 16 preemption levels, no sub-priority).
Handlers at one level do not nest, so the worst case stacks the deepest handler of each level, plus one exception
frame (27 words with FP state and the alignment word) per nested level; the first frame lands on the task's PSP.

| Level | Handlers (depth B) | Deepest |
|---|---|---|
| 0 | DMA1_Stream3 32, UART4 40, USART2 28 | 40 |
| 5 (syscall ceiling) | UART5 24, USART1 184+U, USART3 136 | 184+U |
| 6 | EXTI0, EXTI1, EXTI9_5 28 each | 28 |
| 15 (kernel) | PendSV 144+U, SysTick 168+U | 168+U |

Nested worst case: 4 levels, 744 B of the 1,024 B MSP (73 %, computed). USART1 and SysTick reach 184/168 B only
through `configASSERT` -> `vAssertCalled` -> `printf`; their normal path is shallower.

Rule check (FreeRTOS "interrupt priorities" rule, gate fails on it): a handler above the syscall ceiling
(priority 0 here) must not call any kernel function. Today none does: the three level-0 handlers reach no code
from tasks.c, queue.c, list.c, timers.c, event_groups.c, port.c or heap_4.c. A negative test (USART2 calling
`xTaskGetTickCountFromISR`) makes the gate fail.

### Finding: a fault inside the deepest nesting overflows the MSP

`HardFault_Handler` -> `FaultCapture_Record` is 524 B deep because the record (`FaultRecord rec`, 512 B) is a
local that is then copied byte by byte into the static `fault_backup[512]`. On top of the 744 B nesting that is
1,376 B, over the 1,024 B MSP. A fault from task code (PSP) needs only 524 + 108 B and is fine.

Fix (PROPOSED, `USER/fault_capture.c`, branch after the demo): build the record in place,
`FaultRecord *rec = (FaultRecord *)fault_backup;` with `fault_backup` 4-byte aligned (`__align(4)` or a
`uint32_t[128]` array), and drop the copy loop. RAM cost 0, MSP need about -512 B, flash a little less. The same
fix shrinks the start_task malloc-failed path (above) from 636 B to about 124 B.

Related finding (static, from `OBJ/JX_FLY.map`): the comment on `fault_backup` says the record survives a warm
reset, but `fault_backup` is in `fault_capture.o(.bss)` and `fault_captured` in `fault_capture.o(.data)`
(0x20000054). armcc's `__main` zero-fills ZI and re-copies RW on every reset, so after any reset
`fault_captured` is back to `FAULT_STATE_EMPTY` and `FaultRecord_Persist` never sees `FAULT_STATE_SAVED`. Today the
record is readable only over SWD while the handler spins (`for (;;)`). Fix (PROPOSED, needs the scatter file, so
operator-owned): put both in an `UNINIT` execution region and validate them by `magic` + `crc16` at boot.
