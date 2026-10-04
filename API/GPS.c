/**
 * @module     GPS.c
 * @subsystem  bsp
 * @owner      nothing: no caller in the firmware (GPS.h is empty and declares nothing).
 * @purpose    Placeholder for a GPS driver on USART6. No GPS is fitted; USART6's pins PC6/PC7 are RPM inputs
 *             (stm32f4xx_it.c). Kept so the Keil project's file list stays unchanged.
 * @inputs     none.
 * @outputs    none.
 */

#include "GPS.h"

/* ------------------------------------------------------------------
 * Private constants
 * ------------------------------------------------------------------ */

#define GPS_UART	USART6   /* port a GPS driver would use */

/* ------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------ */

void Drv_GpsPin_Init(void)
{

}
