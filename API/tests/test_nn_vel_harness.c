#include "nn_vel.h"
#include "test_nn_vel_golden.h"
#include <stdio.h>
#include <math.h>

#define ASSERTM(cond, msg, ...) do { \
    if (!(cond)) { \
        printf("FAIL: " msg "\n", ##__VA_ARGS__); \
        return 1; \
    } \
} while(0)

static int test_golden_vectors(void) {
    float max_err = 0.0f;
    uint32_t now_us = 1000000;
    int t;
    const nn_vel_state_t* st;

    nn_vel_reset();

    for (t = 0; t < GOLDEN_STEPS; t++) {
        float p_out[2];
        float vx = golden_v[t * 2 + 0];
        float vy = golden_v[t * 2 + 1];
        float expected_px = golden_out[t * 2 + 0];
        float expected_py = golden_out[t * 2 + 1];
        float err_x, err_y;

        /* Accumulate */
        if (golden_is_empty[t] < 0.5f) {
            float x[NN_VEL_D];
            int i;
            for (i = 0; i < NN_VEL_D; i++) {
                x[i] = golden_x[t * NN_VEL_D + i];
            }
            nn_vel_accum(x);
        }

        /* Step */
        nn_vel_step(vx, vy, p_out, NN_ACTIVE, 100.0f, false, now_us);

        st = nn_vel_get_state();
        err_x = fabsf(st->p[0] - expected_px);
        err_y = fabsf(st->p[1] - expected_py);

        if (err_x > max_err) max_err = err_x;
        if (err_y > max_err) max_err = err_y;

        now_us += 50000; /* 50ms = 20Hz */
    }

    printf("Golden max abs error: %e\n", max_err);
    ASSERTM(max_err < 1e-4f, "Golden vector mismatch > 1e-4 (err=%e)", max_err);

    st = nn_vel_get_state();
    ASSERTM(st->latch_reason == NN_LATCH_NAN_INF, "Expected NaN/Inf latch, got %d", st->latch_reason);
    ASSERTM(st->mode == NN_SHADOW, "Expected SHADOW mode after latch");

    return 0;
}

static int test_latch_late(void) {
    float x[NN_VEL_D] = {0};
    const nn_vel_state_t* st;

    nn_vel_reset();
    nn_vel_accum(x);
    nn_vel_step(0, 0, NULL, NN_ACTIVE, 100.0f, false, 0);

    nn_vel_accum(x);
    nn_vel_step(0, 0, NULL, NN_ACTIVE, 100.0f, false, 80000); /* 80ms > 75ms (late) */

    st = nn_vel_get_state();
    ASSERTM(st->latch_reason == NN_LATCH_STEP_LATE, "Expected STEP_LATE latch, got %d", st->latch_reason);

    return 0;
}

static int test_latch_fallback(void) {
    float x[NN_VEL_D] = {0};
    const nn_vel_state_t* st;

    nn_vel_reset();
    nn_vel_accum(x);
    nn_vel_step(0, 0, NULL, NN_ACTIVE, 100.0f, true, 0); /* pid_fallback = true */

    st = nn_vel_get_state();
    ASSERTM(st->latch_reason == NN_LATCH_PID_FALLBACK, "Expected PID_FALLBACK latch, got %d", st->latch_reason);

    return 0;
}

static int test_cap(void) {
    const nn_vel_state_t* st;
    float x[NN_VEL_D] = {100.0f, 100.0f, 100.0f, 100.0f, 100.0f, 100.0f, 100.0f, 100.0f, 100.0f, 100.0f, 100.0f, 100.0f, 100.0f, 100.0f, 100.0f, 100.0f, 100.0f};

    nn_vel_reset();

    nn_vel_accum(x);
    nn_vel_step(10.0f, 10.0f, NULL, NN_ACTIVE, 0.001f, false, 0);

    st = nn_vel_get_state();
    if (st->latch_reason == NN_LATCH_NONE) {
        float raw_mag = sqrtf(st->p[0]*st->p[0] + st->p[1]*st->p[1]);
        float mag = sqrtf(st->p_applied[0]*st->p_applied[0] + st->p_applied[1]*st->p_applied[1]);
        if (raw_mag > 0.001f) {
            ASSERTM(fabsf(mag - 0.001f) < 1e-4f, "Magnitude cap failed: got %f", mag);
        }
    }

    return 0;
}

int main(void) {
    if (test_golden_vectors() != 0) return 1;
    if (test_latch_late() != 0) return 1;
    if (test_latch_fallback() != 0) return 1;
    if (test_cap() != 0) return 1;

    printf("PASS\n");
    return 0;
}
