# S15 ！ Firmware SWD integration

## Objective

Close the SWD observability gap called out in `reports/S15-audit.md` ′6:
confirm `firmware/rtos_observability.c` symbols are exported, add new
metrics the resource-panel and bandwidth-panel need, and clear the stale
"file not in Keil project" gap in STATE.md.

## What I did

1. **Audit** `firmware/rtos_observability.{c,h}` + repo-wide Grep.
   - 3 globals exported, 1 function defined, **function never called**.
   - `USER/JX_FLY.uvprojx` line 668 already includes the file.
2. **DWARF probe** of `OBJ/JX_FLY.axf` (985 KB, pre-edit) via
   `ground_station.livewatch.symbols.SymbolResolver`:
   - ? `platform_obs_send_ticks` 0x200008b0 / 4
   - ? `platform_obs_queue_depth` 0x200008b4 / 2
   - ? `platform_obs_dma_busy` 0x200008b6 / 2
   - ? `xTickCount`, `uxTaskNumber`, `UA3TxDrops`, `gs_cmd_*`
3. **Extend** `rtos_observability.c` with 4 new globals and one new
   `extern`-pulled source (`UA3TxDrops`, `gs_cmd_head/tail`). Added
   `#include "FreeRTOS.h"` for `xPortGetFreeHeapSize()`. Total +22 lines.
4. **Header** updated with 4 new extern declarations + doc block.
5. **`firmware/README.md`** (new, 65 lines) ！ full symbol table,
   recommended wire-up snippet, build/flash notes.
6. **`STATE.md`** ！ replaced 2 stale "rtos_observability.c in Keil"
   gaps with a single gap pointing at the unwired call site.
7. **Compile check** with host gcc -std=c99 -Wall -Wextra against a
   stub FreeRTOS.h ！ clean, 0 warnings, 0 errors. `nm` confirms all 7
   globals + function are exported. **Keil rebuild not run.**
8. **DWARF re-probe** (post-edit) ！ 4 new symbols still MISSING because
   the ELF is pre-rebuild. Will appear after jiang reflash.

## New symbol surface (post-rebuild)

| Symbol                          | Type   | Source                            |
|---------------------------------|--------|-----------------------------------|
| `platform_obs_usart3_tx_drops`  | uint32 | mirror of `UA3TxDrops`            |
| `platform_obs_cmd_queue_depth`  | uint16 | `(head - tail) mod 16` of gs_cmd  |
| `platform_obs_cmd_queue_max`    | uint16 | compile-time constant 16          |
| `platform_obs_heap_free_bytes`  | uint32 | `xPortGetFreeHeapSize()` snapshot |

## Files changed

- `firmware/rtos_observability.c` ！ +22 lines
- `firmware/rtos_observability.h` ！ +12 lines
- `firmware/README.md` ！ new, 65 lines
- `docs/dashboard-platform/STATE.md` ！ gap list updated
- `docs/dashboard-platform/reports/S15-firmware-swd.md` ！ full report
- `docs/dashboard-platform/sessions/S15-firmware-swd.md` ！ this file

## Out of scope (left for next wave)

- **`USER/main.c::Send_Task` wire-up** ！ `PlatformObservability_Tick()`
  needs a single call at the end of the loop. Constraint said "DO NOT
  touch any other source file" so the snippet is in `firmware/README.md`
  for jiang to apply in a 4-line follow-up patch.
- **Service-layer LiveReader injection** ！ owned by the service agent.
- **Keil rebuild + wireless-SWD reflash** ！ required to push the 4 new
  symbols into the live ELF. jiang has the toolchain.
