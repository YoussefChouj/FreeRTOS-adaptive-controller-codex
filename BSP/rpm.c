#include "rpm.h"

/**
 * @module  rpm.c
 * @subsystem  drivers
 * @depends  rpm.h, stm32f4xx.h, stm32f4xx_syscfg.h (SYSCFG_EXTILineConfig),
 *           stm32f4xx_gpio.h, misc.h (NVIC), core_cm4.h (DWT / CoreDebug)
 * @owns  GPIO + SYSCFG + EXTI + NVIC init for PA0/PA1/PC6/PC7 (re-targeted
 *        2026-07-21 from PA5/PB3/PB10/PB11 — the old pins shared the ESC
 *        signal-return header and could not be used as inputs); per-channel
 *        period ring buffer + averaged RPM readout; DWT cycle counter enable.
 * @caution  ARMCC V5.06 is not C99-friendly in this project's config — keep
 *           locals at the top of every block.
 */

/* Per-channel state.  All written by the EXTI ISR, all read by RPM_Get (task
 * context).  `volatile` is sufficient: u32 reads on Cortex-M4 are atomic, so
 * a torn ring-average across an ISR can mix at most one adjacent revolution —
 * acceptable, called out in the ISR comment. */
typedef struct {
    volatile uint32_t edge_count;          /* counts every rising edge; wraps each RPM_PULSES_PER_REV */
    volatile uint32_t last_rev_stamp;      /* DWT->CYCCNT at last full-revolution edge */
    volatile uint32_t last_any_edge_stamp; /* DWT->CYCCNT at last rising edge (any) */
    volatile uint32_t ring[RPM_RING_DEPTH];
    volatile uint8_t  ring_idx;
    volatile uint8_t  ring_filled;         /* 0 until RPM_RING_DEPTH full */
} RPM_ChannelState_t;

static RPM_ChannelState_t rpm_ch[RPM_NUM_CH];

/* Debug counters (see rpm.h). Non-static so the Keil debugger resolves them by name.
 * rpm_dbg_edges is monotonic — a live "are edges arriving?" indicator you can watch
 * in the Watch window while waving a mark past the sensor. */
volatile uint32_t rpm_dbg_edges[RPM_NUM_CH]     = {0};
volatile uint32_t rpm_dbg_period_cyc[RPM_NUM_CH] = {0};

/* ----------------------------- init ----------------------------- */

static void RPM_GpioInit(void)
{
    GPIO_InitTypeDef gpio;
    EXTI_InitTypeDef  exti;
    uint8_t i;

    /* Per the existing pattern in BSP/pwm.c: GPIO_StructInit then init. */
    GPIO_StructInit(&gpio);

    RCC_AHB1PeriphClockCmd(RCC_AHB1Periph_GPIOA | RCC_AHB1Periph_GPIOC, ENABLE);
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_SYSCFG, ENABLE);

    /* Module output is active-HIGH on reflection, idles LOW; pull-down keeps
     * UNPLUGGED channels silent (no false edges from floating input). */
    gpio.GPIO_Mode  = GPIO_Mode_IN;
    gpio.GPIO_PuPd  = GPIO_PuPd_DOWN;
    gpio.GPIO_Speed = GPIO_Speed_100MHz;
    gpio.GPIO_OType = GPIO_OType_PP;

    gpio.GPIO_Pin = RPM_CH0_GPIO_PIN;
    GPIO_Init(RPM_CH0_GPIO_PORT, &gpio);
    SYSCFG_EXTILineConfig(RPM_CH0_EXTI_PORT, RPM_CH0_GPIO_PINSRC);

    gpio.GPIO_Pin = RPM_CH1_GPIO_PIN;
    GPIO_Init(RPM_CH1_GPIO_PORT, &gpio);
    SYSCFG_EXTILineConfig(RPM_CH1_EXTI_PORT, RPM_CH1_GPIO_PINSRC);

    gpio.GPIO_Pin = RPM_CH2_GPIO_PIN;
    GPIO_Init(RPM_CH2_GPIO_PORT, &gpio);
    SYSCFG_EXTILineConfig(RPM_CH2_EXTI_PORT, RPM_CH2_GPIO_PINSRC);

    gpio.GPIO_Pin = RPM_CH3_GPIO_PIN;
    GPIO_Init(RPM_CH3_GPIO_PORT, &gpio);
    SYSCFG_EXTILineConfig(RPM_CH3_EXTI_PORT, RPM_CH3_GPIO_PINSRC);

    /* All four EXTI lines: rising edge, interrupt mode. */
    exti.EXTI_Mode    = EXTI_Mode_Interrupt;
    exti.EXTI_Trigger = EXTI_Trigger_Rising;
    exti.EXTI_LineCmd = ENABLE;

    exti.EXTI_Line = RPM_CH0_EXTI_LINE; EXTI_Init(&exti);
    exti.EXTI_Line = RPM_CH1_EXTI_LINE; EXTI_Init(&exti);
    exti.EXTI_Line = RPM_CH2_EXTI_LINE; EXTI_Init(&exti);
    exti.EXTI_Line = RPM_CH3_EXTI_LINE; EXTI_Init(&exti);

    /* Zero per-channel state. */
    for (i = 0; i < RPM_NUM_CH; i++) {
        rpm_ch[i].edge_count         = 0U;
        rpm_ch[i].last_rev_stamp     = 0U;
        rpm_ch[i].last_any_edge_stamp = 0U;
        rpm_ch[i].ring_idx           = 0U;
        rpm_ch[i].ring_filled        = 0U;
    }
}

static void RPM_NvicInit(void)
{
    NVIC_InitTypeDef nvic;

    /* NVIC_PriorityGroup_4 is already set in BSP_Init() — 4 bits preemption, 0 sub.
     * Preemption priority 6 keeps these below the UART/USART interrupts used by
     * comms (priority 0-2 range) so a noisy sensor cannot starve comms. */
    nvic.NVIC_IRQChannelPreemptionPriority = 6;
    nvic.NVIC_IRQChannelSubPriority        = 0;
    nvic.NVIC_IRQChannelCmd                = ENABLE;

    /* EXTI0 (PA0), EXTI1 (PA1) get their own IRQ vectors; EXTI9_5 covers
     * PC6 (line 6) and PC7 (line 7).  EXTI3 / EXTI15_10 are no longer used. */
    nvic.NVIC_IRQChannel = EXTI0_IRQn;      NVIC_Init(&nvic);
    nvic.NVIC_IRQChannel = EXTI1_IRQn;      NVIC_Init(&nvic);
    nvic.NVIC_IRQChannel = EXTI9_5_IRQn;    NVIC_Init(&nvic);
}

static void RPM_DwtInit(void)
{
    /* ARMv7-M DWT cycle counter — required by ADR-0010 for ~6 ns period
     * resolution.  TRCENA enables the trace block; CTRL.CYCCNTENA starts CYCCNT. */
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0U;
    DWT->CTRL  |= DWT_CTRL_CYCCNTENA_Msk;
}

void RPM_Init(void)
{
    RPM_GpioInit();
    RPM_NvicInit();
    RPM_DwtInit();
}

/* ----------------------------- ISR ----------------------------- */

void RPM_EdgeISR(uint8_t ch)
{
    /* ISR CONTRACT: no floats, no FreeRTOS API, no division.
     * Only writes to volatile per-channel state; never reads task-owned data. */
    RPM_ChannelState_t* s;
    uint32_t now;
    uint32_t prev;
    uint32_t period;

    if (ch >= RPM_NUM_CH) {
        return;
    }

    s = &rpm_ch[ch];
    now = DWT->CYCCNT;

    /* Stash every-edge timestamp for the staleness watchdog in RPM_Get. */
    s->last_any_edge_stamp = now;

    /* Debug: monotonic every-edge counter (watch in Keil to confirm edges arrive). */
    rpm_dbg_edges[ch]++;

    /* Period is measured every RPM_PULSES_PER_REV-th edge so a full revolution
     * is captured regardless of mark-to-mark asymmetry. */
    s->edge_count++;
    if (s->edge_count < RPM_PULSES_PER_REV) {
        return;
    }
    s->edge_count = 0U;

    prev = s->last_rev_stamp;
    /* Unsigned wrap-around arithmetic is exact: now - prev is the elapsed
     * cycle count even if CYCCNT has wrapped since the previous sample. */
    period = now - prev;

    /* Stale-gap reject: the first full-revolution period after boot or after
     * any >0.5 s stall is measured against a stale last_rev_stamp, so one
     * huge gap-spanning sample would enter the ring and skew the average for
     * the next RPM_RING_DEPTH revolutions.  Discard it and reset the ring so
     * the next valid edge starts a fresh average. */
    if (period > RPM_TIMEOUT_CYCLES) {
        s->last_rev_stamp = now;
        s->ring_filled    = 0U;
        s->ring_idx       = 0U;
        return;
    }

    /* Glitch reject: a period shorter than 20000 RPM cannot come from a real
     * propeller (which would self-destruct first); it's almost certainly a
     * bounce or a noise spike.  Drop it without touching the ring. */
    if (period < RPM_MIN_PERIOD_CYCLES) {
        return;
    }

    /* Debug: last accepted revolution period (cycles) — RPM = 60*f_cpu/this. */
    rpm_dbg_period_cyc[ch] = period;

    s->ring[s->ring_idx] = period;
    s->ring_idx = (uint8_t)((s->ring_idx + 1U) % RPM_RING_DEPTH);
    if (s->ring_filled < RPM_RING_DEPTH) {
        s->ring_filled++;
    }
    s->last_rev_stamp = now;
}

/* ----------------------------- query ----------------------------- */

uint16_t RPM_Get(uint8_t ch)
{
    /* Task-context read.  May cross an ISR mid-update — torn ring samples are
     * accepted because each ring slot is a u32 (atomic on M4), and a torn
     * *average* can mix at most one adjacent-revolution sample (low impact at
     * 100 Hz frame rate vs the kHz-scale ring turnover). */
    RPM_ChannelState_t* s;
    uint32_t sum;
    uint32_t avg_period;
    uint32_t now;
    uint32_t last_any;
    uint8_t  filled;
    uint8_t  i;
    uint32_t  rpm32;

    if (ch >= RPM_NUM_CH) {
        return 0U;
    }
    s = &rpm_ch[ch];

    /* Snapshot under ISR-free assumption.  A pre-empted ISR write cannot split
     * a u32, so the snapshot is consistent for our purposes. */
    filled  = s->ring_filled;
    last_any = s->last_any_edge_stamp;
    now     = DWT->CYCCNT;

    /* Staleness: no full-revolution stamp update OR no any-edge activity for
     * > 0.5 s => motor is stopped or sensor unplugged. */
    if ((now - last_any) > RPM_TIMEOUT_CYCLES) {
        return 0U;
    }

    /* Guard div-by-0 before the ring fills.  Also catches the case where every
     * period was rejected by the glitch filter (period == 0 impossible, but
     * defensive). */
    if (filled == 0U) {
        return 0U;
    }

    sum = 0U;
    for (i = 0; i < filled; i++) {
        sum += s->ring[i];
    }
    /* filled is in [1..RPM_RING_DEPTH], sum > 0 because glitch-reject guarantees
     * every stored period >= RPM_MIN_PERIOD_CYCLES. */
    avg_period = sum / (uint32_t)filled;

    /* RPM = 60 * f_cpu / period (revolutions per minute from per-revolution
     * period).  Multiply first to preserve resolution.  The multiply MUST be
     * done in u64: 60U * SystemCoreClock overflows u32 above ~71.6 MHz and
     * wraps unconditionally on a 168 MHz target, which would make every
     * reported RPM ~6.8x too low. */
    rpm32 = (uint32_t)((60ULL * (uint64_t)SystemCoreClock) / avg_period);
    if (rpm32 > 65535U) {
        rpm32 = 65535U;
    }
    return (uint16_t)rpm32;
}