#ifndef PLATFORM_RTOS_OBSERVABILITY_H
#define PLATFORM_RTOS_OBSERVABILITY_H

#include <stdint.h>

/* Low-cost counters sampled once per Send_Task cycle. They are deliberately
 * plain globals so SWD/livewatch can read them without a new transport frame. */
extern volatile uint32_t platform_obs_send_ticks;
extern volatile uint16_t platform_obs_queue_depth;
extern volatile uint16_t platform_obs_dma_busy;

void PlatformObservability_Tick(uint16_t queue_depth, uint16_t dma_busy);

#endif
