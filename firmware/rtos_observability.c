#include "rtos_observability.h"
#include "FreeRTOS.h"   /* xPortGetFreeHeapSize() */

/* Live references to existing telemetry counters. These symbols are owned by
 * BSP/usart4.c (gs_cmd_*) and BSP/usart3.c (UA3Tx*) and already exported via
 * gs_command.h / usart3.h. Declaring them extern here lets the SWD reader
 * observe them through the same platform_obs_* surface without touching those
 * source files. */
extern volatile uint32_t UA3TxDrops;
extern volatile uint8_t  gs_cmd_head;
extern volatile uint8_t  gs_cmd_tail;

#define GS_CMD_QUEUE_LEN 16U

/* Original three counters exposed over SWD/livewatch (see S7 report). */
volatile uint32_t platform_obs_send_ticks = 0U;
volatile uint16_t platform_obs_queue_depth = 0U;
volatile uint16_t platform_obs_dma_busy = 0U;

/* New S15 counters: mirrored/snapshot values for the resource-panel and
 * bandwidth-panel. Each one is a one-way read from an existing source so
 * the host can resolve the symbol with no extra transport. */
volatile uint32_t platform_obs_usart3_tx_drops   = 0U; /* mirror of UA3TxDrops */
volatile uint16_t platform_obs_cmd_queue_depth   = 0U; /* gs_cmd queue depth   */
volatile uint16_t platform_obs_cmd_queue_max     = GS_CMD_QUEUE_LEN;
volatile uint32_t platform_obs_heap_free_bytes   = 0U; /* xPortGetFreeHeapSize() snapshot */

void PlatformObservability_Tick(uint16_t queue_depth, uint16_t dma_busy)
{
    platform_obs_send_ticks++;
    platform_obs_queue_depth = queue_depth;
    platform_obs_dma_busy = dma_busy;

    /* Mirror the three new sources. All reads are non-blocking (volatile
     * loads); xPortGetFreeHeapSize() is O(1) on heap_4. */
    platform_obs_usart3_tx_drops = UA3TxDrops;
    /* gs_cmd_head and gs_cmd_tail are uint8_t volatile, ring capacity is 16.
     * Compute depth as (head - tail) mod 16; both operands are 8-bit so the
     * subtraction wraps naturally. */
    {
        uint16_t head16 = (uint16_t)gs_cmd_head;
        uint16_t tail16 = (uint16_t)gs_cmd_tail;
        platform_obs_cmd_queue_depth = (head16 >= tail16)
                                       ? (uint16_t)(head16 - tail16)
                                       : (uint16_t)(GS_CMD_QUEUE_LEN - tail16 + head16);
    }
    platform_obs_heap_free_bytes = (uint32_t)xPortGetFreeHeapSize();
}
