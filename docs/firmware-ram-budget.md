# Firmware RAM budget (2026-10-05)

Source: `OBJ/JX_FLY.map` from the 2026-10-04 14:40 link. The 21:58 build reported RW 2852 + ZI 136744 = 139,596 B
in total, 324 B more than this map. Nothing below has been changed in the flight image. Every saving is PROPOSED
and should be done after the 2026-10-06 demo, one per commit, with a Keil build, a bench run and a `g_rtos_budget`
check.

## Where the RAM is

| Region | Size | Used | Free |
|---|---|---|---|
| SRAM (0x20000000) | 131,072 B | 125,000 B (95 %) | 6,072 B (about 5.7 KB after the 21:58 build) |
| CCM (0x10000000) | 65,536 B | 14,288 B (MRAC 2,008 + waypoint buffer 12,280, section `MRAC_CCM`) | 51,248 B |

| Symbol | Bytes | File | What it is |
|---|---|---|---|
| `Status_offset` | 60,000 | API/bmi088_driver.c (PROTECTED) | gyro samples for the start-up drift check, 3 x CALI_NUM 5000 floats |
| `ucHeap` | 20,480 | heap_4.c | FreeRTOS heap: every task stack, queue and semaphore |
| `Num_offset` | 20,000 | API/bmi088_driver.c (PROTECTED) | the sample index 0..4999 stored as floats |
| `s_tx_ring` | 4,096 | BSP/usart3.c | UART3 TX DMA ring |
| subscribe buffers | about 5,800 | API/subscribe.c | stream / reply buffers (several are DMA sources) |

The two calibration arrays are 80,000 B, 64 % of the SRAM in use. They are only read once, by
`leastSquareLinearFit`, which returns the slope (the drift check rejects |slope| > 5e-5 and restarts).

## Savings, best first (all PROPOSED)

| # | Change | SRAM saved | Cost / risk | Proof needed |
|---|---|---|---|---|
| R1 | Move `ucHeap` to CCM: `configAPPLICATION_ALLOCATED_HEAP 1` and define `ucHeap` with `__attribute__((section("MRAC_CCM")))` in a non-protected file | 20,480 B | CCM is zero-wait for data, so no slowdown. DMA cannot reach CCM. Audited tonight: every DMA memory address (`usart3/4/5.c`, `send_data.c`, `subscribe.c` reply buffers) is a static buffer, none is on a task stack or the heap. A future DMA from a local buffer would fail silently, so add a comment at `configTOTAL_HEAP_SIZE`. | Keil build, bench run, `g_rtos_budget` stack high-water marks unchanged |
| R2 | Replace the two calibration arrays with running sums (sum x, sum y, sum xy, sum x^2 per axis) and compute the same slope | about 79,900 B | Protected file: needs operator sign-off. Float sums in the same sample order may give the same slope bit for bit; this is not yet shown. | Host test: old `leastSquareLinearFit` on recorded gyro data vs running sums, slope equal or within 1e-9 |
| R3 | `Num_offset` only (keep `Status_offset`): compute x = i inside the fit | 20,000 B | Same protected file, smaller change than R2 | Same host test |

R1 alone takes SRAM from 95 % to 80 % used and CCM from 22 % to 53 %. R2 on top takes SRAM to about 19 % used.
Not proposed: shrinking `Heap_Mem` (512 B C-library heap) or the subscribe buffers. The gain is small and several of
those buffers are DMA sources.
