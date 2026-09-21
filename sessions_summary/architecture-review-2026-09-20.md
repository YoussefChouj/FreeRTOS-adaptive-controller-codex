# Architecture Review - 2026-09-20

Friction patterns found in the STM32F4 firmware (`API/`, `TASK/`, `BSP/`, `USER/`,
`Global_file/`, `firmware/`) and its host-side contract surface
(`registry/`, `scripts/`, `ground_station/comm/tests/`).

## Scope and prerequisites

The skill asks for `CONTEXT.md` and `docs/adr/` to be read first. **Neither exists in
this repo**, and neither does `sessions_summary/` (created by this report). Consequences:

- **No ADR conflicts can be checked.** Nothing below is marked `Blocked by ADR-NNNN`
  because there are no ADRs to be blocked by. That is itself a finding: the load-bearing
  decisions in this project (EKF stays in shadow mode; UART5 subscribe is compiled out;
  the arm gate fails closed) live only in `AGENTS.md`, code comments and session reports.
- Domain vocabulary was taken from `docs/glossary.md` and `docs/architecture/`, the only
  substitutes available.

Exploration was deliberately non-exhaustive: 7 survey passes over the task/stack
configuration, all 12 live ISRs, the test inventory, and the registry codegen pipeline.

---

## Candidate 1: Stack budget has no headroom telemetry (pattern #5)

**Files**: `Global_file/creat_task.h`, `FreeRTOS/include/FreeRTOSConfig.h`,
`USER/main.c`, `firmware/rtos_observability.c`

**Friction**: Every task stack is sized by copy-paste, and nothing on the running vehicle
reports how much of it is actually used. A stack change is therefore unfalsifiable: you
cannot tell whether `500` words is 3x too much or 10% from a hard fault, so nobody dares
change it and nobody can justify adding work to a task.

Measured:

| Item | Value |
|---|---|
| `configTOTAL_HEAP_SIZE` (`FreeRTOSConfig.h:122`) | 20 * 1024 = **20,480 B** |
| Tasks actually created | **8** (start + 7 app tasks) |
| Stack words | 128 + 7 x 500 = **3,628 words** |
| Stack bytes | **14,512 B = ~71% of the heap** |
| Timer task (`configTIMER_TASK_STACK_DEPTH`, `:151`) | 260 words = 1,040 B |
| TCBs | ~100 B x 8 |
| `uxTaskGetStackHighWaterMark` call sites in project code | **0** |

`grep -rn "uxTaskGetStackHighWaterMark" --include=*.c --include=*.h . | grep -v FreeRTOS/`
returns empty. `firmware/rtos_observability.c` does not log it.

Every app task gets the same 500 words regardless of workload - `IMU_DataDeal`,
`Stabilizer`, `Remoter`, `Autofly`, `SystemMonitor` and `Send` are not comparable
consumers. Macro naming is inconsistent (`STABILIZER_Task_TASK_PRIO` has a doubled infix,
`SENDTASK_PRIO` lacks `_TASK_`), which is a small sign that the block is edited by
duplication rather than by design.

A schedulability note while we are here: **five tasks share priority 4**
(`IMU_DataDeal`, `IMUSample`, `Stabilizer`, `Remoter`, `Autofly`). They round-robin
against each other, so the stabilizer loop's jitter is coupled to the other four.

**Root cause**: stack budget treated as a constant, not as a measured resource. The
project has *detection* but not *budgeting*.

**Credit where due - detection already exists.** `configCHECK_FOR_STACK_OVERFLOW 2`
(`FreeRTOSConfig.h:110`) runs the sentinel-byte check on every context switch, and
`USER/main.c::vApplicationStackOverflowHook` writes `g_stack_overflow_marker` and
`g_stack_overflow_task_crc` so `livewatch` can name the offending task post-mortem over
SWD. This candidate is not "we could crash blind"; it is "we cannot right-size, and we
cannot see the margin shrink before it hits zero."

**Proposal**: Add a high-water-mark sweep to the existing `systemmonitor_task`, publish
it through `firmware/rtos_observability.c` as a small static array of
`{task name CRC, high_water_words}`, and surface it in the dashboard's existing
`panel-rtos-resources`. Then right-size the per-task `*_STK_SIZE` macros from one flight's
worth of data and normalise the macro names in the same commit.

**Benefit**:
- Testability: stack margin becomes a number a test or a bench run can assert on, rather
  than a thing discovered by hard fault.
- Parity: n/a (no sim counterpart).
- Safety: directly. Reclaiming even 1,000 words of over-allocation returns 4 kB to a
  20 kB heap; and a shrinking margin becomes visible *before* the sentinel fires.

**Cost**:
- Code churn: ~30 lines in `systemmonitor_task.c` + `rtos_observability.c/.h`, plus the
  macro edits in `creat_task.h`. One dashboard panel field.
- Risk: low for the instrumentation (read-only FreeRTOS API, runs in an existing
  low-priority task). Medium for the resizing step, which must be a separate commit and
  must be flown before the stacks are trimmed - never trim on the same flash as the
  measurement.
- Runtime: `uxTaskGetStackHighWaterMark` walks the stack to the sentinel; at 1 Hz in the
  monitor task the cost is negligible. Flash cost is tens of bytes.

**Recommendation**: `Strong`

---

## Candidate 2: No golden vectors for any numeric module (patterns #4 and #1)

**Files**: `API/ekf.c`, `API/ekf_of.c`, `API/mrac.c`, `API/mrac_math.c`, `API/SINS.c`,
`API/thrust_estimators.c`, `API/tests/`

**Friction**: The hardest math in the project has no ground truth. A sign flip, a gain
scaled by 100, or a regressor basis reordered would pass every test that exists and would
only show up as bad flight behaviour.

Measured:

| Item | Value |
|---|---|
| `API/*.c` modules | **25** |
| C test files in `API/tests/` | **2** (`test_mrac_sigma_prior.c`, `test_subscribe_harness.c`) |
| Golden / reference datasets on disk | **0** |

`find . -iname "*golden*" -o -iname "*reference*.csv"` (excluding `OBJ/`, `__pycache__/`,
`stm32_lib/`) returns empty. `ekf.c`, `ekf_of.c`, `mrac_math.c`, `SINS.c` and
`thrust_estimators.c` have no test of any kind.

This is also the pattern #1 (sim<->firmware parity) finding in disguise: there is no
Python reference implementation to drift *from*, so "parity" currently has no meaning
here. The absence of a reference is the gap.

**Root cause**: numeric code was ported/tuned against the vehicle rather than against a
dataset, so the vehicle is the only oracle.

**Proposal**: Start with **one** module, `mrac_math.c`, because it is pure (no hardware,
no globals), it already has a partial test neighbour (`test_mrac_sigma_prior.c`), and it
is the module whose silent drift is most expensive. Capture input/output pairs from a
trusted reference (SciPy or hand-calculation), store them as a CSV under
`API/tests/golden/`, and add a host-compiled test that pins the C against them. Then
repeat for `SINS.c` and the EKFs only if the first one proves its worth.

**Benefit**:
- Testability: converts "the drone flew fine" into a bench-checkable assertion.
- Parity: creates the artifact a future Python sim would be pinned against, so parity
  becomes enforceable rather than aspirational.
- Safety: catches sign/scale errors on the bench instead of in the air.

**Cost**:
- Code churn: one CSV + one test file + a small host build rule. No firmware change.
- Risk: very low - nothing on the vehicle changes. The real cost is deciding what the
  trusted reference *is*, which is a judgement call, not a coding one.
- Runtime: zero (host-only).

**Recommendation**: `Strong` (scoped to one module; `Worth exploring` for the full sweep)

---

## Candidate 3: Interrupt-file ownership and dead flag blocks (patterns #2, #3, #7)

**Files**: `TASK/stm32f4xx_it.c`, `BSP/usart3.c`, `BSP/usart5.c`, `USER/main.c`

**Hypothesis tested and REFUTED.** The obvious pattern-#2 suspicion - heavy float math in
an ISR - is **not true here**. All 12 live ISRs in `stm32f4xx_it.c` are between 3 and 28
lines:

```
USART1 14   USART2 17   DMA1_S6  7   USART3 28   DMA1_S3  9   EXTI0 7
EXTI1   7   EXTI9_5 12  UART4   20   UART5   18   DMA1_S7  7   USART6 3
```

The float union and `* 100.0f` scaling at `stm32f4xx_it.c:283-305` sit **outside** any
handler (`UART4_IRQHandler` ends at line 279); they belong to file scope and to
`void Decode_RX_Data_t265(void)`. So the real friction is narrower than the pattern
description suggests.

**Friction** (what is actually there):

1. **Non-static file-scope mutable state in the interrupt file**: the
   `union { float data_float; char cdata[4]; } data_to_float;` and `float U4_RX_Data` at
   `:283-287` have external linkage and are mutated by decode logic living in the vector
   file. Anything in the image can touch them and nothing declares who owns them.
2. **Split parser ownership across the USART3 seam**: the USART3 handler body was moved
   into `BSP/usart3.c` so the ring tail pointer stays private (comment at `:164`), but the
   parser that consumes it still lives in `BSP/usart5.c:188,493`. Reading the USART3 path
   end-to-end means three files.
3. **Dead code and duplicated headers**: commented-out handler bodies at `:149`
   (`//void USART3_IRQHandler1`) and `:206` (`//void DMA1_Stream1_IRQHandler`), and two
   copies of the UART5 comment header at `:410` and `:424`.
4. **Two `#if 0` blocks in `USER/main.c`** (pattern #7): the `dbg_report_task` creation at
   `:210-220` plus its helper block at the top of the file. They are correctly
   cross-referenced by comment, which is better than most dead code, but they are why a
   reader counting `xTaskCreate` calls gets 9 tasks when the image runs 8.

**Root cause**: hardware seam drawn halfway. The USART3 move was the right refactor, done
to one file out of three.

**Proposal**: Finish the seam rather than redesign it. Make `data_to_float` and
`U4_RX_Data` `static`, move `Decode_RX_Data_t265` next to the other UART4 code, relocate
the USART3 parser out of `usart5.c` into `usart3.c`, and delete the four dead blocks. Do
not touch the `#if 0` debug task - it is documented and cheap.

**Benefit**:
- Testability: marginal. A parser that lives in one file with the ring it reads is
  stub-testable; today it is not.
- Parity: n/a.
- Safety: removes external linkage on ISR-adjacent state, which is a real (if unexercised)
  aliasing hazard.

**Cost**:
- Code churn: 4 files, roughly 80 lines moved and 40 deleted. All GBK-encoded, so every
  edit must go through the latin-1 round-trip.
- Risk: **medium and worse than it looks.** Moving parser code across translation units in
  a build with 70 warnings, on the receive path of the live telemetry link, risks a silent
  behavioural change that only a flight would reveal. `DMA1_Stream3_IRQHandler` carries a
  comment saying it absolutely must not be removed or the system hangs.
- Runtime: none intended.

**Recommendation**: `Worth exploring` for the `static` + dead-code half (cheap, contained);
`Risky` for the parser relocation until candidate 2 gives us a way to test a receive path
off-vehicle.

---

## Candidate 4 (recorded, largely already solved): protocol schema drift (pattern #6)

**Files**: `registry/platform_registry.yaml`, `scripts/generate_platform_registry.py`,
`scripts/validate_protocol_schema.py`, `firmware/platform_registry_gen.c/.h`,
`ground_station/comm/tests/test_protocol_schema.py`

This project has **already built** the fix pattern the skill prescribes: a single YAML
source of truth, a generator that emits the firmware C, and four tests that pin the
schema against the host parsers (`test_protocol_schema_matches_host_contracts`,
`test_schema_declares_all_parser_owned_frames`, `test_frame_b_formula_matches_basis_six_parser_size`,
`test_schema_pins_crc_family_for_stream_data`). The generated files are currently fresh -
`platform_registry_gen.c/.h` (Sep 17 03:10) are newer than
`platform_registry.yaml` (Sep 16 23:47).

**Residual gap, precisely stated**: `test_protocol_schema.py` imports only
`scripts.validate_protocol_schema`. Nothing regenerates `platform_registry_gen.c/.h` and
diffs it against the YAML, and **no CI configuration anywhere in the repo references
either script**. So the YAML->C half of the pipeline is enforced by memory, not by a test.
Edit the YAML, forget to run the generator, and every test still passes.

**Proposal**: one test that shells out to the generator into a temp dir and asserts the
output is byte-identical to the checked-in files.

**Cost**: ~20 lines, host-only, zero firmware risk.

**Recommendation**: `Strong` as a 20-minute chore; too small to be the headline candidate.

---

## Top recommendation

**Candidate 1 (stack budget telemetry), followed immediately by Candidate 4 as a chore.**

Reasoning - impact x feasibility x risk:

- **Impact is the highest and the most concrete.** 14,512 B of stack against a 20,480 B
  heap is the tightest measured resource in the system, and it is the one resource with
  zero observability. Every future feature (a new task, a deeper call chain in the
  stabilizer, a bigger telemetry buffer) spends from a budget nobody can read.
- **Feasibility is high.** The landing spots already exist: `systemmonitor_task` is the
  natural host, `firmware/rtos_observability.c` is the natural publisher, and
  `panel-rtos-resources` in the dashboard is the natural display. Nothing new has to be
  invented, and the read path (SWD `livewatch read`, which works with the service running)
  is already proven.
- **Risk is separable.** The instrumentation commit is read-only and low risk; the
  stack-resizing commit is where the risk lives and can be gated on a flight's worth of
  measured data. That split is what makes this safe to start.
- Candidate 2 is the better *long-term* investment but needs a decision about what the
  trusted reference is, which is a conversation, not a task. Candidate 3 is half cheap and
  half genuinely risky, and its risky half is unlocked by Candidate 2 - so it should wait.

Candidate 4 is recommended alongside rather than instead: it is a small, zero-risk test
that closes a real hole in an otherwise well-built pipeline.

Which candidate do you want to explore?
