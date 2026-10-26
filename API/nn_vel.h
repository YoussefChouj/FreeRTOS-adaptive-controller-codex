#ifndef NN_VEL_H
#define NN_VEL_H

#include <stdint.h>
#include <stdbool.h>

#define NN_VEL_D 17
#define NN_VEL_H_DIM 32

typedef enum {
    NN_OFF = 0,
    NN_SHADOW = 1,
    NN_ACTIVE = 2
} nn_vel_mode_t;

typedef enum {
    NN_LATCH_NONE = 0,
    NN_LATCH_PID_FALLBACK = 1,
    NN_LATCH_NAN_INF = 2,
    NN_LATCH_STEP_LATE = 3,
    NN_LATCH_OUT_OF_BOUNDS = 4
} nn_vel_latch_reason_t;

typedef struct {
    float p[2];
    float p_applied[2];
    nn_vel_mode_t mode;
    nn_vel_latch_reason_t latch_reason;
    uint32_t step_count;
    uint32_t last_step_time_us;
} nn_vel_state_t;

void nn_vel_reset(void);
void nn_vel_accum(const float x[NN_VEL_D]);
void nn_vel_step(float vx, float vy, float p_out[2], nn_vel_mode_t mode, float cap, bool pid_fallback, uint32_t now_us);
const nn_vel_state_t* nn_vel_get_state(void);

#endif // NN_VEL_H
