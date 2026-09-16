#ifndef __CALIB_H__
#define __CALIB_H__

#include "global_declare.h"

/* ADR-0011 Phase 3 (CAL_AIRBORNE_HOVER_TRIM) + Phase 4 (CAL_HOT_HOVER).
 * Pure C, no malloc, no globals. The state machine mirrors the v3 OF-bias
 * estimator in TASK/StabilizerTask.c (lines 46-143) so the style and gates
 * are familiar to anyone reading the firmware. */

typedef enum {
    CAL_TRIM_STATE_WAIT_TAKEOFF = 0,   /* not yet flying + altitude > 0.3 m + sticks centered */
    CAL_TRIM_STATE_RUNNING      = 1,   /* accumulating samples */
    CAL_TRIM_STATE_SETTLED      = 2,   /* residual < 5 mg for 1 s; b_a frozen at best */
    CAL_TRIM_STATE_DEGRADED     = 3    /* window expired; best-so-far held, AIRBORNE_DEGRADED */
} CalTrimState_t;

typedef struct {
    CalTrimState_t state;
    float   b_a[3];        /* estimated accel bias (mg), exported for telemetry */
    float   settle_resid;  /* current residual |g_ref - g_meas|, mg */
    uint16_t settled_ticks;
    uint16_t run_ticks;
    uint16_t max_ticks;    /* 5-10 s @ 200 Hz = 1000-2000 */
} CalTrim_t;

typedef enum {
    CAL_HOT_STATE_WAIT_STILL = 0,
    CAL_HOT_STATE_ACCUM      = 1,
    CAL_HOT_STATE_COMMIT     = 2   /* transient; auto-resets to WAIT_STILL next tick */
} CalHotState_t;

typedef struct {
    CalHotState_t state;
    float   b_g[3];        /* estimated gyro bias (rad/s), exported for telemetry */
    uint16_t still_tick;
    uint16_t acc_tick;
    uint8_t  rejected;     /* HOT_REJECTED telemetry bit; latched until cleared */
    uint8_t  cleared;      /* sticky-clear: once set, rejected must be read then cleared */
} CalHot_t;

/* Phase 3: closed-form accel-bias LS estimator.
 * Call once per control tick from the stabilizer task.
 * gravity_world = (0, 0, +1000) mg (world-Z up in 1 G hover).
 * g_meas_body   = (ax, ay, az) mg (gravity-removed body frame; i.e. raw accel - 1000*g_dir).
 * flight_phase_flying gates the whole FSM (Phase 3 is in-flight only by design). */
extern void CalTrim_Step(CalTrim_t *c,
                         float gravity_world_x, float gravity_world_y, float gravity_world_z,
                         float g_meas_body_x,   float g_meas_body_y,   float g_meas_body_z,
                         uint8_t flight_phase_flying);

/* Phase 4: gyro hot-bias FSM mirroring the v3 OF estimator.
 * Call once per control tick.
 * gyro           : (gx, gy, gz) rad/s, gyro-bias-removed raw.
 * lin_acc_xy_mg  : (|ax|, |ay|) mg, gravity-removed body frame, used for translational guard.
 * flight_phase_flying : must be FLYING.
 * rc_quiescent   : 1 if no RC stick is active on THR/PITCH/ROLL/YAW. */
extern void CalHot_Step(CalHot_t *c,
                        float gx, float gy, float gz,
                        float lin_acc_x_mg, float lin_acc_y_mg,
                        uint8_t flight_phase_flying,
                        uint8_t rc_quiescent);

/* Const-init helper: defaults from sim/calibrator.py. */
extern void CalTrim_Init(CalTrim_t *c, uint16_t max_ticks);
extern void CalHot_Init (CalHot_t *c);

#endif /* __CALIB_H__ */
