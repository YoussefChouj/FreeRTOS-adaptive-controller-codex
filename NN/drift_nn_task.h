#ifndef DRIFT_NN_TASK_H
#define DRIFT_NN_TASK_H
/* Drift NN task (PROPOSED). Reads the same 23 inputs the offline model trains on, runs it at 20 Hz and
 * publishes its horizontal body-velocity residual in g_drift_nn. Shadow by default: nothing in the flight
 * loop reads it while g_drift_nn_live == 0. Live (1 or 2) adds the clamped residual dvx/dvy to the mode-2
 * EKF position track (1) and also to the EKF velocity feedback (2); StabilizerTask.c gates it per tick
 * (Dnn_LiveGate) and writes live_on/live_why. */
#include <stdint.h>
#define DRIFT_NN_ST_IDLE      0U
#define DRIFT_NN_ST_RUN       1U
#define DRIFT_NN_ST_LOWHEAP   2U
#define DRIFT_NN_ST_NOCREATE  3U
#define DRIFT_NN_ST_BADSUM    4U   /* weight checksum mismatch, vx/vy = EKF velocity, dvx/dvy = 0 */

/* g_drift_nn_live values (VAR_WRITE id 56, default 0) */
#define DRIFT_NN_LIVE_OFF     0U   /* shadow: computed and logged, never applied */
#define DRIFT_NN_LIVE_POS     1U   /* residual added to the mode-2 position track only */
#define DRIFT_NN_LIVE_POSVEL  2U   /* also added to the mode-2 EKF velocity feedback */

/* live_why: why the residual is not applied this tick (0 = applied) */
#define DRIFT_NN_WHY_ON       0U
#define DRIFT_NN_WHY_OFF      1U   /* g_drift_nn_live == 0 */
#define DRIFT_NN_WHY_STATUS   2U   /* task not RUN (low heap, no create, bad checksum) */
#define DRIFT_NN_WHY_STALE    3U   /* no new step for DRIFT_NN_STALE_MS */
#define DRIFT_NN_WHY_WARMUP   4U   /* reset pending, or fewer than DRIFT_NN_WARM_STEPS since it (reset on each take-off) */
#define DRIFT_NN_WHY_EKF      5U   /* not bias mode 2, or the OF EKF is unhealthy */
#define DRIFT_NN_WHY_PHASE    6U   /* not FLIGHT_PHASE_FLYING */
#define DRIFT_NN_WHY_CAMERA   7U   /* camera fusion in the loop (g_ext_pos_on >= 2): camera stays truth-only */

#define DRIFT_NN_WARM_STEPS   125U /* 6.25 s at 20 Hz: the TCN receptive field is full */
#define DRIFT_NN_STALE_MS     200U /* 4 missed steps */

typedef struct {
    float    vx, vy;        /* raw NN body-velocity residual [m/s], same frame as s_ekf_of.x[1]/x[4]:
                             * corrected velocity = v_in + (vx, vy) */
    float    v_in[2];       /* EKF velocity fed to the step (block mean) */
    float    dvx, dvy;      /* residual clamped to +-g_drift_nn_clamp_mps, 0 unless the step returned 0 */
    uint32_t steps; uint32_t cyc_last; uint32_t cyc_max;
    uint32_t step_ms;       /* tick time of the last step [ms] */
    uint16_t alt_hold; uint8_t status;
    uint8_t  live_on;       /* 1 when the flight loop applied dvx/dvy this tick */
    uint8_t  live_why;      /* DRIFT_NN_WHY_* */
    volatile uint8_t reset_req;
} DriftNn_t;
extern volatile DriftNn_t g_drift_nn;
extern volatile uint8_t g_drift_nn_live;      /* DRIFT_NN_LIVE_*, VAR_WRITE id 56 */
extern volatile float   g_drift_nn_clamp_mps; /* |dvx|, |dvy| limit [m/s], VAR_WRITE id 57 */
void drift_nn_task_create(void);
#endif
