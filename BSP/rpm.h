#ifndef __RPM_H__
#define __RPM_H__

#include "stm32f4xx.h"

/**
 * @module  rpm.h
 * @subsystem  drivers
 * @depends  stm32f4xx.h, misc.h (NVIC), syscfg.h (SYSCFG_EXTILineConfig)
 * @owns  RPM measurement via EXTI rising-edge + DWT cycle counter (ADR-0010).
 *        Replaces the never-used TIM2 PWM init that previously owned PA5/PB3/PB10/PB11.
 * @caution  ISR context only — RPM_EdgeISR must NOT use floats or FreeRTOS API.
 *           Per-channel state is `volatile` so the ISR can write and the bench
 *           frame builder (called from Send_Groundstation_Telemetry_UART4, normal
 *           task context) can read.
 */

/* Number of sensor channels wired on the bench.  Channel index is the
 * physical pin on the 3-pin header type, NOT the motor number — pairing is
 * decided by which header carries the one bench sensor.
 *
 * 2026-07-21: pins re-targeted from PA5/PB3/PB10/PB11 (ESC headers, shared
 *             with motor PWM) to PA0/PA1/PC6/PC7 (UART4 TX/RX, UART6 TX/RX).
 *             UART4 and UART6 are physically unused on this custom FC board
 *             (no connector traces to anything), so their pins are free for
 *             digital input.  PA0/PA1 (UART4) and PC6/PC7 (UART6) are all
 *             5V-tolerant and EXTI-capable on STM32F407. */
#define RPM_NUM_CH            4U

/* Reflective marks per prop: 2 (one per blade underside, sensor under the disc). */
#define RPM_PULSES_PER_REV    2U

/* Channel-to-pin mapping.  Grouped per channel so one channel can be remapped
 * in a single place.  All four pins are 5V-tolerant FT inputs on F407. */
#define RPM_CH0_GPIO_PORT     GPIOA
#define RPM_CH0_GPIO_PIN      GPIO_Pin_0    /* UART4 TX (phys. unused on this FC) */
#define RPM_CH0_GPIO_PINSRC   GPIO_PinSource0
#define RPM_CH0_EXTI_PORT     EXTI_PortSourceGPIOA
#define RPM_CH0_EXTI_LINE     EXTI_Line0

#define RPM_CH1_GPIO_PORT     GPIOA
#define RPM_CH1_GPIO_PIN      GPIO_Pin_1    /* UART4 RX (phys. unused on this FC) */
#define RPM_CH1_GPIO_PINSRC   GPIO_PinSource1
#define RPM_CH1_EXTI_PORT     EXTI_PortSourceGPIOA
#define RPM_CH1_EXTI_LINE     EXTI_Line1

#define RPM_CH2_GPIO_PORT     GPIOC
#define RPM_CH2_GPIO_PIN      GPIO_Pin_6    /* UART6 TX (firmware empty, pin free) */
#define RPM_CH2_GPIO_PINSRC   GPIO_PinSource6
#define RPM_CH2_EXTI_PORT     EXTI_PortSourceGPIOC
#define RPM_CH2_EXTI_LINE     EXTI_Line6

#define RPM_CH3_GPIO_PORT     GPIOC
#define RPM_CH3_GPIO_PIN      GPIO_Pin_7    /* UART6 RX (firmware empty, pin free) */
#define RPM_CH3_GPIO_PINSRC   GPIO_PinSource7
#define RPM_CH3_EXTI_PORT     EXTI_PortSourceGPIOC
#define RPM_CH3_EXTI_LINE     EXTI_Line7

/* Staleness timeout: 0.5 s of no full-revolution edges => RPM = 0. */
#define RPM_TIMEOUT_CYCLES    (SystemCoreClock / 2U)

/* Glitch reject: discard revolution periods shorter than the period of 20000 RPM.
 * At 168 MHz: (168e6 / 20000) * 60 = 8400 * 60 = 504000 cycles.
 * NOTE: divide-first then multiply — SystemCoreClock * 60U overflows u32 above ~71.6 MHz. */
#define RPM_MIN_PERIOD_CYCLES ((SystemCoreClock / 20000U) * 60U)

/* Ring buffer depth for per-revolution periods (averaged for noise rejection). */
#define RPM_RING_DEPTH        4U

void     RPM_Init(void);
void     RPM_EdgeISR(uint8_t ch);   /* called from EXTI handlers; ch 0..RPM_NUM_CH-1 */
uint16_t RPM_Get(uint8_t ch);       /* averaged RPM, 0 if stopped or stale */

/* ---- Debug instrumentation: add these to the Keil Watch window ----
 * rpm_dbg_edges[ch]     : monotonic count of EVERY rising edge the ISR sees (never
 *                         reset). Wave a reflective mark past the sensor: if this
 *                         ticks up, edges ARE reaching the MCU and EXTI is live. If
 *                         it stays frozen, no edges arrive (electrical / power /
 *                         ground / EXTI config). Catching the us-wide pulse by
 *                         eyeballing GPIOx->IDR is hopeless; watch this instead.
 * rpm_dbg_period_cyc[ch]: DWT cycles of the last ACCEPTED revolution period
 *                         (after glitch/stale reject). RPM = 60*SystemCoreClock/this;
 *                         at 168 MHz, RPM = 10080000000 / cycles. 0 until first rev. */
extern volatile uint32_t rpm_dbg_edges[RPM_NUM_CH];
extern volatile uint32_t rpm_dbg_period_cyc[RPM_NUM_CH];

#endif