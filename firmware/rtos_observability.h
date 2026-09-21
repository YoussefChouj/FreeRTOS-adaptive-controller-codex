#ifndef PLATFORM_RTOS_OBSERVABILITY_H
#define PLATFORM_RTOS_OBSERVABILITY_H

#include <stdint.h>

/* Low-cost counters sampled once per Send_Task cycle. They are deliberately
 * plain globals so SWD/livewatch can read them without a new transport frame.
 *
 * Surface (S15):
 *   platform_obs_send_ticks       - Send_Task loop counter (uint32)
 *   platform_obs_queue_depth      - last reported USART3 TX ring depth (uint16)
 *   platform_obs_dma_busy         - last reported DMA busy flag (uint16)
 *   platform_obs_usart3_tx_drops  - mirror of UA3TxDrops, ring-full drops (uint32)
 *   platform_obs_cmd_queue_depth  - gs_cmd ring depth at last tick (uint16)
 *   platform_obs_cmd_queue_max    - compile-time gs_cmd ring capacity (uint16)
 *   platform_obs_heap_free_bytes  - xPortGetFreeHeapSize() snapshot (uint32)
 */
extern volatile uint32_t platform_obs_send_ticks;
extern volatile uint16_t platform_obs_queue_depth;
extern volatile uint16_t platform_obs_dma_busy;
extern volatile uint32_t platform_obs_usart3_tx_drops;
extern volatile uint16_t platform_obs_cmd_queue_depth;
extern volatile uint16_t platform_obs_cmd_queue_max;
extern volatile uint32_t platform_obs_heap_free_bytes;

void PlatformObservability_Tick(uint16_t queue_depth, uint16_t dma_busy);

#endif
