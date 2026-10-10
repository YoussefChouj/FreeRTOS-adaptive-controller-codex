#ifndef STUBS_ROBOT_TYPES_H
#define STUBS_ROBOT_TYPES_H

#include <stdint.h>
#include "imu_update.h"

typedef struct {
    float FB;
    float Des;
    float U;
} StubPID_t;

typedef struct {
    StubPID_t gyroxPID;
    StubPID_t gyroyPID;
    StubPID_t gyrozPID;
    StubPID_t Z_ratePID;
    StubPID_t locxsPID;   /* x/y velocity loops, cm/s (6-axis MRAC 2026-10-10) */
    StubPID_t locysPID;
} CtrlerTypeDef;

/* Host stubs for CMSIS intrinsics when MRAC_ENABLE_SIGMA_PRIOR is enabled */
static inline uint32_t __get_PRIMASK(void) { return 0; }
static inline void __disable_irq(void) {}
static inline void __set_PRIMASK(uint32_t pri) { (void)pri; }

#endif /* STUBS_ROBOT_TYPES_H */
