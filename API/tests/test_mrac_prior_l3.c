/* ============================================================================
 * API/tests/test_mrac_prior_l3.c
 *
 * HOST-TEST RUNNER ONLY - see sil_gate/README.md for the contract.
 *
 * The L3 prior (spec docs/design/2026-10-22-mrac-experiment-stack.md item 3), built WITHOUT
 * -DMRAC_ENABLE_SIGMA_PRIOR (the prior is always built now). Four tests:
 *
 *   1. MRAC_SetPriorElem      bad axis, idx >= MRAC_N_FEATURES, NaN / inf -> 0 and nothing written;
 *                             a valid write -> 1.
 *   2. pull mask              sigma_prior > 0 with an axis's mrac_prior_pull bit clear leaves that axis
 *                             bit-identical to sigma_prior = 0; with the bit set the axis moves.
 *   3. warm start             MRAC_ResetWeights seeds Theta = Whatf = clamp(Theta_prior) on the
 *                             mrac_prior_warm axes only.
 *   4. reseed flag            MRAC_GateStep re-seeds on mrac_prior_reseed only while disarmed, then
 *                             clears the flag; armed, the flag waits and Theta is untouched.
 *
 * Fails to build on the code before item 3 (no MRAC_SetPriorElem / mrac_prior_* symbols).
 * This file is NOT firmware. It is NEVER compiled into the drone's image.
 * ============================================================================
 */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <math.h>

#include "mrac.h"

_imu_st imu_data = {0};
float MRAC_VectorNormSquare(const float *v, int n) { return 0.0f; }
float MRAC_Simple_RBF(float x, float c, float width) { (void)x; (void)c; (void)width; return 0.0f; }

/* Zero-init stand-in; only the four PIDs MRAC_Control reads are written (see test_mrac_sigma_prior.c). */
static CtrlerTypeDef g_fake_state;

static int fails, checks;
static void ok(const char *what, int cond)
{
    checks++;
    if (!cond) { fails++; printf("FAIL  %s\n", what); }
}

/* Steady tracking error inside the active band [e_deadzone, e_freeze] on pitch, roll, yaw and z. */
static void drive(int n_ticks)
{
    int k;

    memset(&g_fake_state, 0, sizeof(g_fake_state));
    g_fake_state.gyroyPID.FB = 28.65f;
    g_fake_state.gyroxPID.FB = 28.65f;
    g_fake_state.gyrozPID.FB = 28.65f;
    g_fake_state.Z_ratePID.FB = 0.5f;
    for (k = 0; k < n_ticks; k++) {
        MRAC_Control((const CtrlerTypeDef *)&g_fake_state);
    }
}

static void reset_state(void)
{
    MRAC_Reset();
    mrac_in_armed = 1;
    mrac_in_phase = 1;
    mrac_inj.fly_ticks = 200;
    mrac_inj.learn_gate = 1;
    mrac_inj.inj_alpha = 1.0f;
    mrac_flags.output_injection_on = 1;
}

static void clear_prior(void)
{
    memset(Theta_prior, 0, sizeof(Theta_prior));
    sigma_prior = 0.0f;
    mrac_prior_pull = MRAC_PRIOR_ALL_AXES;
    mrac_prior_warm = 0U;
    mrac_prior_reseed = 0U;
}

/* First feature with a positive projection bound on this config (so a mid-range prior is non-trivial). */
static int first_bounded(const MRAC_AxisConfig_t *c)
{
    int i;

    for (i = 0; i < MRAC_N_FEATURES; i++) {
        if (c->What_limit[i] > 0.0f) {
            return i;
        }
    }
    return -1;
}

static int theta_differs(const float *a, const float *b, double tol)
{
    int i;

    for (i = 0; i < MAX_NUM_BASIS; i++) {
        if (fabs((double)a[i] - (double)b[i]) > tol) {
            return 1;
        }
    }
    return 0;
}

#define N_TICKS 2000

int main(void)
{
    float base_p[MAX_NUM_BASIS], base_r[MAX_NUM_BASIS], pa[MAX_NUM_BASIS], pb[MAX_NUM_BASIS], rb[MAX_NUM_BASIS];
    int i, ip, ir;

    MRAC_Init();
    clear_prior();
    ip = first_bounded(&mrac_config_pitch);
    ir = first_bounded(&mrac_config_roll);
    ok("pitch and roll have a bounded feature", (ip >= 0) && (ir >= 0));
    if ((ip < 0) || (ir < 0)) {
        printf("%d/%d checks failed\n", fails, checks);
        return 1;
    }

    /* 1. element write bounds */
    ok("axis 6 refused", MRAC_SetPriorElem(6U, 0U, 1.0f) == 0);
    ok("idx MRAC_N_FEATURES refused", MRAC_SetPriorElem(0U, (uint8_t)MRAC_N_FEATURES, 1.0f) == 0);
    ok("NaN refused", MRAC_SetPriorElem(0U, 0U, NAN) == 0);
    ok("inf refused", MRAC_SetPriorElem(0U, 0U, INFINITY) == 0);
    ok("refused writes left the prior zero", Theta_prior[0][0] == 0.0f);
    ok("valid write accepted", MRAC_SetPriorElem(MRAC_AXIS_Y, (uint8_t)(MRAC_N_FEATURES - 1), 0.25f) == 1);
    ok("valid write landed", Theta_prior[MRAC_AXIS_Y][MRAC_N_FEATURES - 1] == 0.25f);
    clear_prior();

    /* 2. pull mask: pitch bit clear -> pitch bit-identical to sigma_prior = 0; roll (bit set) moves */
    Theta_prior[MRAC_AXIS_PITCH][ip] = 0.5f * mrac_config_pitch.What_limit[ip];
    Theta_prior[MRAC_AXIS_ROLL][ir] = 0.5f * mrac_config_roll.What_limit[ir];
    reset_state();
    drive(N_TICKS);
    memcpy(base_p, mrac_state.pitch.Theta, sizeof(base_p));
    memcpy(base_r, mrac_state.roll.Theta, sizeof(base_r));

    sigma_prior = 0.5f;
    reset_state();
    drive(N_TICKS);
    memcpy(pa, mrac_state.pitch.Theta, sizeof(pa));
    ok("pull set: pitch moved off the sigma_prior = 0 run", theta_differs(pa, base_p, 1e-6));

    mrac_prior_pull = (uint8_t)(MRAC_PRIOR_ALL_AXES & ~(1U << (unsigned)MRAC_AXIS_PITCH));
    reset_state();
    drive(N_TICKS);
    memcpy(pb, mrac_state.pitch.Theta, sizeof(pb));
    memcpy(rb, mrac_state.roll.Theta, sizeof(rb));
    ok("pull clear: pitch bit-identical to the sigma_prior = 0 run", memcmp(pb, base_p, sizeof(pb)) == 0);
    ok("pull clear on pitch only: roll still pulled", theta_differs(rb, base_r, 1e-6));
    clear_prior();

    /* 3. warm start: pitch only, upper and lower clamp, mid value passes through */
    Theta_prior[MRAC_AXIS_PITCH][ip] = 0.5f * mrac_config_pitch.What_limit[ip];
    Theta_prior[MRAC_AXIS_PITCH][(ip + 1) % MRAC_N_FEATURES] = 1.0e6f;
    Theta_prior[MRAC_AXIS_PITCH][(ip + 2) % MRAC_N_FEATURES] = -1.0e6f;
    Theta_prior[MRAC_AXIS_ROLL][ir] = 0.5f * mrac_config_roll.What_limit[ir];
    mrac_prior_warm = (uint8_t)(1U << (unsigned)MRAC_AXIS_PITCH);
    MRAC_ResetWeights();
    ok("warm: pitch mid value seeded into Theta",
       mrac_state.pitch.Theta[ip] == 0.5f * mrac_config_pitch.What_limit[ip]);
    ok("warm: pitch mid value seeded into Whatf",
       mrac_state.pitch.Whatf[ip] == 0.5f * mrac_config_pitch.What_limit[ip]);
    ok("warm: upper clamp to What_limit",
       mrac_state.pitch.Theta[(ip + 1) % MRAC_N_FEATURES] ==
       mrac_config_pitch.What_limit[(ip + 1) % MRAC_N_FEATURES]);
    ok("warm: lower clamp to What_lower_limit",
       mrac_state.pitch.Theta[(ip + 2) % MRAC_N_FEATURES] ==
       mrac_config_pitch.What_lower_limit[(ip + 2) % MRAC_N_FEATURES]);
    for (i = 0; i < MAX_NUM_BASIS; i++) {
        if ((mrac_state.roll.Theta[i] != 0.0f) || (mrac_state.roll.Whatf[i] != 0.0f)) {
            break;
        }
    }
    ok("warm: roll (bit clear) stays zero", i == MAX_NUM_BASIS);
    mrac_prior_warm = 0U;
    MRAC_ResetWeights();
    ok("warm off: pitch zero again", mrac_state.pitch.Theta[ip] == 0.0f);

    /* 4. reseed flag: armed -> waits; disarmed (no disarm edge) -> seeds and clears */
    mrac_prior_warm = (uint8_t)(1U << (unsigned)MRAC_AXIS_PITCH);
    mrac_prior_reseed = 1U;
    mrac_in_armed = 1;
    mrac_inj.prev_armed = 1;
    MRAC_GateStep();
    ok("armed: reseed flag kept", mrac_prior_reseed == 1U);
    ok("armed: Theta untouched", mrac_state.pitch.Theta[ip] == 0.0f);

    mrac_in_armed = 0;
    mrac_inj.prev_armed = 0;
    MRAC_GateStep();
    ok("disarmed: reseed flag cleared", mrac_prior_reseed == 0U);
    ok("disarmed: Theta seeded from the prior",
       mrac_state.pitch.Theta[ip] == 0.5f * mrac_config_pitch.What_limit[ip]);

    /* 0.125f is exact in binary: a 32-bit x87 build with -std=c99 evaluates a literal like 0.123f in long double
     * (FLT_EVAL_METHOD 2), so == against the stored float would fail with Theta untouched. */
    mrac_state.pitch.Theta[ip] = 0.125f;
    mrac_inj.prev_armed = 0;
    MRAC_GateStep();
    ok("disarmed, no flag: no reset", mrac_state.pitch.Theta[ip] == 0.125f);

    printf("%s: %d/%d checks passed\n", fails ? "FAIL" : "PASS", checks - fails, checks);
    return fails ? 1 : 0;
}
