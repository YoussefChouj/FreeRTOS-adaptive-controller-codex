/**
 * @file     ekf_of.c
 * @brief    8-state body-frame OF position + velocity-bias + accel-bias Kalman Filter.
 *
 * State:
 *   x[0..7] = [px, vx, bof_x, py, vy, bof_y, ba_x, ba_y]
 *
 * Axis index table:
 *   X axis indices: 0, 1, 2, 6
 *   Y axis indices: 3, 4, 5, 7
 *
 * Predict (discrete, dt seconds):
 *   u = a - ba
 *   p += v*dt + 0.5*u*dt^2
 *   v += u*dt
 *   bof, ba: random walks
 *
 * Measurements:
 *   OF:   h = [0, 1, 1, 0], gated at of_gate sigmas (innov^2 > gate^2*S -> skip, rej_x/rej_y++)
 *   OF1:  h = [0, 1, 0, 0], raw flow (no gyro fix, no bias state), gated at of1_gate sigmas
 *   ZUPT: h = [0, 1, 0, 0]
 *
 * Units: meters, seconds
 * Pure C99, no malloc. Compatible with Keil ARMCC.
 *
 * Owner: Stabilizer_Task, 200 Hz: TASK/StabilizerTask.c Of_TickKf runs Predict, ZUPT, Update and the
 *        health gate each tick (mode 2 or shadow); ResetPos / ResetBias on origin reset, ARM and handheld edges.
 * Input: OF velocity minus s_of_bias (m/s), gravity-tilt accel (m/s^2). Output: x[] (s_ekf_of in the task),
 *        innov_x/y (health gate), rej_x/y (gated samples).
 */

#include "ekf_of.h"
#include <math.h>

/* axis index table: state index of each local index (EKF_OF_L_*) on the X and Y axis */
static const uint8_t k_axis_idx[2][4] = {{0,1,2,6},{3,4,5,7}};
enum { EKF_OF_L_POS = 0, EKF_OF_L_VEL = 1, EKF_OF_L_BOF = 2, EKF_OF_L_BA = 3 };   /* local index per axis */

#define EKF_OF_S_MIN   1e-8f   /* floor on the innovation variance S (divides) */

/* ------------------------------------------------------------------ */
/* Init                                                               */
/* ------------------------------------------------------------------ */

#define EKF_OF_VEL_P0  0.1f   /* velocity P0, (m/s)^2; also the gate-lockout release value */
#define EKF_OF_STATE(i, q_field, q, P0)  e->q_field = (q); e->P[0][(i)*5] = (P0); e->P[1][(i)*5] = (P0)

void EkfOf_Init(EkfOf_t *e)
{
    int i, j;
    for (i = 0; i < 8; i++) e->x[i] = 0.0f;

    for (i = 0; i < 2; i++) {
        for (j = 0; j < 16; j++) {
            e->P[i][j] = 0.0f;
        }
    }

    /* q = random-walk/white-noise density per second, units per state; P0 = initial variance */
    EKF_OF_STATE(0, q_pos, 1e-6f, 1.0f);   /* p    position */
    EKF_OF_STATE(1, q_acc, 1e-3f, EKF_OF_VEL_P0);   /* v    velocity */
    EKF_OF_STATE(2, q_bof, 1e-5f, 0.01f);  /* bof  optical flow bias: learned vs of1, see change history */
    EKF_OF_STATE(3, q_ba,  1e-6f, 0.25f);  /* ba   accel bias */

    /* Measurement noise variances */
    e->R_of   = 1e-4f;
    e->R_of1  = 1e-3f;
    e->R_zupt = 1e-4f;
    e->of_gate = 5.0f;   /* sigmas; armed flights auto_landing_1/hover_7/ekf1 peak at 4.5 sigma */
    e->of1_gate = 10.0f; /* raw flow carries the uncompensated rotation flow in fast moves; see 2026-10-11 */

    /* Change history (newest first)
     * 2026-10-11 R3 camera truth: of1_gate 5 -> 10. At 5 sigma the raw of1 anchor is dropped one-sided in
     *   fast RC moves (no release), so each move leaves a position step. Replay vs R3 camera (computed,
     *   ekf_of_replay replay_truth, x tilt sign fixed): rms 43.6 -> 32.4 cm, same-place revisit error
     *   p90 55.5 -> 38.5 cm; hover drift unchanged 0.44 cm/s. Gates 8..40 and off all within 2 cm.
     * 2026-10-07 two-channel EKF: of1 (raw flow) as a 2nd velocity measurement (EkfOf_UpdateRaw,
     *   h = [0,1,0,0], R_of1 1e-3, gate 5) and q_bof 0 -> 1e-5, so bof learns the gyro-fixed of2
     *   bias against of1. Replay vs video truth (ekf_of_replay truth, yaw-fixed): step B rms
     *   69.9 -> 20.8 cm, end (-123,-48) -> (+2,+2) cm; step A rms 31.2 -> 22.0 cm.
     * 2026-10-03 x_y_calibration_hitting_wall_1: gate-lockout recovery (EKF_OF_REJ_RELEASE in ekf_of.h);
     *   5 consecutive rejected OF samples reset P_vv to EKF_OF_VEL_P0 and apply the sample.
     * 2026-10-03 flight_test_drift_fix_1: q_bof 0 and bof variance 0 at ARM (EKF_OF_BOF_ARM_VAR),
     *   so bof stays 0 in flight. Its only remaining learning was the first 0.1 s of OF at
     *   lift-off, where the real sideways slide (-4..-6 cm/s) was taken as bias (bof_y -1.6 cm/s
     *   in F4), then flown as zero for the whole hover: the later-flight +x drift.
     * 2026-10-02 WP-21: OF innovation gate 5 sigma (S_ss 1.56e-4 at 50 Hz OF, so ~6.2 cm/s)
     * 2026-10-02 WP-14: tilt-only input; replay CHOSEN q_acc 1e-3, q_bof 1e-6, q_ba 1e-6, R_of 1e-4, R_zupt 1e-4
     * 2026-10-01 WP-8: 8-state model, q_vel->q_acc, ZUPT; values provisional
     */

    e->innov_x = 0.0f;
    e->innov_y = 0.0f;
    e->rej_x   = 0U;
    e->rej_y   = 0U;
    e->rej_run_x = 0U;
    e->rej_run_y = 0U;
    e->inited  = 1U;
}

/* ------------------------------------------------------------------ */
/* Predict                                                            */
/* ------------------------------------------------------------------ */

void EkfOf_Predict(EkfOf_t *e, float dt, float ax, float ay)
{
    if (!e->inited) return;
    
    float a_meas[2] = {ax, ay};
    int axis;

    for (axis = 0; axis < 2; axis++) {
        uint8_t i_p = k_axis_idx[axis][EKF_OF_L_POS];
        uint8_t i_v = k_axis_idx[axis][EKF_OF_L_VEL];
        
        uint8_t i_ba = k_axis_idx[axis][EKF_OF_L_BA];

        float u = a_meas[axis] - e->x[i_ba];
        e->x[i_p] += e->x[i_v] * dt + 0.5f * u * dt * dt;
        e->x[i_v] += u * dt;

        float dt2 = dt * dt;

        /* F = 
         * [1, dt, 0, -0.5*dt^2]
         * [0, 1,  0, -dt]
         * [0, 0,  1, 0]
         * [0, 0,  0, 1]
         */
        float F[4][4] = {
            {1.0f, dt, 0.0f, -0.5f * dt2},
            {0.0f, 1.0f, 0.0f, -dt},
            {0.0f, 0.0f, 1.0f, 0.0f},
            {0.0f, 0.0f, 0.0f, 1.0f}
        };
        float FP[4][4];
        float FPFt[4][4];
        int i, j, k;

        for (i = 0; i < 4; i++) {
            for (j = 0; j < 4; j++) {
                FP[i][j] = 0.0f;
                for (k = 0; k < 4; k++) {
                    FP[i][j] += F[i][k] * e->P[axis][k*4 + j];
                }
            }
        }
        for (i = 0; i < 4; i++) {
            for (j = 0; j < 4; j++) {
                FPFt[i][j] = 0.0f;
                for (k = 0; k < 4; k++) {
                    FPFt[i][j] += FP[i][k] * F[j][k]; /* F' means transpose F[j][k] */
                }
            }
        }

        e->P[axis][0]  = FPFt[0][0] + e->q_pos * dt;
        e->P[axis][1]  = FPFt[0][1];
        e->P[axis][2]  = FPFt[0][2];
        e->P[axis][3]  = FPFt[0][3];
        e->P[axis][4]  = FPFt[1][0];
        e->P[axis][5]  = FPFt[1][1] + e->q_acc * dt;
        e->P[axis][6]  = FPFt[1][2];
        e->P[axis][7]  = FPFt[1][3];
        e->P[axis][8]  = FPFt[2][0];
        e->P[axis][9]  = FPFt[2][1];
        e->P[axis][10] = FPFt[2][2] + e->q_bof * dt;
        e->P[axis][11] = FPFt[2][3];
        e->P[axis][12] = FPFt[3][0];
        e->P[axis][13] = FPFt[3][1];
        e->P[axis][14] = FPFt[3][2];
        e->P[axis][15] = FPFt[3][3] + e->q_ba * dt;
    }
}

/* ------------------------------------------------------------------ */
/* Update — scalar sequential                                         */
/* ------------------------------------------------------------------ */

/* gate: sigmas, 0 = off. Returns 1 when the sample was rejected (state and P untouched). */
static uint8_t ekf_of_update_one(EkfOf_t *e, int axis, const float h[4], float z, float R, float gate, float *innov_out)
{
    float PHt[4];
    float S = R;
    float K[4];
    float y;
    int i, j;

    uint8_t idx[4];
    idx[0] = k_axis_idx[axis][0];
    idx[1] = k_axis_idx[axis][1];
    idx[2] = k_axis_idx[axis][2];
    idx[3] = k_axis_idx[axis][3];

    for (i = 0; i < 4; i++) {
        PHt[i] = 0.0f;
        for (j = 0; j < 4; j++) {
            PHt[i] += e->P[axis][i*4 + j] * h[j];
        }
    }

    for (i = 0; i < 4; i++) {
        S += h[i] * PHt[i];
    }
    if (S < EKF_OF_S_MIN) S = EKF_OF_S_MIN;

    float hx = 0.0f;
    for (i = 0; i < 4; i++) {
        hx += h[i] * e->x[idx[i]];
    }
    
    y = z - hx;
    if (innov_out) {
        *innov_out = y;   /* raw innovation, logged even when gated so the health monitor still sees it */
    }
    if (gate > 0.0f && y * y > gate * gate * S) return 1U;

    for (i = 0; i < 4; i++) K[i] = PHt[i] / S;

    for (i = 0; i < 4; i++) e->x[idx[i]] += K[i] * y;

    /* P = P - K*PHt' - PHt*K' + K*S*K' */
    for (i = 0; i < 4; i++) {
        for (j = i; j < 4; j++) {
            e->P[axis][i*4+j] -= K[i]*PHt[j] + K[j]*PHt[i] - K[i]*K[j]*S;
            e->P[axis][j*4+i] = e->P[axis][i*4+j];  /* enforce symmetry */
        }
    }
    return 0U;
}

/* Reset one axis's velocity variance to its P0 with no cross-covariance (gate-lockout recovery). */
static void ekf_of_release_vel(EkfOf_t *e, int axis)
{
    int k;
    for (k = 0; k < 4; k++) {              /* vel: clear its row and column */
        e->P[axis][EKF_OF_L_VEL*4 + k] = 0.0f;
        e->P[axis][k*4 + EKF_OF_L_VEL] = 0.0f;
    }
    e->P[axis][EKF_OF_L_VEL*4 + EKF_OF_L_VEL] = EKF_OF_VEL_P0;
}

/* One axis of the OF update with gate-lockout recovery (see EKF_OF_REJ_RELEASE). */
static void ekf_of_update_axis(EkfOf_t *e, int axis, float z, float *innov, uint32_t *rej, uint8_t *run)
{
    const float h[4] = {0.0f, 1.0f, 1.0f, 0.0f};
    if (!ekf_of_update_one(e, axis, h, z, e->R_of, e->of_gate, innov)) {
        *run = 0U;
        return;
    }
    (*rej)++;
    if (++(*run) < EKF_OF_REJ_RELEASE) return;
    *run = 0U;
    ekf_of_release_vel(e, axis);
    (void)ekf_of_update_one(e, axis, h, z, e->R_of, 0.0f, 0);
}

void EkfOf_Update(EkfOf_t *e, float of_x, float of_y)
{
    if (!e->inited) return;
    ekf_of_update_axis(e, 0, of_x, &e->innov_x, &e->rej_x, &e->rej_run_x);
    ekf_of_update_axis(e, 1, of_y, &e->innov_y, &e->rej_y, &e->rej_run_y);
}

void EkfOf_UpdateRaw(EkfOf_t *e, float of1_x, float of1_y)
{
    const float h[4] = {0.0f, 1.0f, 0.0f, 0.0f};
    if (!e->inited || e->R_of1 <= 0.0f) return;
    (void)ekf_of_update_one(e, 0, h, of1_x, e->R_of1, e->of1_gate, 0);
    (void)ekf_of_update_one(e, 1, h, of1_y, e->R_of1, e->of1_gate, 0);
}

void EkfOf_UpdateZeroVel(EkfOf_t *e)
{
    if (!e->inited) return;
    const float h[4] = {0.0f, 1.0f, 0.0f, 0.0f};
    (void)ekf_of_update_one(e, 0, h, 0.0f, e->R_zupt, 0.0f, 0);
    (void)ekf_of_update_one(e, 1, h, 0.0f, e->R_zupt, 0.0f, 0);
}

/* ------------------------------------------------------------------ */
/* Reset OF bias to zero with variance var (on ARM / handheld edge)   */
/* ------------------------------------------------------------------ */

void EkfOf_ResetBias(EkfOf_t *e, float var)
{
    int axis, k;
    if (!e->inited) return;
    for (axis = 0; axis < 2; axis++) {
        e->x[k_axis_idx[axis][EKF_OF_L_BOF]] = 0.0f;
        for (k = 0; k < 4; k++) {          /* bof: clear its row and column */
            e->P[axis][EKF_OF_L_BOF*4 + k] = 0.0f;
            e->P[axis][k*4 + EKF_OF_L_BOF] = 0.0f;
        }
        e->P[axis][EKF_OF_L_BOF*4 + EKF_OF_L_BOF] = var;
    }
}

/* ------------------------------------------------------------------ */
/* Reset position (on Reset_World_Origin)                            */
/* ------------------------------------------------------------------ */

void EkfOf_ResetPos(EkfOf_t *e)
{
    if (!e->inited) return;
    e->x[k_axis_idx[0][EKF_OF_L_POS]] = 0.0f;  /* pos_x */
    e->x[k_axis_idx[1][EKF_OF_L_POS]] = 0.0f;  /* pos_y */
}

/* ---- External position fix (camera), shadow mode (see ekf_of.h) ---- */
void ext_pos_ring_reset(ext_pos_ring_t *r)
{
    r->head  = 0U;
    r->count = 0U;
}

/* One sample every EXT_POS_RING_DT_MS (the 200 Hz caller hits every 10th tick). */
void ext_pos_ring_tick(ext_pos_ring_t *r, uint32_t now_ms, float ex, float ey)
{
    if (r->count > 0U && (int32_t)(now_ms - r->t[r->head]) < (int32_t)EXT_POS_RING_DT_MS) {
        return;
    }
    r->head = (uint8_t)((r->head + 1U) % EXT_POS_RING_N);
    r->x[r->head] = ex;
    r->y[r->head] = ey;
    r->t[r->head] = now_ms;
    if (r->count < EXT_POS_RING_N) {
        r->count++;
    }
}

/* Earth position at t_ms: linear between the two samples around it, the newest sample when t_ms is after it.
 * Returns 0 when the buffer is empty or t_ms is older than the oldest sample. */
uint8_t ext_pos_ring_at(const ext_pos_ring_t *r, uint32_t t_ms, float *ex, float *ey)
{
    uint8_t k, a, b;
    if (r->count == 0U) {
        return 0U;
    }
    b = r->head;
    if ((int32_t)(t_ms - r->t[b]) >= 0) {
        *ex = r->x[b];
        *ey = r->y[b];
        return 1U;
    }
    for (k = 1U; k < r->count; k++) {
        a = (uint8_t)((r->head + EXT_POS_RING_N - k) % EXT_POS_RING_N);
        if ((int32_t)(t_ms - r->t[a]) >= 0) {
            float w = (float)(int32_t)(t_ms - r->t[a]) / (float)(int32_t)(r->t[b] - r->t[a]);
            *ex = r->x[a] + w * (r->x[b] - r->x[a]);
            *ey = r->y[a] + w * (r->y[b] - r->y[a]);
            return 1U;
        }
        b = a;
    }
    return 0U;
}

/* Innovation of one fix (z, cm, EKF earth frame) taken at t_meas_ms against the raw path plus the running
 * correction c. Gated fixes still record e_x/e_y. */
uint8_t ext_pos_fix(ext_pos_stat_t *s, const ext_pos_ring_t *r, uint32_t t_meas_ms, uint32_t now_ms, float zx, float zy)
{
    float px, py;
    int32_t age = (int32_t)(now_ms - t_meas_ms);
    s->age_ms = (uint16_t)((age < 0) ? 0 : ((age > 65535) ? 65535 : age));
    if (!ext_pos_ring_at(r, t_meas_ms, &px, &py)) {
        s->n_old++;
        return EXT_POS_FIX_OLD;
    }
    s->e_x = zx - (px + s->c_x);
    s->e_y = zy - (py + s->c_y);
    if (s->e_x * s->e_x + s->e_y * s->e_y > EXT_POS_GATE_CM * EXT_POS_GATE_CM) {
        s->n_rej++;
        if (s->rej_run < 255U) {
            s->rej_run++;
        }
        return EXT_POS_FIX_GATED;
    }
    s->n_ok++;
    s->rej_run = 0U;
    s->last_ok_ms = now_ms;
    s->stale = 0U;
    return EXT_POS_FIX_OK;
}

void ext_pos_stale_tick(ext_pos_stat_t *s, uint32_t now_ms)
{
    if (s->n_ok == 0U || (int32_t)(now_ms - s->last_ok_ms) > (int32_t)EXT_POS_STALE_MS) {
        s->stale = 1U;
    }
}

/* Closed-loop step for one ext_pos_fix() result: an accepted fix, or a gated one once EXT_POS_REJ_N came in a
 * row (slewed re-anchor), moves c by EXT_POS_GAIN * e clamped to EXT_POS_SLEW_CM. Returns 1 and the step
 * (dx, dy, cm) of the target c; earth_x/y follows through ext_pos_spread(). 0 = no step (old, or gated inside the run). */
uint8_t ext_pos_step(ext_pos_stat_t *s, uint8_t res, float *dx, float *dy)
{
    float d;
    *dx = 0.0f;
    *dy = 0.0f;
    if (res == EXT_POS_FIX_OLD || (res == EXT_POS_FIX_GATED && s->rej_run < EXT_POS_REJ_N)) {
        return 0U;
    }
    if (res == EXT_POS_FIX_GATED && s->n_anchor < 65535U) {
        s->n_anchor++;
    }
    *dx = EXT_POS_GAIN * s->e_x;
    *dy = EXT_POS_GAIN * s->e_y;
    d = sqrtf(*dx * *dx + *dy * *dy);
    if (d > EXT_POS_SLEW_CM) {
        *dx *= EXT_POS_SLEW_CM / d;
        *dy *= EXT_POS_SLEW_CM / d;
    }
    s->c_x += *dx;
    s->c_y += *dy;
    return 1U;
}

/* Mode 2, every control tick: move the applied part a toward the target c by at most EXT_POS_SLEW_CM /
 * EXT_POS_SPREAD_N (a full slew step over EXT_POS_SPREAD_N ticks), so a fix reaches earth_x/y as a ramp, not as a
 * one-tick step into the locx/locyPID D term. Returns the step (dx, dy, cm) for the caller to add to earth_x/y. */
void ext_pos_spread(ext_pos_stat_t *s, float *dx, float *dy)
{
    const float m = EXT_POS_SLEW_CM / (float)EXT_POS_SPREAD_N;
    float ex = s->c_x - s->a_x, ey = s->c_y - s->a_y;
    float d = sqrtf(ex * ex + ey * ey);
    if (d > m) {
        ex *= m / d;
        ey *= m / d;
    }
    s->a_x += ex;
    s->a_y += ey;
    *dx = ex;
    *dy = ey;
}

/* Wire value: an exact integer code = (qx + 2048) * 4096 + (qy + 2048), q = round(cm / EXT_POS_Q_CM) in
 * [-2048, 2047]. An integer below 2^24 survives the float32 field bit-exact (no NaN patterns). 0 = bad code. */
uint8_t ext_pos_decode(float val, float *x_cm, float *y_cm)
{
    uint32_t c;
    if (!(val >= 0.0f && val < 16777216.0f)) {
        return 0U;
    }
    c = (uint32_t)val;
    if ((float)c != val) {
        return 0U;
    }
    *x_cm = (float)((int32_t)(c >> 12) - 2048) * EXT_POS_Q_CM;
    *y_cm = (float)((int32_t)(c & 0xFFFU) - 2048) * EXT_POS_Q_CM;
    return 1U;
}
