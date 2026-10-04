#ifndef PREARM_H
#define PREARM_H

/* Pre-arm checks (WP-40). PreArm_Evaluate() is pure (host-tested by tests/firmware_host/test_prearm.c); the
 * target glue PreArm_Refresh() gathers the inputs (TASK/systemmonitor_task.c). The system monitor refreshes the
 * result at 1 Hz and FlightFSM_Event() refreshes it again on every ARM_REQUEST, from RC and from the ground
 * station alike. Every check always reports in g_prearm_fail_mask; only the checks enabled in
 * g_prearm_enable_mask (table in API/prearm.c, all 0 by default) block arming. Contract and the bit list:
 * docs/firmware-safety.md; the dashboard preflight report reads the three masks by name. */

#include <stdint.h>

typedef enum {                      /* bit index in the masks; the order is the dashboard's PREARM_BITS */
    PREARM_BIT_ESTIMATOR = 0,       /* attitude estimator converged */
    PREARM_BIT_VBAT      = 1,       /* battery at rest at or above vbat_min_v */
    PREARM_BIT_RC        = 2,       /* RC link (SBUS) live */
    PREARM_BIT_LEVEL     = 3,       /* |roll| and |pitch| under tilt_max_deg */
    PREARM_BIT_TRIP      = 4,       /* no wfb_safety trip latched */
    PREARM_BIT_STAB_FPS  = 5,       /* stabilizer task rate at or above stab_fps_min */
    PREARM_BIT_COUNT     = 6
} prearm_bit_t;

#define PREARM_FIRST_NONE 0xFFU     /* g_prearm_first_fail when every check passes */

typedef struct {
    uint8_t  estimator_ready;       /* IMU_EstimatorReady() */
    float    vbat_v;                /* real_voltage, V */
    uint8_t  rc_live;               /* !sbus_lost */
    float    roll_deg;              /* imu_data.rol */
    float    pitch_deg;             /* imu_data.pit */
    uint8_t  safety_trip;           /* g_wfb_status.safety_trip != 0 */
    uint16_t stab_fps;              /* system_monitor.stabilizerTask_fps, Hz */
} prearm_in_t;

typedef struct {
    float vbat_min_v;
    float tilt_max_deg;
    float stab_fps_min;
} prearm_limits_t;

extern volatile uint16_t g_prearm_fail_mask;    /* bit set = check failed (every check, enabled or not) */
extern volatile uint16_t g_prearm_block_mask;   /* fail & enable: the checks that refuse an ARM_REQUEST now */
extern volatile uint16_t g_prearm_enable_mask;  /* bit set = a failure of this check blocks arming */
extern volatile uint8_t  g_prearm_first_fail;   /* lowest failed bit index, PREARM_FIRST_NONE if none */

void     PreArm_DefaultLimits(prearm_limits_t *out);
uint16_t PreArm_Evaluate(const prearm_in_t *in); /* sets the globals above, returns the fail mask */
uint8_t  PreArm_Allows(void);                    /* 1 when no enabled check failed at the last evaluation */
void     PreArm_Refresh(void);                   /* target glue: gather the inputs, then PreArm_Evaluate */

#endif /* PREARM_H */
