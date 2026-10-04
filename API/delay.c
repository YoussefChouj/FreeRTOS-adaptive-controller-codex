/**
 * @module     delay.c
 * @subsystem  bsp
 * @owner      bmi088_driver.c uses delay_ms() during sensor bring-up; the vector table calls SysTick_Handler.
 *             delay_init() has no caller today (the FreeRTOS port configures SysTick when the scheduler starts).
 * @purpose    SysTick set-up for the FreeRTOS tick, the SysTick interrupt that forwards to the kernel, and two
 *             blocking busy-wait delays for code that runs before the scheduler (or must not yield).
 * @inputs     SYSCLK in MHz (delay_init), configTICK_RATE_HZ from FreeRTOSConfig.h.
 * @outputs    SysTick clocked from HCLK, reloading every 1/configTICK_RATE_HZ s with its interrupt enabled.
 */

#include "delay.h"
#include "sys.h"
#include "FreeRTOS.h"
#include "task.h"

/* ------------------------------------------------------------------
 * Private constants
 * ------------------------------------------------------------------ */

/* Busy-wait loop counts. Not calibrated against a timer: the real delay depends on the compiler and the flash
   wait states, so treat delay_ms / delay_us as "at least roughly" and never use them for timing. */
#define DELAY_SPIN_PER_MS  42000
#define DELAY_SPIN_PER_US  40

/* ------------------------------------------------------------------
 * External symbols
 * ------------------------------------------------------------------ */

extern void xPortSysTickHandler(void);   /* FreeRTOS port.c */

/* ------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------ */

/* SysTick interrupt: hand the tick to FreeRTOS once the scheduler is running. */
void SysTick_Handler(void)
{
	if (xTaskGetSchedulerState() != taskSCHEDULER_NOT_STARTED)
	{
		xPortSysTickHandler();
	}
}

/* Clock SysTick from HCLK (not HCLK/8) so FreeRTOS gets an exact tick, and fire it at configTICK_RATE_HZ.
   SYSCLK is in MHz. LOAD is 24 bits wide: at 168 MHz the longest period is about 0.0998 s. */
void delay_init(u8 SYSCLK)
{
	u32 reload;
	SysTick_CLKSourceConfig(SysTick_CLKSource_HCLK);
	reload = SYSCLK;                              /* counts per microsecond */
	reload *= 1000000 / configTICK_RATE_HZ;       /* counts per tick        */
	SysTick->CTRL |= SysTick_CTRL_TICKINT_Msk;    /* enable the interrupt   */
	SysTick->LOAD = reload;
	SysTick->CTRL |= SysTick_CTRL_ENABLE_Msk;     /* start the counter      */
}

/* Blocking delay of roughly t milliseconds (busy-wait, does not yield). */
void delay_ms(u32 t)
{
	int i;
	for( i=0;i<t;i++)
	{
		int a=DELAY_SPIN_PER_MS;
		while(a--);
	}
}

/* Blocking delay of roughly t microseconds (busy-wait, does not yield). */
void delay_us(u32 t)
{
	int i;
	for( i=0;i<t;i++)
	{
		int a=DELAY_SPIN_PER_US;
		while(a--);
	}
}
