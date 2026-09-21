# Firmware ùù STM32F407 FreeRTOS + MRAC adaptive controller

ARMCC-built (Keil uVision V5.06) C99 sources for the FreeRTOS-adaptive
controller drone. Source files live at `USER/` (legacy flight code),
`TASK/` (FreeRTOS task stubs), `BSP/` (board support), `Global_file/`,
`FreeRTOS/`, and `firmware/` (platform glue). Keil project file is
`USER/JX_FLY.uvprojx`.

## RTOS observability surface (`firmware/rtos_observability.c`)

Plain `volatile` globals read by the SWD/livewatch host over the wireless
debugger. No new wire frame; no extra transport. The host resolves the
symbol via the firmware ELF's DWARF info (`ground_station.livewatch.symbols`)
and reads its bytes directly.

| Symbol                          | Type     | Source / meaning                                       |
|---------------------------------|----------|--------------------------------------------------------|
| `platform_obs_send_ticks`       | uint32   | Send_Task loop counter, increments each call           |
| `platform_obs_queue_depth`      | uint16   | Last reported USART3 TX ring depth (bytes used)        |
| `platform_obs_dma_busy`         | uint16   | Last reported USART3 DMA-busy flag                     |
| `platform_obs_usart3_tx_drops`  | uint32   | Mirror of `UA3TxDrops` ùù ring-full / refused frames    |
| `platform_obs_cmd_queue_depth`  | uint16   | gs_cmd ring depth at last tick (`head`/`tail` wrap)    |
| `platform_obs_cmd_queue_max`    | uint16   | Compile-time `GS_CMD_QUEUE_LEN` (16)                   |
| `platform_obs_heap_free_bytes`  | uint32   | `xPortGetFreeHeapSize()` snapshot (heap_4)             |

`PlatformObservability_Tick(queue_depth, dma_busy)` is the existing tick
handler. It now also updates the three S15 counters. **Caller wiring is
required** ùù see below.

## Tick wiring (caller side ó DONE)

The function is declared and the call site is wired:

```c
/* TASK/send_data.c ó Send_Groundstation_Telemetry_UART4(), first statement */
PlatformObservability_Tick(
    (uint16_t)((gs_cmd_head + 16U - gs_cmd_tail) % 16U),
    (uint16_t)(Usart3_Stream_Busy() != 0U));
```

That is the only call site. It fires once per Send_Task cycle (~80 Hz in
MIXED mode, ~200 Hz in SUBSCRIBE_ONLY). The legacy queue-depth and
dma-busy arguments feed the S7 counters; the S15 counters (UA3 drops,
cmd queue depth, free heap) are refreshed by the function itself from
externs. No additional wiring is required when adding new S-series
counters ó declare them in `firmware/rtos_observability.h`, mirror them
in the body of `PlatformObservability_Tick`, and they go live on the
next rebuild.

## Build / flash

The Keil project compiles the file under the `<Group>firmware</Group>`
source group (already present in `USER/JX_FLY.uvprojx`, see line 668).
Rebuild in uVision, then flash via the wireless SWD adapter using
`python -m ground_station.flashtool.flash`. The post-flash ELF
(`OBJ/JX_FLY.axf`) carries DWARF debug info; resolve new symbols with:

```python
from ground_station.livewatch.symbols import SymbolResolver
r = SymbolResolver('OBJ/JX_FLY.axf')
print(hex(r.resolve('platform_obs_heap_free_bytes').address))
```

## Suggested host poll interval

100 ms is a good default: matches Send_Task's 100 Hz cadence in MIXED
mode without thrashing the SWD link. The bandwidth-panel can pull the
counters once per dashboard refresh (~250 ms).
