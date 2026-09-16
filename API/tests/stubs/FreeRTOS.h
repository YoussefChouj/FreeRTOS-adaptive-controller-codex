/* Host-test stub. subscribe.c includes FreeRTOS.h only to reach task.h for
 * xTaskGetTickCount; nothing else in the RTOS is referenced. */
#ifndef __FREERTOS_STUB_H__
#define __FREERTOS_STUB_H__

#include <stdint.h>

typedef uint32_t TickType_t;   /* configUSE_16_BIT_TICKS is 0 on the target */

#endif
