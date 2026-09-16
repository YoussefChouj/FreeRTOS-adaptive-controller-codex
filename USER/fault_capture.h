/**
 * @file    fault_capture.h
 * @brief   Persistent Hard Fault Logger — Phase 1 MVP
 *
 * Phase 1 captures fault context into a RAM buffer (fault_backup).
 * After a warm reset, FaultRecord_Init() detects the captured fault,
 * persists it to flash Sector 11 (0x080E0000), and blinks SOS on the red LED.
 *
 * Design constraints:
 *   - No malloc in any fault handler (heap may be corrupted)
 *   - No FreeRTOS API calls in fault handlers (scheduler may be inconsistent)
 *   - No flash writes in fault handlers (timing unpredictable)
 *   - Flash write only in FaultRecord_Persist(), called before the scheduler starts
 *
 * Flash layout (Sector 11, 128 KB):
 *   0x080E0000: Metadata  (16 B): magic, version, slot_count, active_slot, CRC16
 *   0x080E0010: Slot 0   (512 B): fault record
 *   0x080E0210: Slot 1   (512 B): fault record
 *   0x080E0410: Slot 2   (512 B): fault record
 *   0x080E0610: Slot 3   (512 B): fault record
 *
 * Hard constraints:
 *   - Do NOT fly with OF position-hold engaged (sign-inverted velocity feedback)
 *   - EKF is shadow mode; do not wire EKF output into control paths
 *
 * @note    Requires configUSE_MALLOC_FAILED_HOOK=1 in FreeRTOSConfig.h to arm
 *          the malloc-failure hook. Current value is 0; change it when ready.
 */
#ifndef __FAULT_CAPTURE_H__
#define __FAULT_CAPTURE_H__

#include <stdint.h>

/* Flash layout constants */
#define FAULT_BACKUP_MAGIC   0x464C5452U  /* "FLTR" */
#define FAULT_BACKUP_VERSION 1U
#define FAULT_SLOT_COUNT     4U
#define FAULT_SLOT_SIZE      512U
#define FAULT_FLASH_BASE     0x080E0000U  /* Sector 11 base */
#define FAULT_FLASH_SIZE     0x00020000U  /* Sector 11 = 128 KB */

/* fault_captured states */
#define FAULT_STATE_EMPTY   0U  /* normal boot */
#define FAULT_STATE_SAVED  1U  /* fault captured, persist to flash then panic */
#define FAULT_STATE_LOGGED  2U  /* fault already persisted, just panic */

/* Access to the RAM buffer (file-scope static in fault_capture.c) */
extern uint8_t fault_backup[FAULT_SLOT_SIZE];
extern volatile uint8_t fault_captured;

/* Called from main() after BSP_Init(), before vTaskStartScheduler().
 * Checks fault_captured:
 *   0 = normal boot, continue
 *   1 = fault was captured, persist to flash then blink SOS
 *   2 = fault was already persisted, blink SOS */
void FaultRecord_Init(void);

/* Writes fault_backup to the next slot in flash Sector 11.
 * Ring-buffer: next_slot = (active_slot + 1) % FAULT_SLOT_COUNT.
 * On first call (no valid magic in Sector 11), erases Sector 11 first.
 * Returns 0 on success, non-zero on failure. */
uint32_t FaultRecord_Persist(void);

/* Blinks the red LED (PA11) in SOS Morse pattern indefinitely.
 * Called after FaultRecord_Persist completes.
 * 2 Hz blink rate — CMSIS-DAP probe can halt the core at any time.
 * Does not return. */
void FaultRecord_PanicLoop(void);

/* Internal — called from fault/exception handlers.
 * exception: IPSR value (2=NMI, 3=HardFault, 4=MemManage, 5=BusFault, 6=UsageFault) */
void FaultCapture_Record(uint8_t exception);

#endif /* __FAULT_CAPTURE_H__ */
