#ifndef DRIFT_NN_TASK_H
#define DRIFT_NN_TASK_H

/* Shadow-mode drift NN (PROPOSED, not flown). Reads the same 23 inputs the offline model trains on,
 * runs it at 20 Hz and publishes its horizontal velocity estimate in g_drift_nn. Nothing in the
 * flight loop reads it: it is for logging and comparison only. */

#include <stdint.h>

#define DRIFT_NN_ST_IDLE      0U   /* task not created yet                                  */
#define DRIFT_NN_ST_RUN       1U   /* running                                               */
#define DRIFT_NN_ST_LOWHEAP   2U   /* removed itself: free heap below DRIFT_NN_HEAP_MARGIN   */
#define DRIFT_NN_ST_NOCREATE  3U   /* xTaskCreate failed                                    */
#define DRIFT_NN_ST_BADSUM    4U   /* weight checksum mismatch, output = EKF velocity        */

typedef struct {
    float    vx, vy;        /* NN velocity estimate [m/s], same frame as s_ekf_of.x[1]/x[4] */
    float    v_in[2];       /* EKF velocity fed to the step (block mean)                     */
    uint32_t steps;         /* model steps since boot or last reset                          */
    uint32_t cyc_last;      /* DWT cycles of the last step                                   */
    uint32_t cyc_max;
    uint16_t alt_hold;      /* steps where of_alt_cm was invalid and the last value was held */
    uint8_t  status;        /* DRIFT_NN_ST_*                                                 */
    volatile uint8_t reset_req;   /* write 1 to clear the model history (e.g. before takeoff) */
} DriftNn_t;

extern volatile DriftNn_t g_drift_nn;

void drift_nn_task_create(void);

#endif
