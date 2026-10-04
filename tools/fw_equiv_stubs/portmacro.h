/* Host shim for FreeRTOS/portable/RVDS/ARM_CM4F/portmacro.h (tools/fw_equiv.py only, never built for the target).
   The RVDS port uses armcc __forceinline/__asm; the types and macros here match it, the BASEPRI helpers are extern. */
#ifndef PORTMACRO_H
#define PORTMACRO_H

#include <stdint.h>

#define portCHAR        char
#define portFLOAT       float
#define portDOUBLE      double
#define portLONG        long
#define portSHORT       short
#define portSTACK_TYPE  uint32_t
#define portBASE_TYPE   long

typedef portSTACK_TYPE StackType_t;
typedef long BaseType_t;
typedef unsigned long UBaseType_t;
typedef uint32_t TickType_t;
#define portMAX_DELAY ( TickType_t ) 0xffffffffUL
#define portTICK_TYPE_IS_ATOMIC 1

#define portSTACK_GROWTH            ( -1 )
#define portTICK_PERIOD_MS          ( ( TickType_t ) 1000 / configTICK_RATE_HZ )
#define portBYTE_ALIGNMENT          8

extern void vPortYield( void );
extern void vPortEnterCritical( void );
extern void vPortExitCritical( void );
extern void vPortSetBASEPRI( uint32_t ulBASEPRI );
extern void vPortRaiseBASEPRI( void );
extern uint32_t ulPortRaiseBASEPRI( void );
extern BaseType_t xPortIsInsideInterrupt( void );

#define portYIELD()                             vPortYield()
#define portEND_SWITCHING_ISR( xSwitchRequired ) if( xSwitchRequired != pdFALSE ) portYIELD()
#define portYIELD_FROM_ISR( x ) portEND_SWITCHING_ISR( x )
#define portDISABLE_INTERRUPTS()                vPortRaiseBASEPRI()
#define portENABLE_INTERRUPTS()                 vPortSetBASEPRI( 0 )
#define portENTER_CRITICAL()                    vPortEnterCritical()
#define portEXIT_CRITICAL()                     vPortExitCritical()
#define portSET_INTERRUPT_MASK_FROM_ISR()       ulPortRaiseBASEPRI()
#define portCLEAR_INTERRUPT_MASK_FROM_ISR(x)    vPortSetBASEPRI(x)

#define portTASK_FUNCTION_PROTO( vFunction, pvParameters ) void vFunction( void *pvParameters )
#define portTASK_FUNCTION( vFunction, pvParameters ) void vFunction( void *pvParameters )
#define portNOP()
#define portINLINE __inline
#define portFORCE_INLINE inline

#endif /* PORTMACRO_H */
