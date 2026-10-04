/**
 * @module     led.c
 * @subsystem  bsp
 * @owner      USER/main.c start-up (LED_Init, BEEP_Init); SystemErrorDetect (systemmonitor_task.c) drives the LED.
 * @purpose    GPIO set-up for the RGB status LED and the buzzer pin.
 * @inputs     none.
 * @outputs    PA11/PA12/PC8 push-pull outputs, LED left red; PB9 push-pull output, buzzer off.
 */

#include "led.h"

/* ------------------------------------------------------------------
 * Private constants
 * ------------------------------------------------------------------ */

/* Status LED, one pin per colour, lit when the pin is low (SystemErrorDetect shows red with PC8 low). */
#define LED_R_PORT   GPIOC
#define LED_R_PIN    GPIO_Pin_8
#define LED_G_PORT   GPIOA
#define LED_G_PIN    GPIO_Pin_12
#define LED_B_PORT   GPIOA
#define LED_B_PIN    GPIO_Pin_11
#define BEEP_PORT    GPIOB
#define BEEP_PIN     GPIO_Pin_9      /* also TIM4 channel 4 */

/* ------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------ */

void LED_Init(void)
{
	GPIO_InitTypeDef  GPIO_InitStructure;

	RCC_AHB1PeriphClockCmd(RCC_AHB1Periph_GPIOA, ENABLE);
	RCC_AHB1PeriphClockCmd(RCC_AHB1Periph_GPIOC, ENABLE);

	/* blue and green: push-pull outputs with pull-up */
	GPIO_InitStructure.GPIO_Pin = LED_B_PIN | LED_G_PIN;
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_OUT;
	GPIO_InitStructure.GPIO_OType = GPIO_OType_PP;
	GPIO_InitStructure.GPIO_Speed = GPIO_Speed_100MHz;
	GPIO_InitStructure.GPIO_PuPd = GPIO_PuPd_UP;
	GPIO_Init(LED_B_PORT, &GPIO_InitStructure);

	/* red */
	GPIO_InitStructure.GPIO_Pin = LED_R_PIN;
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_OUT;
	GPIO_InitStructure.GPIO_OType = GPIO_OType_PP;
	GPIO_InitStructure.GPIO_Speed = GPIO_Speed_100MHz;
	GPIO_InitStructure.GPIO_PuPd = GPIO_PuPd_UP;
	GPIO_Init(LED_R_PORT, &GPIO_InitStructure);

	/* start red */
	GPIO_SetBits(LED_B_PORT, LED_B_PIN);
	GPIO_SetBits(LED_G_PORT, LED_G_PIN);
	GPIO_ResetBits(LED_R_PORT, LED_R_PIN);
}

void BEEP_Init(void)
{
	GPIO_InitTypeDef  GPIO_InitStructure;

	RCC_AHB1PeriphClockCmd(RCC_AHB1Periph_GPIOB, ENABLE);

	GPIO_InitStructure.GPIO_Pin = BEEP_PIN;
	GPIO_InitStructure.GPIO_Mode = GPIO_Mode_OUT;
	GPIO_InitStructure.GPIO_OType = GPIO_OType_PP;
	GPIO_InitStructure.GPIO_Speed = GPIO_Speed_100MHz;
	GPIO_InitStructure.GPIO_PuPd = GPIO_PuPd_UP;
	GPIO_Init(BEEP_PORT, &GPIO_InitStructure);

	GPIO_ResetBits(BEEP_PORT, BEEP_PIN);   /* buzzer off */
}
