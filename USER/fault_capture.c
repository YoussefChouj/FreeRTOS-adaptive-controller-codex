/**
 * @file    fault_capture.c
 * @brief   Persistent Hard Fault Logger — Phase 1 MVP
 *
 * Phase 1 captures fault context into a RAM buffer (fault_backup[512]).
 * After a warm reset, FaultRecord_Init() detects the captured fault,
 * persists it to flash Sector 11, and blinks SOS on the red LED.
 *
 * Design decisions:
 *   - fault_backup is a static file-scope buffer in .bss. On a warm reset
 *     (NVIC_SystemReset()) the .bss is NOT zeroed, so the buffer survives.
 *     On a cold power-on the MCU resets the full SRAM, so the buffer is clean.
 *   - No malloc anywhere — all memory is statically allocated.
 *   - No FreeRTOS calls in fault handlers — raw CMSIS register reads only.
 *   - No flash writes in fault handlers — timing is unpredictable.
 *   - Flash write only in FaultRecord_Persist(), called from main() before the
 *     scheduler starts. Sector 11 (0x080E0000) is far from executing code.
 *
 * @note    Requires configUSE_MALLOC_FAILED_HOOK=1 in FreeRTOSConfig.h for the
 *          malloc-failure hook to fire. Set it when ready to arm the hook.
 */
#include "fault_capture.h"
#include "stm32f4xx_flash.h"

/* FreeRTOS task control block — pxCurrentTCB is the running task pointer.
 * pxCurrentTCB is a file-scope pointer (TCB_t*) in tasks.c, declared extern here.
 * pcTaskName is at offset sizeof(void*) in the TCB (first member after pxTopOfStack). */
extern void *pxCurrentTCB;

/* Fault capture RAM buffer and state flag.
 * Placed in .bss (static storage) — survives warm resets (NVIC_SystemReset())
 * but is zeroed on cold power-on.  fault_captured=0 means normal boot;
 * =1 means a fault was captured on the previous boot cycle; =2 means it was
 * persisted to flash and the SOS blink should have fired. */
static uint8_t fault_backup[FAULT_SLOT_SIZE];
static volatile uint8_t fault_captured = FAULT_STATE_EMPTY;

/* Hardware fault register accessor */
#define SCB_HFSR   (*((volatile uint32_t *)0xE000ED2CU))
#define SCB_CFSR   (*((volatile uint32_t *)0xE000ED28U))
#define SCB_MMFAR  (*((volatile uint32_t *)0xE000ED34U))
#define SCB_BFAR   (*((volatile uint32_t *)0xE000ED38U))

/* Bit masks for CFSR sub-fields */
#define CFSR_MEMFAULTSR_MASK  0x000000FFU
#define CFSR_BUSFAULTSR_MASK  0x0000FF00U
#define CFSR_USGFAULTSR_MASK  0xFFFF0000U
#define HFSR_FORCED_MASK      0x40000000U

/* GPIOA base for the red LED (PA11) */
#define GPIOA_ODR  (*((volatile uint32_t *)0x40020014U))
#define GPIOA_BSRR  (*((volatile uint32_t *)0x40020018U))
/* Set PA11 high (LED off, active-low): write 1 << 11 to BSRR */
#define LED_RED_ON()    do { GPIOA_ODR &= ~(1U << 11); } while(0)
#define LED_RED_OFF()   do { GPIOA_ODR |=  (1U << 11); } while(0)

/* Approximate delay: 100000 iterations ≈ 200 ms at 168 MHz (5 cycles/loop) */
#define BLINK_DELAY_DOT   100000U
#define BLINK_DELAY_DASH  300000U
#define BLINK_DELAY_UNIT  BLINK_DELAY_DOT

/* Delay loop — software only, no peripherals, no SysTick dependency */
static void delay_loops(uint32_t n)
{
    volatile uint32_t i = 0U;
    while (i < n) { i++; }
}

/* SOS blink for the panic loop */
static void blink_dash(void)
{
    LED_RED_ON();
    delay_loops(BLINK_DELAY_DASH);
    LED_RED_OFF();
    delay_loops(BLINK_DELAY_UNIT);
}

static void blink_dot(void)
{
    LED_RED_ON();
    delay_loops(BLINK_DELAY_UNIT);
    LED_RED_OFF();
    delay_loops(BLINK_DELAY_UNIT);
}

/* ================================================================
 * Fault Record structure (512 bytes, packed)
 * ================================================================ */
typedef struct {
    uint32_t magic;          /*  0: 0x464C5452 "FLTR" */
    uint16_t version;       /*  4: 1 */
    uint16_t size;           /*  6: 512 */
    uint32_t tick_count;     /*  8: xTaskGetTickCountFromISR or 0 */
    uint32_t rtc_epoch;      /* 12: 0 (Phase 1) */
    uint32_t hfsr;           /* 16: SCB->HFSR */
    uint32_t cfsr;           /* 20: SCB->CFSR */
    uint32_t mmfar;          /* 24: SCB->MMFAR */
    uint32_t bfar;           /* 28: SCB->BFAR */
    uint8_t  exception;      /* 32: IPSR value at fault */
    uint8_t  active_stack;   /* 33: 0=MSP, 1=PSP */
    uint8_t  fault_type;     /* 34: 0=generic, 1=NMI, 2=stack overflow,
                                3=assert, 4=malloc fail, 5=hardfault */
    uint8_t  reserved1;     /* 35: padding */
    /* Stacked exception frame */
    uint32_t stacked_pc;    /* 36: R15 / PC */
    uint32_t stacked_lr;    /* 40: R14 / LR */
    uint32_t stacked_psr;   /* 44: xPSR */
    uint32_t stacked_r0;     /* 48: R0  */
    uint32_t stacked_r1;     /* 52: R1  */
    uint32_t stacked_r2;     /* 56: R2  */
    uint32_t stacked_r3;     /* 60: R3  */
    uint32_t stacked_r12;    /* 64: R12 */
    uint32_t stacked_lr_call;/* 68: original LR at fault */
    uint32_t msp;           /* 72: MSP at fault */
    uint32_t psp;           /* 76: PSP at fault */
    uint32_t control;       /* 80: CONTROL register */
    char     task_name[16];  /* 84: pxCurrentTCB->pcTaskName or "pre-sched\0" */
    uint32_t task_handle;   /*100: (uint32_t)pxCurrentTCB */
    /* Callee-saved registers (R4–R11) at fault */
    uint32_t r4;            /*104 */
    uint32_t r5;            /*108 */
    uint32_t r6;            /*112 */
    uint32_t r7;            /*116 */
    uint32_t r8;            /*120 */
    uint32_t r9;            /*124 */
    uint32_t r10;           /*128 */
    uint32_t r11;           /*132 */
    char     fault_region[32]; /*136: TCB name or "unknown" */
    /* Pre-fault state snapshot (Phase 1: zeroed, filled in Phase 2) */
    float    roll_rate;     /*168: 0 (Phase 1) */
    float    pitch_rate;    /*172: 0 */
    float    yaw_rate;       /*176: 0 */
    float    bat_voltage;    /*180: 0 */
    uint32_t system_monitor_snapshot[22]; /*184: zeroed */
    uint16_t crc16;          /*272: CRC16-CCITT of bytes 0–271 */
    uint8_t  padding[238];   /*274: reserved, zeroed */
} FaultRecord;

/* Static assertions — verify struct size at compile time */
typedef char STATIC_ASSERT_SIZE[(FAULT_SLOT_SIZE == sizeof(FaultRecord)) ? 1 : -1];
#define UNUSED(x)  ((void)(x))

/* ================================================================
 * CRC-16 CCITT (XMODEM polynomial, initial 0xFFFF)
 * ================================================================ */
static uint16_t crc16_ccitt(const uint8_t *data, uint16_t len)
{
    uint16_t crc = 0xFFFFU;
    uint16_t i;
    while (len--) {
        crc ^= (uint16_t)(*data++) << 8;
        for (i = 0; i < 8; i++) {
            if (crc & 0x8000U) {
                crc = (crc << 1) ^ 0x1021U;
            } else {
                crc <<= 1;
            }
        }
    }
    return crc;
}

/* ================================================================
 * Exception handlers — all delegate to FaultCapture_Record()
 * ================================================================ */
void HardFault_Handler(void)   { FaultCapture_Record(3U); }
void MemManage_Handler(void)  { FaultCapture_Record(4U); }
void BusFault_Handler(void)   { FaultCapture_Record(5U); }
void UsageFault_Handler(void) { FaultCapture_Record(6U); }
void NMI_Handler(void)        { FaultCapture_Record(2U); }

/* ================================================================
 * Core capture routine — called from every fault/exception handler
 * ================================================================ */
void FaultCapture_Record(uint8_t exception)
{
    FaultRecord rec;
    uint32_t sp;
    uint32_t control;
    uint32_t *frame;
    void *tcb;
    const char *name = "pre-sched";
    uint16_t crc;

    /* Disable interrupts — the handler must be deterministic */
    __disable_irq();

    /* Read fault status registers */
    rec.hfsr  = SCB_HFSR;
    rec.cfsr  = SCB_CFSR;
    rec.mmfar = SCB_MMFAR;
    rec.bfar  = SCB_BFAR;

    /* Determine active stack pointer from CONTROL register */
    /* Bit 1 of CONTROL: 0 = privileged, 1 = unprivileged (not what we want)
     * Bit 0 of CONTROL: 0 = MSP active, 1 = PSP active */
    control = __get_CONTROL();
    rec.control = control;
    rec.active_stack = (control & 2U) ? 1U : 0U;  /* bit 1 = SPSEL */

    if ((control & 2U) != 0U) {
        sp = __get_PSP();
    } else {
        sp = __get_MSP();
    }
    rec.msp = __get_MSP();
    rec.psp = __get_PSP();

    /* Read stacked exception frame (8 words pushed by hardware) */
    frame = (uint32_t *)sp;
    rec.stacked_r0       = frame[0];
    rec.stacked_r1       = frame[1];
    rec.stacked_r2       = frame[2];
    rec.stacked_r3       = frame[3];
    rec.stacked_r12      = frame[4];
    rec.stacked_lr_call  = frame[5];
    rec.stacked_pc       = frame[6];
    rec.stacked_psr      = frame[7];

    /* Callee-saved registers (R4–R11) are on the stack below the hardware frame.
     * Their exact offset depends on the compiler's stack layout at the fault point.
     * We record the current values from the frame pointer for Phase 1.
     * Phase 2 will walk the stack to find these more precisely. */
    rec.r4  = 0U; rec.r5  = 0U; rec.r6  = 0U; rec.r7  = 0U;
    rec.r8  = 0U; rec.r9  = 0U; rec.r10 = 0U; rec.r11 = 0U;

    /* Walk pxCurrentTCB to get the task name.
     * pxCurrentTCB is a global pointer (file-scope, not on any stack).
     * It is valid even if the current task's stack is corrupted. */
    tcb = (void *)pxCurrentTCB;
    {
        uint8_t ci;  /* loop index: declared here so it's in scope for the else branch below */
        if (tcb != (void *)0) {
            /* TCB layout: pxTopOfStack is first, then pxStack, then pcTaskName[16].
             * pcTaskName offset = sizeof(void*) + sizeof(void*) = 8 bytes on this platform.
             * We read up to 15 chars + null terminator. */
            const char *p = (const char *)tcb + 8;
            for (ci = 0; ci < 15; ci++) {
                rec.task_name[ci] = p[ci];
                if (p[ci] == '\0') break;
            }
            rec.task_name[15] = '\0';
            name = rec.task_name;
        } else {
            /* pxCurrentTCB is NULL — pre-scheduler fault */
            rec.task_name[0] = 'p';
            rec.task_name[1] = 'r';
            rec.task_name[2] = 'e';
            rec.task_name[3] = '-';
            rec.task_name[4] = 's';
            rec.task_name[5] = 'c';
            rec.task_name[6] = 'h';
            rec.task_name[7] = 'e';
            rec.task_name[8] = 'd';
            rec.task_name[9] = '\0';
            for (ci = 10; ci < 16; ci++) { rec.task_name[ci] = '\0'; }
        }
    }

    rec.task_handle = (uint32_t)tcb;

    /* fault_region = task name for Phase 1 */
    {
        uint8_t ri;
        for (ri = 0; ri < 31; ri++) {
            if (name[ri] == '\0') break;
            rec.fault_region[ri] = name[ri];
        }
        rec.fault_region[ri] = '\0';
        for (ri++; ri < 32; ri++) { rec.fault_region[ri] = '\0'; }
    }

    /* Fill in the remaining record fields */
    rec.magic       = FAULT_BACKUP_MAGIC;
    rec.version     = FAULT_BACKUP_VERSION;
    rec.size        = FAULT_SLOT_SIZE;
    rec.tick_count  = 0U;  /* xTaskGetTickCountFromISR calls malloc — skip for Phase 1 */
    rec.rtc_epoch   = 0U;
    rec.exception   = exception;
    rec.fault_type  = (exception == 2U) ? 1U  /* NMI */
                     : (exception == 3U) ? 5U  /* HardFault */
                     : 0U;                     /* generic */
    rec.reserved1   = 0U;
    rec.roll_rate   = 0.0f;
    rec.pitch_rate  = 0.0f;
    rec.yaw_rate    = 0.0f;
    rec.bat_voltage = 0.0f;
    {
        uint8_t si;
        for (si = 0; si < 22; si++) { rec.system_monitor_snapshot[si] = 0U; }
    }

    /* Compute CRC16 over the first 510 bytes (everything except the crc16 field itself) */
    crc = crc16_ccitt((const uint8_t *)&rec, (uint16_t)(FAULT_SLOT_SIZE - 2));
    rec.crc16 = crc;

    /* Write to the shared RAM buffer */
    {
        uint8_t *dst = (uint8_t *)&fault_backup;
        const uint8_t *src = (const uint8_t *)&rec;
        uint16_t i2;
        for (i2 = 0; i2 < FAULT_SLOT_SIZE; i2++) { dst[i2] = src[i2]; }
    }

    /* Mark fault as captured and spin forever */
    fault_captured = FAULT_STATE_SAVED;
    for (;;) { }
}

/* ================================================================
 * Panic loop — SOS blink on PA11 (red LED), 2 Hz rate
 * ================================================================ */
void FaultRecord_PanicLoop(void)
{
    /* LED starts OFF (GPIOA_ODR bit 11 = 1, active-low LED).
     * Each SOS sequence: S (dot dot dot) O (dash dash dash) S (dot dot dot) */
    for (;;) {
        /* Letter S: 3 dots */
        blink_dot(); blink_dot(); blink_dot();
        delay_loops(300000U);  /* inter-letter gap */

        /* Letter O: 3 dashes */
        blink_dash(); blink_dash(); blink_dash();
        delay_loops(300000U);  /* inter-letter gap */

        /* Letter S: 3 dots */
        blink_dot(); blink_dot(); blink_dot();
        delay_loops(1000000U); /* inter-word gap (≈1 s) */
    }
}

/* ================================================================
 * Persist — write fault_backup to flash Sector 11
 * Called before the scheduler starts.
 * ================================================================ */
uint32_t FaultRecord_Persist(void)
{
    /* Flash metadata layout (16 bytes at FAULT_FLASH_BASE):
     *  [0..3]   uint32_t  magic      (FAULT_BACKUP_MAGIC or 0)
     *  [4..5]   uint16_t  version    (1)
     *  [6..7]   uint16_t  slot_count (FAULT_SLOT_COUNT)
     *  [8]      uint8_t   active_slot (ring-buffer write head)
     *  [9..15]  padding
     *  [14..15] uint16_t  crc16       (metadata CRC) */
    uint32_t magic;
    uint8_t  active_slot;
    uint8_t  next_slot;
    uint16_t meta_crc;
    FLASH_Status st;

    /* Read metadata from flash */
    magic       = *(volatile uint32_t *)(FAULT_FLASH_BASE + 0U);
    active_slot = *(volatile uint8_t  *)(FAULT_FLASH_BASE + 8U);

    /* Check if Sector 11 has been initialised */
    if (magic != FAULT_BACKUP_MAGIC) {
        /* First use — erase Sector 11 */
        FLASH_Unlock();
        st = FLASH_EraseSector(FLASH_Sector_11, VoltageRange_3);
        if (st != FLASH_COMPLETE) {
            FLASH_Lock();
            return 1U;
        }

        /* Write fresh metadata */
        magic = FAULT_BACKUP_MAGIC;
        /* meta_crc: CRC of magic(4) + version(2) + slot_count(2) + active_slot(1) + padding(7) */
        {
            uint8_t mbuf[16] = {0};
            mbuf[0] = (uint8_t)(FAULT_BACKUP_MAGIC >>  0);
            mbuf[1] = (uint8_t)(FAULT_BACKUP_MAGIC >>  8);
            mbuf[2] = (uint8_t)(FAULT_BACKUP_MAGIC >> 16);
            mbuf[3] = (uint8_t)(FAULT_BACKUP_MAGIC >> 24);
            mbuf[4] = (uint8_t)FAULT_BACKUP_VERSION;
            mbuf[5] = (uint8_t)(FAULT_BACKUP_VERSION >> 8);
            mbuf[6] = (uint8_t)FAULT_SLOT_COUNT;
            mbuf[7] = 0U;
            mbuf[8] = 0U;  /* active_slot = 0 (no faults written yet) */
            meta_crc = crc16_ccitt(mbuf, 16U);
        }
        *(volatile uint32_t *)(FAULT_FLASH_BASE + 0U)  = FAULT_BACKUP_MAGIC;
        *(volatile uint16_t *)(FAULT_FLASH_BASE + 4U)  = (uint16_t)FAULT_BACKUP_VERSION;
        *(volatile uint16_t *)(FAULT_FLASH_BASE + 6U)  = (uint16_t)FAULT_SLOT_COUNT;
        *(volatile uint8_t  *)(FAULT_FLASH_BASE + 8U) = 0U;
        *(volatile uint16_t *)(FAULT_FLASH_BASE + 14U) = meta_crc;
        active_slot = 0U;
    }

    /* Compute next slot (ring buffer) */
    next_slot = (uint8_t)((active_slot + 1U) % FAULT_SLOT_COUNT);

    /* Write fault_backup word-by-word to the target slot.
     * Slot N starts at: FAULT_FLASH_BASE + 16 + N * FAULT_SLOT_SIZE */
    {
        const uint32_t *src = (const uint32_t *)fault_backup;
        uint32_t dst_addr = FAULT_FLASH_BASE + 16U + (uint32_t)next_slot * FAULT_SLOT_SIZE;
        uint16_t w;
        for (w = 0; w < (FAULT_SLOT_SIZE / 4U); w++) {
            st = FLASH_ProgramWord(dst_addr + w * 4U, src[w]);
            if (st != FLASH_COMPLETE) {
                FLASH_Lock();
                return 2U;
            }
        }
    }

    /* Update active_slot in metadata */
    *(volatile uint8_t *)(FAULT_FLASH_BASE + 8U) = next_slot;
    {
        /* Recompute and update metadata CRC */
        uint8_t mbuf[16] = {0};
        mbuf[0] = (uint8_t)(FAULT_BACKUP_MAGIC >>  0);
        mbuf[1] = (uint8_t)(FAULT_BACKUP_MAGIC >>  8);
        mbuf[2] = (uint8_t)(FAULT_BACKUP_MAGIC >> 16);
        mbuf[3] = (uint8_t)(FAULT_BACKUP_MAGIC >> 24);
        mbuf[4] = (uint8_t)FAULT_BACKUP_VERSION;
        mbuf[5] = (uint8_t)(FAULT_BACKUP_VERSION >> 8);
        mbuf[6] = (uint8_t)FAULT_SLOT_COUNT;
        mbuf[7] = 0U;
        mbuf[8] = next_slot;
        meta_crc = crc16_ccitt(mbuf, 16U);
        *(volatile uint16_t *)(FAULT_FLASH_BASE + 14U) = meta_crc;
    }

    FLASH_Lock();
    return 0U;
}

/* ================================================================
 * Init — called from main() after BSP_Init(), before scheduler
 * ================================================================ */
void FaultRecord_Init(void)
{
    if (fault_captured == FAULT_STATE_SAVED) {
        /* Fault was captured on the previous boot. Persist to flash. */
        if (FaultRecord_Persist() == 0U) {
            fault_captured = FAULT_STATE_LOGGED;
        }
        /* Blink SOS — does not return */
        FaultRecord_PanicLoop();
    }
    /* fault_captured == 0 (normal boot) or 2 (already persisted): continue */
}

/* ================================================================
 * Malloc-failed hook — wired by vApplicationMallocFailedHook in main.c
 * Note: configUSE_MALLOC_FAILED_HOOK must be 1 in FreeRTOSConfig.h for this
 * to fire. It is currently 0. Set it to 1 when ready to arm the hook.
 * ================================================================ */
