#include "att_ctrl.h"
#include "att_ctrl_gains.h"
#include <math.h>
#include <string.h>

volatile uint8_t g_att_mode[4] = {0};
volatile float g_att_alpha[4] = {0};
volatile uint8_t g_att_latch[4] = {0};
volatile uint8_t g_att_would_latch[4] = {0};
volatile uint8_t g_hov_ff_mode = 0;
float g_att_v_filt = 16.8f;

static AttCtrlState s_att[4];
static uint32_t s_cyccnt_total = 0;

extern volatile uint8_t g_ctrl_pid_fallback_active;
extern volatile uint8_t s_alt_valid_tick;
extern uint8_t flight_phase;
#define FLIGHT_PHASE_FLYING 5
#define FLIGHT_PHASE_LANDING 6

static float s_latch_angle_deg = 35.0f;
static float s_latch_rate_dps = 300.0f;
static float s_latch_yaw_err_deg = 45.0f;
static float s_du_max[4] = { 200.0f, 200.0f, 200.0f, 200.0f };
static uint32_t s_cpu_max = 50400;

static void rls_update(AttCtrlState *s, float w_next, float w_prev, float u_del) {
    float phi[3] = { w_prev, u_del, 1.0f };
    float err = w_next - (s->rls_p[0] * phi[0] + s->rls_p[1] * phi[1] + s->rls_p[2] * phi[2]);
    float k_gain[3] = { 0.01f * phi[0], 0.01f * phi[1], 0.01f * phi[2] }; /* stub static gain to compile and be finite */

    s->rls_p[0] += k_gain[0] * err;
    s->rls_p[1] += k_gain[1] * err;
    s->rls_p[2] += k_gain[2] * err;
}
static float clampf(float v, float lo, float hi)
{
	return v < lo ? lo : (v > hi ? hi : v);
}

#define ATTC_INT_BAND 0.5f
#define ATTC_INT_MAX 50.0f
#define PROPOSED_RLS_FORGET 0.995f
#define PROPOSED_RLS_COV_MIN 1e-4f
#define PROPOSED_RLS_COV_MAX 1e4f

static void integrate(AttCtrlState *s, float pos, float des, float dt)
{
    float e = pos - des;
    if (fabsf(e) < ATTC_INT_BAND) {
        s->z = clampf(s->z + dt * e, -ATTC_INT_MAX, ATTC_INT_MAX);
    }
}

static void predicted_error(uint8_t axis, AttCtrlState *s, const float *phi, const float *gam, float d, float dt, float tau, float des, float pos, float vel, float vff, float x[4])
{
    float x0[3] = {pos, vel, s->m_est};
    float xp[3];
    int r, j;
    int di = (int)d;
    (void)axis;
    (void)dt;
    (void)tau;

    for (r = 0; r < 3; r++) {
        float acc = phi[r*3+0] * x0[0] + phi[r*3+1] * x0[1] + phi[r*3+2] * x0[2];
        if (di > 0) {
            for (j = 0; j < di && j < ATT_CTRL_DMAX; j++) {
                int head_idx = s->head + j;
                float u_j = s->pipe[head_idx >= di ? head_idx - di : head_idx];
                acc += gam[r*di + j] * u_j;
            }
        }
        xp[r] = acc;
    }
    x[0] = xp[0] - des;
    x[1] = xp[1] - vff;
    x[2] = xp[2];
    x[3] = s->z;
}

static float lqr_step(uint8_t axis, AttCtrlState *s, const AttCtrlIn *in)
{
    float x[4] = {0.0f, 0.0f, 0.0f, 0.0f};
    float u = 0.0f;
    float dt = (axis == ATT_Z) ? 0.010f : 0.005f;
    float tau = 0.025f; /* Simplified */
    float d = 3.0f;
    const float *phi = (const float *)ATTC_ROLL_PHI;
    const float *gam = (const float *)ATTC_ROLL_GAM;
    const float *k = ATTC_ROLL_LQR_K;
    float u_min = -400.0f;
    float u_max = 400.0f;
    float pos = in->y;
    float des = in->r;
    float u_del;

    if (axis == ATT_PITCH) {
        phi = (const float *)ATTC_PITCH_PHI; gam = (const float *)ATTC_PITCH_GAM; k = ATTC_PITCH_LQR_K;
    } else if (axis == ATT_YAW) {
        phi = (const float *)ATTC_YAW_PHI; gam = (const float *)ATTC_YAW_GAM; k = ATTC_YAW_LQR_K;
        tau = 0.030f; d = 4.0f; u_min = -300.0f; u_max = 300.0f;
    } else if (axis == ATT_Z) {
        phi = (const float *)ATTC_Z_PHI; gam = (const float *)ATTC_Z_GAM; k = ATTC_Z_LQR_K;
        tau = 0.050f; d = 4.0f; u_min = -1000.0f; u_max = 1000.0f;
    }

    if (axis == ATT_YAW) {
        pos = fmodf(pos + 3.14159f, 2.0f * 3.14159f) - 3.14159f;
        des = fmodf(des + 3.14159f, 2.0f * 3.14159f) - 3.14159f;
        pos = des + (fmodf(pos - des + 3.14159f, 2.0f * 3.14159f) - 3.14159f);
    }

    integrate(s, pos, des, dt);
    predicted_error(axis, s, phi, gam, d, dt, tau, des, pos, in->w, in->w_ff, x);

    u = -(k[0]*x[0] + k[1]*x[1] + k[2]*x[2] + k[3]*x[3]);
    u = clampf(u, u_min, u_max);

    u_del = (d > 0.0f) ? s->pipe[s->head] : u;
    s->m_est += (u_del - s->m_est) * (dt / tau);

    return u;
}

static float mpc_solve(uint8_t axis, AttCtrlState *s, const float x[4])
{
    float lin[ATT_CTRL_NMAX] = {0.0f}, u[ATT_CTRL_NMAX] = {0.0f}, y[ATT_CTRL_NMAX] = {0.0f}, un[ATT_CTRL_NMAX] = {0.0f};
    float u_min = -400.0f;
    float u_max = 400.0f;
    float dt = (axis == ATT_Z) ? 0.010f : 0.005f;
    float tau = 0.025f;
    float d = 3.0f;
    const float *f_mat = (const float *)ATTC_ROLL_MPC_F;
    const float *h_mat = (const float *)ATTC_ROLL_MPC_H;
    float t = 1.0f;
    int i, j, it;
    float step_len = 0.1f; /* stub */

    if (axis == ATT_PITCH) {
        f_mat = (const float *)ATTC_PITCH_MPC_F; h_mat = (const float *)ATTC_PITCH_MPC_H;
    } else if (axis == ATT_YAW) {
        f_mat = (const float *)ATTC_YAW_MPC_F; h_mat = (const float *)ATTC_YAW_MPC_H;
        tau = 0.030f; d = 4.0f; u_min = -300.0f; u_max = 300.0f;
    } else if (axis == ATT_Z) {
        f_mat = (const float *)ATTC_Z_MPC_F; h_mat = (const float *)ATTC_Z_MPC_H;
        tau = 0.050f; d = 4.0f; u_min = -1000.0f; u_max = 1000.0f;
    }

    for (i = 0; i < ATT_CTRL_NMAX; i++) {
        lin[i] = f_mat[i*4+0] * x[0] + f_mat[i*4+1] * x[1] + f_mat[i*4+2] * x[2] + f_mat[i*4+3] * x[3];
        u[i] = y[i] = s->plan[i];
    }

    for (it = 0; it < 15; it++) {
        float t_new = 0.5f * (1.0f + sqrtf(1.0f + 4.0f * t * t));
        float mom = (t - 1.0f) / t_new;
        for (i = 0; i < ATT_CTRL_NMAX; i++) {
            float g = lin[i];
            for (j = 0; j < ATT_CTRL_NMAX; j++)
                g += h_mat[i*ATT_CTRL_NMAX + j] * y[j];
            un[i] = clampf(y[i] - step_len * g, u_min, u_max);
        }
        for (i = 0; i < ATT_CTRL_NMAX; i++) {
            y[i] = un[i] + mom * (un[i] - u[i]);
            u[i] = un[i];
        }
        t = t_new;
    }

    for (i = 0; i < ATT_CTRL_NMAX - 1; i++) {
        s->plan[i] = u[i+1];
    }
    s->plan[ATT_CTRL_NMAX-1] = u[ATT_CTRL_NMAX-1];

    {
        float out = u[0];
        float u_del = (d > 0.0f) ? s->pipe[s->head] : out;
        s->m_est += (u_del - s->m_est) * (dt / tau);
        return out;
    }
}

static float mpc_step(uint8_t axis, AttCtrlState *s, const AttCtrlIn *in)
{
    float x[4] = {0.0f, 0.0f, 0.0f, 0.0f};
    float dt = (axis == ATT_Z) ? 0.010f : 0.005f;
    float tau = 0.025f; /* Simplified */
    float d = 3.0f;
    const float *phi = (const float *)ATTC_ROLL_PHI;
    const float *gam = (const float *)ATTC_ROLL_GAM;
    float pos = in->y;
    float des = in->r;

    if (axis == ATT_PITCH) {
        phi = (const float *)ATTC_PITCH_PHI; gam = (const float *)ATTC_PITCH_GAM;
    } else if (axis == ATT_YAW) {
        phi = (const float *)ATTC_YAW_PHI; gam = (const float *)ATTC_YAW_GAM;
        tau = 0.030f; d = 4.0f;
    } else if (axis == ATT_Z) {
        phi = (const float *)ATTC_Z_PHI; gam = (const float *)ATTC_Z_GAM;
        tau = 0.050f; d = 4.0f;
    }

    if (axis == ATT_YAW) {
        pos = fmodf(pos + 3.14159f, 2.0f * 3.14159f) - 3.14159f;
        des = fmodf(des + 3.14159f, 2.0f * 3.14159f) - 3.14159f;
        pos = des + (fmodf(pos - des + 3.14159f, 2.0f * 3.14159f) - 3.14159f);
    }

    integrate(s, pos, des, dt);
    predicted_error(axis, s, phi, gam, d, dt, tau, des, pos, in->w, in->w_ff, x);

    return mpc_solve(axis, s, x);
}

static float adrc_step(uint8_t axis, AttCtrlState *s, const AttCtrlIn *in)
{
    float dt = (axis == ATT_Z) ? 0.010f : 0.005f;
    float y = in->y;
    float w = in->w;
    float des = in->r;
    float d = 3.0f;

    float wc = 1.0f;
    float wo = 1.0f;
    float b0 = 100.0f;
    float u_min = -400.0f;
    float u_max = 400.0f;

    float u_del, e, b1, b2, u0, u;

    if (axis == ATT_YAW) {
        d = 4.0f; u_min = -300.0f; u_max = 300.0f;
        y = fmodf(y + 3.14159f, 2.0f * 3.14159f) - 3.14159f;
        des = fmodf(des + 3.14159f, 2.0f * 3.14159f) - 3.14159f;
        y = des + (fmodf(y - des + 3.14159f, 2.0f * 3.14159f) - 3.14159f);
    } else if (axis == ATT_Z) {
        d = 4.0f; u_min = -1000.0f; u_max = 1000.0f;
    }

    if (!s->init) {
        s->z1 = w;
        s->init = 1U;
    }

    u_del = (d > 0.0f) ? s->pipe[s->head] : 0.0f;
    e = s->z1 - w;
    b1 = 2.0f * wo;
    b2 = wo * wo;

    s->z1 = s->z1 + dt * (s->z2 - b1 * e + b0 * u_del);
    s->z2 = s->z2 + dt * (-b2 * e);

    u0 = wc * wc * (des - y) - 2.0f * wc * w;
    u = clampf((u0 - s->z2) / b0, u_min, u_max);

    return u;
}

float AttCtrl_Step_Impl(uint8_t axis, AttCtrlState *s, const AttCtrlIn *in)
{
    if (s->kind == ATT_CTRL_LQR) {
        return lqr_step(axis, s, in);
    } else if (s->kind == ATT_CTRL_MPC) {
        return mpc_step(axis, s, in);
    } else if (s->kind == ATT_CTRL_ADRC) {
        return adrc_step(axis, s, in);
    }
    return 0.0f;
}

void AttCtrl_TickBegin(void)
{
    s_cyccnt_total = 0;
}

void AttCtrl_ResetAll(void)
{
    int i;
    for (i = 0; i < 4; i++) {
        memset(&s_att[i], 0, sizeof(AttCtrlState));
        g_att_latch[i] = LATCH_NONE;
        g_att_would_latch[i] = LATCH_NONE;
    }
}

static uint8_t check_latch(uint8_t axis, const AttCtrlIn *in, float u_new, float u_pid)
{
    float deg = 180.0f / 3.14159265f;
    float e;
    (void)u_new; (void)u_pid;

    if (isnan(u_new) || isinf(u_new) || isnan(in->y) || isinf(in->y) || isnan(in->w) || isinf(in->w)) return LATCH_NAN_INF;

    if (axis == ATT_ROLL || axis == ATT_PITCH) {
        if (fabsf(in->y * deg) > s_latch_angle_deg) return LATCH_ANGLE_BOUND;
    }
    if (fabsf(in->w * deg) > s_latch_rate_dps) return LATCH_RATE_BOUND;
    if (axis == ATT_YAW) {
        e = in->r - in->y;
        e = fmodf(e + 3.14159265f, 2.0f * 3.14159265f);
        if (e < 0) e += 2.0f * 3.14159265f;
        e -= 3.14159265f;
        if (fabsf(e * deg) > s_latch_yaw_err_deg) return LATCH_YAW_ERR_BOUND;
    }

    if (s_cyccnt_total > s_cpu_max) return LATCH_CPU_OVERRUN;

    if (g_ctrl_pid_fallback_active) return LATCH_PID_FALLBACK;

    if (axis == ATT_Z) {
        if (!s_alt_valid_tick) return LATCH_Z_NOT_VALID;
        if (in->y < 0.15f) return LATCH_Z_TOO_LOW;
        if (flight_phase == FLIGHT_PHASE_LANDING) return LATCH_Z_LANDING;
        if (in->w < -0.5f) return LATCH_Z_SINK_FAST;
        if (fabsf(in->r - in->y) > 0.25f) return LATCH_Z_ERR_BOUND;
    }

    return LATCH_NONE;
}

void AttCtrl_Commit(AttCtrlState *s, float u_applied) {
    s->pipe[s->head] = u_applied;
    s->head = (uint8_t)(s->head + 1 >= ATT_CTRL_DMAX ? 0 : s->head + 1);
    s->u_prev = u_applied;
}

extern float real_voltage;

float AttCtrl_Apply(uint8_t axis, float u_pid, const AttCtrlIn *in)
{
    AttCtrlState *s = &s_att[axis];
    float dt;
    float u_new;
    uint8_t latch;
    float u_applied;

    if (s->mode != g_att_mode[axis]) {
        memset(s, 0, sizeof(*s));
        s->mode = g_att_mode[axis];
        s->kind = g_att_mode[axis] > 0 ? g_att_mode[axis] : 0;
    }

    dt = (axis == ATT_Z) ? 0.010f : 0.005f;
    g_att_v_filt = g_att_v_filt + (dt / 2.0f) * (real_voltage - g_att_v_filt);

    if (s->mode == ATT_MODE_OFF) {
        return u_pid;
    }

    u_new = AttCtrl_Step_Impl(axis, s, in);

    latch = check_latch(axis, in, u_new, u_pid);
    if (fabsf(u_new - u_pid) > s_du_max[axis]) {
        s->u_sat_ticks++;
        if (s->u_sat_ticks > 20) {
            latch = LATCH_U_SAT_TIMEOUT;
        }
    } else {
        s->u_sat_ticks = 0;
    }

    if (s->mode == ATT_MODE_SHADOW) {
        if (latch) g_att_would_latch[axis] = latch;
        AttCtrl_Commit(s, u_pid);
        return u_pid;
    }

    if (latch && g_att_latch[axis] == LATCH_NONE) {
        g_att_latch[axis] = latch;
    }

    if (g_att_latch[axis] != LATCH_NONE) {
        s->a_eff = 0.0f;
    } else {
        float diff = g_att_alpha[axis] - s->a_eff;
        float max_step = 2.0f * dt;
        s->a_eff += clampf(diff, -max_step, max_step);
    }

    if (s->a_eff == 0.0f) {
        return u_pid;
    }

    u_applied = (1.0f - s->a_eff) * u_pid + s->a_eff * u_new;

    if (flight_phase == FLIGHT_PHASE_FLYING && latch == LATCH_NONE) {
        float d = (axis == ATT_YAW || axis == ATT_Z) ? 4.0f : 3.0f;
        float u_del = (d > 0) ? s->pipe[s->head] : u_applied;
        rls_update(s, in->w, s->z1, u_del);
    }
    s->z1 = in->w;

    AttCtrl_Commit(s, u_applied);

    return u_applied;
}
