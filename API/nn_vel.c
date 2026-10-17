#include "nn_vel.h"
#include "nn_vel_weights.h"
#include <math.h>
#include <string.h>

static nn_vel_state_t s_state;

static float s_accum[NN_VEL_D];
static int s_accum_count;
static bool s_accum_nan;

static float s_last_valid[NN_VEL_D];
static float s_h[NN_VEL_LAYERS][NN_VEL_H_DIM];

static inline float sigmoidf(float x) {
    return 1.0f / (1.0f + expf(-x));
}

void nn_vel_reset(void) {
    memset(&s_state, 0, sizeof(s_state));
    s_state.mode = NN_OFF;
    s_state.latch_reason = NN_LATCH_NONE;

    memset(s_accum, 0, sizeof(s_accum));
    s_accum_count = 0;
    s_accum_nan = false;
    memset(s_last_valid, 0, sizeof(s_last_valid));
    memset(s_h, 0, sizeof(s_h));
}

void nn_vel_accum(const float x[NN_VEL_D]) {
    int i;
    for (i = 0; i < NN_VEL_D; i++) {
        float val = x[i];
        if (isnan(val) || isinf(val)) {
            s_accum_nan = true;
            val = 0.0f; /* NaN -> 0 as in Python */
        }
        s_accum[i] += val;
    }
    s_accum_count++;
}

static void check_latch(bool pid_fallback, bool is_nan_inf, bool is_late, bool is_out_of_bounds) {
    if (s_state.latch_reason != NN_LATCH_NONE) {
        return; /* Already latched */
    }
    if (pid_fallback) {
        s_state.latch_reason = NN_LATCH_PID_FALLBACK;
    } else if (is_nan_inf) {
        s_state.latch_reason = NN_LATCH_NAN_INF;
    } else if (is_late) {
        s_state.latch_reason = NN_LATCH_STEP_LATE;
    } else if (is_out_of_bounds) {
        s_state.latch_reason = NN_LATCH_OUT_OF_BOUNDS;
    }

    if (s_state.latch_reason != NN_LATCH_NONE) {
        s_state.mode = NN_SHADOW;
    }
}

static bool is_invalid(float x) {
    return isnan(x) || isinf(x);
}

void nn_vel_step(float vx, float vy, float p_out[2], nn_vel_mode_t mode, float cap, bool pid_fallback, uint32_t now_us) {
    float dt_us;
    bool is_late = false;
    float x[NN_VEL_D];
    float x_n[NN_VEL_D];
    bool nan_inf = false;
    float inp[NN_VEL_H_DIM];
    float h_in[NN_VEL_H_DIM];
    int nout = 4;
    float o[4] = {0};
    float px, py;
    float p_mag;
    bool out_of_bounds;
    float px_app = 0.0f;
    float py_app = 0.0f;
    int i, j, l;

    if (s_state.latch_reason == NN_LATCH_NONE) {
        s_state.mode = mode;
    }

    dt_us = (float)(now_us - s_state.last_step_time_us);
    if (s_state.step_count > 0 && dt_us > 75000.0f) { /* 1.5 ticks at 20Hz (50ms) = 75ms */
        is_late = true;
    }

    if (s_accum_nan) {
        nan_inf = true;
    }

    if (s_accum_count > 0) {
        float inv = 1.0f / (float)s_accum_count;
        for (i = 0; i < NN_VEL_D; i++) {
            x[i] = s_accum[i] * inv;
            s_last_valid[i] = x[i];
            s_accum[i] = 0.0f;
        }
    } else {
        for (i = 0; i < NN_VEL_D; i++) {
            x[i] = s_last_valid[i];
        }
    }
    s_accum_count = 0;
    s_accum_nan = false;

    /* Normalise */
    for (i = 0; i < NN_VEL_D; i++) {
        x_n[i] = (x[i] - nn_vel_mu[i]) / nn_vel_sd[i];
        if (is_invalid(x_n[i])) nan_inf = true;
    }

    if (is_invalid(vx) || is_invalid(vy)) nan_inf = true;

    /* Input layer */
    for (j = 0; j < NN_VEL_H_DIM; j++) {
        float sum = nn_vel_inp_b[j];
        for (i = 0; i < NN_VEL_D; i++) {
            sum += nn_vel_inp_w[j * NN_VEL_D + i] * x_n[i];
        }
        inp[j] = sum;
    }

    /* Process GRU layers */
    for (j = 0; j < NN_VEL_H_DIM; j++) {
        h_in[j] = tanhf(inp[j]);
    }

    for (l = 0; l < NN_VEL_LAYERS; l++) {
        const float *w_ih = NULL, *w_hh = NULL, *b_ih = NULL, *b_hh = NULL;
        float h_out[NN_VEL_H_DIM];
        if (l == 0) {
            w_ih = nn_vel_gru_0_w_ih;
            w_hh = nn_vel_gru_0_w_hh;
            b_ih = nn_vel_gru_0_b_ih;
            b_hh = nn_vel_gru_0_b_hh;
        } else if (l == 1) {
#if NN_VEL_LAYERS > 1
            w_ih = nn_vel_gru_1_w_ih;
            w_hh = nn_vel_gru_1_w_hh;
            b_ih = nn_vel_gru_1_b_ih;
            b_hh = nn_vel_gru_1_b_hh;
#endif
        }

        for (j = 0; j < NN_VEL_H_DIM; j++) {
            float r_ih = b_ih[j];
            float z_ih = b_ih[NN_VEL_H_DIM + j];
            float n_ih = b_ih[2 * NN_VEL_H_DIM + j];

            float r_hh = b_hh[j];
            float z_hh = b_hh[NN_VEL_H_DIM + j];
            float n_hh = b_hh[2 * NN_VEL_H_DIM + j];

            for (i = 0; i < NN_VEL_H_DIM; i++) {
                float x_i = h_in[i];
                float h_i = s_h[l][i];

                r_ih += w_ih[j * NN_VEL_H_DIM + i] * x_i;
                z_ih += w_ih[(NN_VEL_H_DIM + j) * NN_VEL_H_DIM + i] * x_i;
                n_ih += w_ih[(2 * NN_VEL_H_DIM + j) * NN_VEL_H_DIM + i] * x_i;

                r_hh += w_hh[j * NN_VEL_H_DIM + i] * h_i;
                z_hh += w_hh[(NN_VEL_H_DIM + j) * NN_VEL_H_DIM + i] * h_i;
                n_hh += w_hh[(2 * NN_VEL_H_DIM + j) * NN_VEL_H_DIM + i] * h_i;
            }

            {
                float r = sigmoidf(r_ih + r_hh);
                float z = sigmoidf(z_ih + z_hh);
                float n = tanhf(n_ih + r * n_hh);

                h_out[j] = (1.0f - z) * n + z * s_h[l][j];
                if (is_invalid(h_out[j])) nan_inf = true;
            }
        }
        for (j = 0; j < NN_VEL_H_DIM; j++) {
            s_h[l][j] = h_out[j];
            h_in[j] = h_out[j];
        }
    }

    /* Output layer */
#ifdef NN_VEL_HEAD_ADD
    nout = 2;
#endif

    for (j = 0; j < nout; j++) {
        float sum = nn_vel_out_b[j];
        for (i = 0; i < NN_VEL_H_DIM; i++) {
            sum += nn_vel_out_w[j * NN_VEL_H_DIM + i] * h_in[i];
        }
        o[j] = sum;
    }

#ifdef NN_VEL_HEAD_SCALE
    px = 0.3f * o[0] * vx - 0.3f * o[1] * vy + 0.05f * o[2];
    py = 0.3f * o[0] * vy + 0.3f * o[1] * vx + 0.05f * o[3];
#else
    px = o[0];
    py = o[1];
#endif

    if (is_invalid(px) || is_invalid(py)) nan_inf = true;

    s_state.p[0] = px;
    s_state.p[1] = py;

    p_mag = sqrtf(px * px + py * py);
    out_of_bounds = p_mag > 10.0f;

    check_latch(pid_fallback, nan_inf, is_late, out_of_bounds);

    if (s_state.mode == NN_ACTIVE && s_state.latch_reason == NN_LATCH_NONE) {
        if (p_mag > cap && p_mag > 1e-6f) {
            float scale = cap / p_mag;
            px_app = px * scale;
            py_app = py * scale;
        } else {
            px_app = px;
            py_app = py;
        }
    }

    s_state.p_applied[0] = px_app;
    s_state.p_applied[1] = py_app;

    if (p_out) {
        p_out[0] = px_app;
        p_out[1] = py_app;
    }

    s_state.step_count++;
    s_state.last_step_time_us = now_us;
}

const nn_vel_state_t* nn_vel_get_state(void) {
    return &s_state;
}
