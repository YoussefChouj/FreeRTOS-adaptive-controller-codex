#include "rtos_observability.h"

volatile uint32_t platform_obs_send_ticks = 0U;
volatile uint16_t platform_obs_queue_depth = 0U;
volatile uint16_t platform_obs_dma_busy = 0U;

void PlatformObservability_Tick(uint16_t queue_depth, uint16_t dma_busy)
{
    platform_obs_send_ticks++;
    platform_obs_queue_depth = queue_depth;
    platform_obs_dma_busy = dma_busy;
}
