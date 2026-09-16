/* Host-test stub. The harness defines xTaskGetTickCount so it can advance the
 * clock deterministically and assert the timestamps the firmware stamps. */
#ifndef __TASK_STUB_H__
#define __TASK_STUB_H__

#include "FreeRTOS.h"

TickType_t xTaskGetTickCount(void);

#endif
