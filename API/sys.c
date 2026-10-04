/**
 * @module     sys.c
 * @subsystem  bsp
 * @owner      nothing: no caller in the firmware (prototypes in sys.h).
 * @purpose    Keil embedded-assembler helpers: WFI, global interrupt disable/enable, main stack pointer set.
 *             Thumb code has no inline assembler, so each instruction sequence is an __asm function.
 * @inputs     MSR_MSP: new main stack pointer in r0.
 * @outputs    PRIMASK (INTX_*), MSP (MSR_MSP).
 */

#include "sys.h"

/* ------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------ */

/* Wait for interrupt. */
__asm void WFI_SET(void)
{
    WFI;
}

/* Disable all interrupts (fault handlers and NMI still run). */
__asm void INTX_DISABLE(void)
{
    CPSID   I
    BX      LR
}

/* Enable all interrupts. */
__asm void INTX_ENABLE(void)
{
    CPSIE   I
    BX      LR
}

/* Set the main stack pointer to addr. */
__asm void MSR_MSP(u32 addr)
{
    MSR MSP, r0 			//set Main Stack value
    BX r14
}
