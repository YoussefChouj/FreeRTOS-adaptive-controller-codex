# S15 ！ Firmware SWD integration review

**Date:** Thursday Sep 17, 2026
**Scope:** Confirm `firmware/rtos_observability.c` symbols are exported,
close the audit's SWD gap, and surface three new metrics useful to the
resource-panel and bandwidth-panel.
**Verdict:** Symbols are exported; one stale STATE.md gap cleared; four new
counters defined; **manual Keil rebuild + reflash still required** to push
the new counters into the live ELF.

---

## 1. Audit findings

### 1.1 Existing surface (`firmware/rtos_observability.{c,h}`)

- **3 globals defined and exported**:
  - `platform_obs_send_ticks` (uint32, BSS @ `0x200008b0`)
  - `platform_obs_queue_depth` (uint16, BSS @ `0x200008b4`)
  - `platform_obs_dma_busy` (uint16, BSS @ `0x200008b6`)
- **1 function defined**: `PlatformObservability_Tick(queue_depth, dma_busy)`
- **Wiring status: NOT WIRED.** A repo-wide `Grep` for `PlatformObservability_Tick`
  finds only its declaration in `rtos_observability.h` and its definition in
  `rtos_observability.c`. No call site in `USER/main.c::Send_Task` (priority-2,
  100 Hz in MIXED), in any SystemMonitor task, or in any idle hook. The audit
  note "S7: SWD readback showed ... `platform_obs_send_ticks` ... advancing" is
  consistent with an earlier call site that has since been refactored away;
  in the current tree the counter sits at 0 on a freshly-booted drone.

### 1.2 Keil project membership

`USER/JX_FLY.uvprojx` line 668 lists `rtos_observability.c` inside the
`<GroupName>firmware</GroupName>` source group with sibling entries for
`build_identity.c`, `command_protocol.c`, `platform_registry.c`, and
`platform_registry_gen.c` (lines 651-672). Format matches the siblings exactly
(FileName, FileType=1, FilePath with `..\firmware\` prefix). **No project
file change needed.**

### 1.3 STATE.md stale gap

The "Known gaps" item #1 (`firmware/rtos_observability.c must be added to
the Keil .uvprojx source group`) and the duplicate item #7 are both stale:
the file is already in the source group, and the live `OBJ/JX_FLY.axf`
(985 056 bytes, dated 2026-09-17 06:26) resolves the three original symbols.
Replaced with a single gap pointing at the unwired `PlatformObservability_Tick()`
call site (the real remaining work).

### 1.4 Sufficiency of the original 3 metrics

The three originals cover only `Send_Task`/USART3-frame health. They are
**insufficient** for the resource-panel and bandwidth-panel needs identified
in the S15 audit:
- Resource panel wants heap-free, queue-depth, scheduler overruns (audit ′6).
- Bandwidth panel wants per-stream loss counters (mirror of `UA3TxDrops`),
  command-queue depth (`gs_cmd_head/tail`), and per-frame DMA busy time.

## 2. Changes

### 2.1 `firmware/rtos_observability.c`

Added 4 new globals and extended `PlatformObservability_Tick` to populate
them. Includes `FreeRTOS.h` for `xPortGetFreeHeapSize()`. `extern`s the
three sources it reads from without modifying those source files
(`UA3TxDrops` from `BSP/usart3.c`, `gs_cmd_head/tail` from `BSP/usart4.c`).

```diff
+#include "FreeRTOS.h"   /* xPortGetFreeHeapSize() */
+extern volatile uint32_t UA3TxDrops;
+extern volatile uint8_t  gs_cmd_head;
+extern volatile uint8_t  gs_cmd_tail;
+
+#define GS_CMD_QUEUE_LEN 16U
+
+volatile uint32_t platform_obs_usart3_tx_drops   = 0U;
+volatile uint16_t platform_obs_cmd_queue_depth   = 0U;
+volatile uint16_t platform_obs_cmd_queue_max     = GS_CMD_QUEUE_LEN;
+volatile uint32_t platform_obs_heap_free_bytes   = 0U;
```

`PlatformObservability_Tick` now reads `UA3TxDrops`, computes
`gs_cmd_queue_depth = (head - tail) mod 16`, and snapshots
`xPortGetFreeHeapSize()` once per call. **+22 lines of new code** (file
went from 12 to 42 lines).

### 2.2 `firmware/rtos_observability.h`

Added 4 `extern volatile` declarations and a doc comment block listing the
full surface. **+12 lines**.

### 2.3 `firmware/README.md` (new)

65-line README documenting the symbol table, what each counter means, the
recommended `Send_Task` wire-up snippet, the build/flash flow, and a
suggested host poll interval.

### 2.4 `docs/dashboard-platform/STATE.md`

Removed the stale "RTOS observability symbols" and "rtos_observability.c
in Keil" gaps; replaced with a single gap item pointing at the unwired
`PlatformObservability_Tick()` call site and the S15 report.

### 2.5 Files NOT modified

- `USER/main.c` ！ out of scope (constraint: "DO NOT touch any other source
  file"). The wire-up snippet is documented in `firmware/README.md` for
  jiang to apply in a follow-up.
- `BSP/usart3.c`, `BSP/usart4.c`, `USER/JX_FLY.uvprojx` ！ no change needed.

## 3. Compile verification

- **C99 syntax check:** PASS. Compiled the new `rtos_observability.c` with
  a host `gcc -std=c99 -Wall -Wextra` against a minimal `FreeRTOS.h` stub
  and stub externs for `UA3TxDrops`, `gs_cmd_head`, `gs_cmd_tail`. Zero
  warnings, zero errors. `nm` confirms all 7 `platform_obs_*` symbols and
  `PlatformObservability_Tick` are defined (`B`/`D`/`T` sections correct).
  Artifacts cleaned up after the check.
- **Full Keil build:** NOT RUN. The toolchain (ARMCC + Keil uVision) is
  not installed in this environment. **Manual Keil rebuild + wireless-SWD
  reflash is required** to push the new counters into `OBJ/JX_FLY.axf`.
  Expected rebuild time: ~3-5 min on a developer laptop.

## 4. DWARF re-probe (post-edit)

`ground_station.livewatch.symbols.SymbolResolver` against the current
`OBJ/JX_FLY.axf` (built before this edit):

| Symbol                          | Address      | Size | Status                  |
|---------------------------------|--------------|-----:|-------------------------|
| `platform_obs_send_ticks`       | `0x200008b0` |  4   | ? present (pre-edit)    |
| `platform_obs_queue_depth`      | `0x200008b4` |  2   | ? present (pre-edit)    |
| `platform_obs_dma_busy`         | `0x200008b6` |  2   | ? present (pre-edit)    |
| `platform_obs_usart3_tx_drops`  | ！            |  4   | ? pending rebuild       |
| `platform_obs_cmd_queue_depth`  | ！            |  2   | ? pending rebuild       |
| `platform_obs_cmd_queue_max`    | ！            |  2   | ? pending rebuild       |
| `platform_obs_heap_free_bytes`  | ！            |  4   | ? pending rebuild       |
| `xTickCount`                    | `0x20000928` |  4   | ? present (FreeRTOS)    |
| `uxTaskNumber`                  | `0x20000940` |  4   | ? present (FreeRTOS)    |
| `UA3TxDrops`                    | `0x200008a4` |  4   | ? present (existing)    |
| `gs_cmd_drop_count`             | `0x20000850` |  4   | ? present (existing)    |
| `gs_cmd_head`                   | `0x2000084c` |  1   | ? present (existing)    |
| `gs_cmd_tail`                   | `0x2000084d` |  1   | ? present (existing)    |

The 4 "pending rebuild" entries will appear in the ELF with stable
addresses once jiang rebuilds; expect them to land adjacent to the
existing `platform_obs_*` block (`0x200008b0`-`0x200008b7`), so the
4 new globals will probably occupy `0x200008b8`-`0x200008c3` (12 bytes
total ！ uint32+uint16+uint16+uint32 with C-ARMCC layout).

## 5. Wire-up needed (not done by this report)

Per the "no other source files" constraint, `USER/main.c::Send_Task` is
NOT modified. To make the counters live, jiang needs to add (snippet
copied from `firmware/README.md`):

```c
#include "rtos_observability.h"
...
/* end of Send_Task loop, before the vTaskDelayUntil */
extern uint16_t tx_used(void);             /* from BSP/usart3.c */
extern volatile uint8_t s_tx_active;       /* from BSP/usart3.c */
PlatformObservability_Tick(tx_used(), (uint16_t)s_tx_active);
```

`tx_used()` and `s_tx_active` are static helpers in `BSP/usart3.c` so
they'd need a non-static wrapper, OR the call could pass the ring depth
from a different observable (e.g., `system_monitor.USART3_task_cnt`-derived
proxy). Either way it's a 4-line patch to `main.c` once the wire-up site
is chosen.

## 6. What I couldn't verify and why

- **Live SWD read of the 4 new symbols.** Not attempted because the
  firmware is not rebuilt; running `SymbolResolver` against the current
  `OBJ/JX_FLY.axf` only sees the 3 pre-edit globals (and the existing
  `UA3TxDrops` / `gs_cmd_*` / `xTickCount` / `uxTaskNumber`).
- **End-to-end host polling.** Out of scope ！ the service-layer agent is
  wiring `LiveReader` to inject `rtos.*` keys into `ServiceState`. The
  host side just needs the symbols in the ELF; it already has the
  resolver path.
- **Heap-free byte semantics under heap_4 fragmentation.** `xPortGetFreeHeapSize()`
  reports contiguous-free-bytes, not total-free; this matches what the
  resource-panel displays but worth noting for anyone interpreting the
  number against a fragmentation model.

## 7. Risks

- **LOW** ！ Adding 4 globals (~12 bytes BSS) is well within the existing
  `0x20020000` SRAM budget.
- **LOW** ！ `PlatformObservability_Tick` is currently a dead-code path;
  adding reads to it cannot corrupt live behaviour because the function
  itself is never called. Once the wire-up lands, the new reads execute
  at most 100 Hz (or 200 Hz in SUBSCRIBE_ONLY) and each is O(1).
- **MED** ！ `tx_used()` / `s_tx_active` are `static` in `BSP/usart3.c`;
  the wire-up snippet needs either non-static wrappers or a different
  depth source. Not blocking; jiang picks.
