# S7 — Resource map and RTOS observability

`ground_station/platform/resources.py` now projects task and resource
descriptors from the generated registry into a CRC-bound `ResourceMap`.
Entries retain owner, nominal rate, capacity, and unit, and can be enriched
with DWARF addresses and sizes when a resolver is available. `RuntimeMetric`
provides bounded utilization for queue, heap, stack, deadline, and DMA values
without adding an unbounded telemetry surface.

Host checks pass for the generated resource map and metric utilization. The
read-only live target sample through wireless SWD showed:

```text
xTickCount                     1085469 -> 1097454
system_monitor.stabilizerTask_cnt 20485 -> 22882
DroneStatus.ARM_Status         0 -> 0
UA3RxFrameCnt                  62 -> 65
UA3RxLastLen                   58 -> 58
```

The scheduler and command ingress advanced while the aircraft remained
disarmed. Firmware-side observability is now included in the Keil project
through `firmware/rtos_observability.{h,c}`. `Send_Task` updates SWD-readable
cycle, queue-depth, and USART3-busy counters once per cycle. The guarded
rebuild and flash completed with `0 Error(s)`, `Verify OK`, and a post-download
`SYSRESETREQ` in `State.RUNNING`.

Post-flash read-only SWD samples showed:

```text
platform_obs_send_ticks 4922 -> 7824
platform_obs_queue_depth    0 -> 0
platform_obs_dma_busy        0 -> 0
xTickCount             61174 -> 97237
UA3TxFrames             4921 -> 7825
UA3TxDrops                 0 -> 0
UA3TxPeak                 76 -> 76
DroneStatus.ARM_Status      0 -> 0
```

The Send_Task counter and TX-frame counter advanced together, queue depth and
DMA busy stayed bounded, TX drops remained zero, and the scheduler stayed live
while disarmed. Resource-map and metric tests pass 19 tests. S7 is complete;
stack watermark remains an optional future metric because the current build
does not enable FreeRTOS trace allocation hooks.
