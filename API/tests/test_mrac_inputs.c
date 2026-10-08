/* API/tests/test_mrac_inputs.c - host test of the inputs MRAC_Control reads (WP-38).
 *
 * A. Units. imu_data.pit/rol are degrees (API/imu_update.c:196-197). MRAC converts them with MRAC_DEG2RAD where
 *    it reads them: the simplex envelope (roll_max/pitch_max, rad) and, in the V3 build, the RBF angle input
 *    (rad / rbf_ang_scale). Before WP-38 the degrees were used as rad: a 3.2 deg tilt passed the 3.14 "rad" limit.
 * B. Input guard. A non-finite x, r, u_nom or cross skips that axis (Theta untouched, u_ad 0); the next nan_rearm
 *    finite ticks keep u_ad at 0 with learning frozen; then MRAC re-engages. Before WP-38 one NaN poisoned Theta.
 *
 * Built by tools/host_tests.py, rows mrac_inputs (STRUCT6) and mrac_inputs_rbf (-DMRAC_VARIANT=1), on a copy of
 * API/mrac*.[ch] so the stubs win:
 *   gcc -std=c99 -Wall -Wextra -I<copy> -IAPI/tests/stubs API/tests/test_mrac_inputs.c <copy>/mrac.c <copy>/mrac_math.c -lm
 */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "mrac.h"
#include "mrac_math.h"

_imu_st imu_data;

#define DEG2RAD 0.0174533f       /* MRAC_DEG2RAD, API/mrac.c */

static int checks, fails;
static void ok(const char *what, int cond)
{
    checks++;
    if (!cond) {
        fails++;
        printf("FAIL  %s\n", what);
    }
}

static CtrlerTypeDef ctrl;

/* Flying, learn-gated, injected: the state the equivalence driver also starts from (API/tests/test_mrac_equiv.c). */
static void fly(void)
{
    MRAC_Init();
    memset(&mrac_state, 0, sizeof(mrac_state));
    memset(&mrac_flags, 0, sizeof(mrac_flags));
    mrac_flags.adaptation_on = 1;
    mrac_flags.axis_enable_pitch = 1;
    mrac_flags.axis_enable_roll = 1;
    mrac_flags.axis_enable_yaw = 1;
    mrac_flags.output_injection_on = 1;
    mrac_in_armed = 1;
    mrac_in_phase = MRAC_PHASE_FLYING;
    mrac_inj.fly_ticks = 65535;
    mrac_inj.prev_armed = 1;
    mrac_inj.prev_injection_on = 1;
    mrac_inj.ramp_p = 1.0f;
    mrac_inj.inj_alpha = 1.0f;
    mrac_inj.learn_gate = 1;
    mrac_simplex.mode = 0;
    mrac_simplex.tripped = 0;
    mrac_simplex.reason = 0;
    mrac_simplex.would_trip_count = 0;
    mrac_simplex.roll_max = 3.14f;
    mrac_simplex.pitch_max = 3.14f;
    memset(&ctrl, 0, sizeof(ctrl));
    ctrl.gyroyPID.FB = 30.0f;            /* deg/s */
    ctrl.gyroyPID.Des = 30.0f;
    ctrl.gyroxPID.FB = -20.0f;
    ctrl.gyroxPID.Des = -20.0f;
}

static void tick(float pit_deg, float rol_deg)
{
    imu_data.pit = pit_deg;
    imu_data.rol = rol_deg;
    MRAC_Control(&ctrl);
}

/* A: the simplex compares the tilt in rad. Observe mode counts would-be trips without acting. */
static void test_simplex_envelope_in_rad(void)
{
    fly();
    mrac_simplex.mode = 2;
    mrac_simplex.pitch_max = 0.1f;       /* rad = 5.73 deg */
    mrac_simplex.roll_max = 0.1f;
    tick(5.0f, 0.0f);
    ok("A simplex: 5 deg pitch is inside 0.1 rad", mrac_simplex.would_trip_count == 0 && mrac_simplex.reason == 0);
    tick(6.0f, 0.0f);
    ok("A simplex: 6 deg pitch is outside 0.1 rad (reason 2)", mrac_simplex.would_trip_count == 1 && mrac_simplex.reason == 2);
    tick(0.0f, 0.0f);
    tick(0.0f, -6.0f);
    ok("A simplex: -6 deg roll is outside 0.1 rad (reason 1)", mrac_simplex.would_trip_count == 2 && mrac_simplex.reason == 1);

    fly();
    mrac_simplex.mode = 2;               /* default limits 3.14 rad: no attitude can reach them */
    tick(10.0f, -10.0f);
    tick(3.2f, 3.2f);
    tick(170.0f, -170.0f);
    ok("A simplex: 3.2..170 deg stay inside the default 3.14 rad (tripped as 'rad' before WP-38)",
       mrac_simplex.would_trip_count == 0);
    tick(181.0f, 0.0f);
    ok("A simplex: 181 deg = 3.159 rad is outside 3.14 rad", mrac_simplex.would_trip_count == 1);
}

#if MRAC_VARIANT == MRAC_VARIANT_STRUCT6_RBF12
/* A: the V3 grid sees the angle in rad / rbf_ang_scale. Centres as in API/mrac.c mrac_rbf_rate_c / mrac_rbf_ang_c. */
static void test_rbf_angle_in_rad(void)
{
    static const float rc[4] = {-1.5f, -0.5f, 0.5f, 1.5f};
    static const float ac[3] = {-1.0f, 0.0f, 1.0f};
    const float *phi = mrac_state.pitch.Phi + MRAC_N_STRUCT;
    float xr, xa, d, dmax = 0.0f;
    int i, j;

    fly();
    mrac_config_pitch.rbf_on = 1.0f;
    tick(15.0f, 0.0f);                   /* 15 deg = 0.2618 rad = 1.007 x rbf_ang_scale 0.26 */
    xr = (30.0f * DEG2RAD) / mrac_config_pitch.rbf_rate_scale;
    xa = 15.0f * DEG2RAD / mrac_config_pitch.rbf_ang_scale;
    for (i = 0; i < 4; i++) {
        for (j = 0; j < 3; j++) {
            d = fabsf(phi[i * 3 + j] - MRAC_Simple_RBF(xr, rc[i], 1.0f) * MRAC_Simple_RBF(xa, ac[j], 1.0f));
            if (d > dmax) dmax = d;
        }
    }
    ok("A rbf: phi = g(rate rad/s / scale) * g(angle rad / scale)", dmax <= 1e-6f);
    ok("A rbf: 15 deg lands on the +1 angle centre", phi[1 * 3 + 2] > phi[1 * 3 + 1] && phi[1 * 3 + 2] > 0.1f);
}
#endif

/* Finite ticks with a steady tracking error on every axis (inside the deadzone/freeze band), so Theta learns. */
static int g_k;
static void excite(int n)
{
    int k;
    float t;

    for (k = 0; k < n; k++, g_k++) {
        t = (float)g_k * MRAC_DT;
        ctrl.gyroyPID.Des = 30.0f + 20.0f * sinf(3.0f * t);
        ctrl.gyroyPID.FB = ctrl.gyroyPID.Des - 10.0f;
        ctrl.gyroyPID.U = 100.0f * sinf(2.0f * t);
        ctrl.gyroxPID.Des = -20.0f + 15.0f * sinf(2.5f * t);
        ctrl.gyroxPID.FB = ctrl.gyroxPID.Des + 8.0f;
        ctrl.gyroxPID.U = 80.0f * sinf(1.7f * t);
        ctrl.gyrozPID.Des = 10.0f * sinf(1.1f * t);
        ctrl.gyrozPID.FB = ctrl.gyrozPID.Des - 6.0f;
        ctrl.gyrozPID.U = 60.0f * sinf(1.3f * t);
        ctrl.Z_ratePID.Des = 0.3f * sinf(0.9f * t);
        ctrl.Z_ratePID.FB = ctrl.Z_ratePID.Des - 0.15f;
        ctrl.Z_ratePID.U = 40.0f * sinf(0.7f * t);
        tick(2.0f, -1.0f);
    }
}

static int all_finite(const MRAC_AxisState_t *s)
{
    int i;
    for (i = 0; i < MRAC_N_FEATURES; i++) {
        if (!(s->Theta[i] - s->Theta[i] == 0.0f) || !(s->Whatf[i] - s->Whatf[i] == 0.0f)) return 0;
    }
    return s->u_ad - s->u_ad == 0.0f;
}

/* Hold-off ticks: u_ad stays 0 and Theta does not move; returns 1 if that held on every tick. */
static int hold_off(MRAC_AxisState_t *s, int n)
{
    float th[MAX_NUM_BASIS];
    int k, good = 1;

    memcpy(th, s->Theta, sizeof(th));
    for (k = 0; k < n; k++) {
        excite(1);
        good &= (s->u_ad == 0.0f) && (memcmp(th, s->Theta, sizeof(th)) == 0);
    }
    return good;
}

static void test_nan_guard(void)
{
    float th[MAX_NUM_BASIS];
    uint16_t n;

    fly();
    g_k = 0;
    excite(400);
    n = (uint16_t)mrac_config_pitch.nan_rearm;
    ok("B setup: pitch learns and u_ad != 0", mrac_state.pitch.u_ad != 0.0f && mrac_state.pitch.Theta[0] != 0.0f);
    ok("B setup: nan_rearm default 200 ticks (1 s) on every axis", n == 200U && mrac_config_roll.nan_rearm == 200.0f &&
       mrac_config_yaw.nan_rearm == 200.0f && mrac_config_z.nan_rearm == 200.0f);

    /* NaN pitch rate: pitch skipped; roll too (its cross term p*r); yaw and z do not read it */
    memcpy(th, mrac_state.pitch.Theta, sizeof(th));
    ctrl.gyroyPID.FB = NAN;
    tick(2.0f, -1.0f);
    ok("B NaN rate: pitch u_ad 0, Theta untouched, hold armed",
       mrac_state.pitch.u_ad == 0.0f && memcmp(th, mrac_state.pitch.Theta, sizeof(th)) == 0 && mrac_state.pitch.nan_hold == n);
    ok("B NaN rate: roll (cross p*r) held, yaw and z running",
       mrac_state.roll.nan_hold == n && mrac_state.roll.u_ad == 0.0f && mrac_state.yaw.nan_hold == 0U &&
       mrac_state.yaw.u_ad != 0.0f && mrac_state.z_rate.nan_hold == 0U);
    ok("B NaN rate: every weight and u_ad still finite", all_finite(&mrac_state.pitch) && all_finite(&mrac_state.roll) &&
       all_finite(&mrac_state.yaw) && all_finite(&mrac_state.z_rate));

    ok("B hold-off: 200 finite ticks with u_ad 0 and Theta frozen", hold_off(&mrac_state.pitch, (int)n));
    ok("B hold-off: counter back to 0", mrac_state.pitch.nan_hold == 0U && mrac_state.roll.nan_hold == 0U);
    excite(1);
    ok("B re-engaged: pitch and roll u_ad != 0 and finite on the next tick", mrac_state.pitch.u_ad != 0.0f &&
       mrac_state.roll.u_ad != 0.0f && all_finite(&mrac_state.pitch) && all_finite(&mrac_state.roll));
    memcpy(th, mrac_state.pitch.Theta, sizeof(th));
    excite(50);
    ok("B re-engaged: pitch learns again", memcmp(th, mrac_state.pitch.Theta, sizeof(th)) != 0);

    /* Inf nominal control on yaw, NaN command on z: each held alone */
    excite(1);
    ctrl.gyrozPID.U = INFINITY;
    tick(2.0f, -1.0f);
    ok("B Inf yaw U: yaw held, pitch running", mrac_state.yaw.nan_hold == n && mrac_state.yaw.u_ad == 0.0f &&
       mrac_state.pitch.nan_hold == 0U && mrac_state.pitch.u_ad != 0.0f);
    excite(1);
    ctrl.Z_ratePID.Des = NAN;
    tick(2.0f, -1.0f);
    ok("B NaN z command: z held", mrac_state.z_rate.nan_hold == n && mrac_state.z_rate.u_ad == 0.0f &&
       all_finite(&mrac_state.z_rate));

    /* A second NaN inside the hold-off restarts it */
    excite(300);
    ctrl.gyroyPID.FB = NAN;
    tick(2.0f, -1.0f);
    excite(100);
    ctrl.gyroyPID.FB = -NAN;
    tick(2.0f, -1.0f);
    ok("B NaN in the hold-off restarts it", mrac_state.pitch.nan_hold == n);
    ok("B ...and the full hold-off follows", hold_off(&mrac_state.pitch, (int)n) && mrac_state.pitch.nan_hold == 0U);

    /* nan_rearm 0: back on the next finite tick */
    mrac_config_pitch.nan_rearm = 0.0f;
    ctrl.gyroyPID.FB = NAN;
    tick(2.0f, -1.0f);
    ok("B nan_rearm 0: no hold", mrac_state.pitch.nan_hold == 0U && mrac_state.pitch.u_ad == 0.0f);
    excite(1);
    ok("B nan_rearm 0: engaged on the next finite tick", mrac_state.pitch.u_ad != 0.0f);
}

#if MRAC_VARIANT == MRAC_VARIANT_MULTI
/* FW-B: cfg basis picks the feature set at run time. 0 = S6 (ext slots stay 0), 3 = RBF12 (12 ext slots,
 * phi[0..3] zeroed, u_nom / xm kept), > MRAC_BASIS_HI refused. */
static int ext_nonzero(const float *phi)
{
    int k, n = 0;
    for (k = MRAC_N_STRUCT; k < MRAC_N_FEATURES; k++) n += (phi[k] != 0.0f);
    return n;
}

/* vp 13 / 14: pitch at 30 deg/s and 5 deg = 0.15 of the 200 deg/s limit and 1/3 of the 15 deg limit. The 24
 * ext slots must match the closed form: RBF24T gap widths (rate 0.4 0.3 0.2 0.2 0.3 0.4, tilt 0.4) and RBF24D
 * four 3x2 dolls at s = 1, 1/2, 1/4, 1/8 (rate width s, tilt width 2s). */
static double gauss(double x, double c, double w) { return exp(-0.5 * ((x - c) / w) * ((x - c) / w)); }

static void test_retuned_rbf(void)
{
    static const double rc[6] = {-0.7, -0.3, -0.1, 0.1, 0.3, 0.7}, rw[6] = {0.4, 0.3, 0.2, 0.2, 0.3, 0.4};
    static const double ac[4] = {-0.6, -0.2, 0.2, 0.6};
    const float *phi = mrac_state.pitch.Phi;
    double xr = 30.0 / 200.0, xa = 5.0 / 15.0, e, err;
    int i, j, l, good;

    fly();
    ok("R basis 7 (RBF24T) accepted", MRAC_VariantParamSet(MRAC_AXIS_PITCH, MRAC_VF_BASIS, 7.0f) == 1U);
    tick(5.0f, -3.0f);
    err = 0.0;
    for (i = 0; i < 6; i++) {
        for (j = 0; j < 4; j++) {
            e = gauss(xr, rc[i], rw[i]) * gauss(xa, ac[j], 0.4);
            err = fmax(err, fabs((double)phi[MRAC_N_STRUCT + i * 4 + j] - e));
        }
    }
    ok("R RBF24T: 24 slots match the gap-width closed form", err < 1e-4);
    ok("R RBF24T: phi[0..3] zeroed", phi[0] == 0.0f && phi[1] == 0.0f && phi[2] == 0.0f && phi[3] == 0.0f);
    ok("R RBF24T: nearest bump (0.1, 0.2) is the largest", phi[MRAC_N_STRUCT + 3 * 4 + 2] > 0.8f);
    ok("R basis 8 (RBF24D) accepted", MRAC_VariantParamSet(MRAC_AXIS_PITCH, MRAC_VF_BASIS, 8.0f) == 1U);
    tick(5.0f, -3.0f);
    err = 0.0;
    for (l = 0; l < 4; l++) {
        double sc = ldexp(1.0, -l);
        for (i = 0; i < 3; i++) {
            for (j = 0; j < 2; j++) {
                e = gauss(xr, sc * (i - 1), sc) * gauss(xa, sc * (2 * j - 1), 2.0 * sc);
                err = fmax(err, fabs((double)phi[MRAC_N_STRUCT + l * 6 + i * 2 + j] - e));
            }
        }
    }
    ok("R RBF24D: 24 slots match the doll closed form", err < 1e-4);
    ok("R RBF24D: phi[0..3] zeroed", phi[0] == 0.0f && phi[1] == 0.0f && phi[2] == 0.0f && phi[3] == 0.0f);
    /* vp 15: ext 8..23 = the RBF24T rate +-0.1 / +-0.3 cells (slots 4..19), ext 0 = sin(tilt) of S10X */
    ok("R basis 9 (S10X+RBF16) accepted", MRAC_VariantParamSet(MRAC_AXIS_PITCH, MRAC_VF_BASIS, 9.0f) == 1U);
    tick(5.0f, -3.0f);
    err = 0.0;
    for (i = 1; i < 5; i++) {
        for (j = 0; j < 4; j++) {
            e = gauss(xr, rc[i], rw[i]) * gauss(xa, ac[j], 0.4);
            err = fmax(err, fabs((double)phi[MRAC_N_STRUCT + 8 + (i - 1) * 4 + j] - e));
        }
    }
    ok("R S10X+RBF16: ext 8..23 match RBF24T cells 4..19", err < 1e-4);
    ok("R S10X+RBF16: phi[0..3] zeroed, ext 0 = sin(5 deg)",
       phi[0] == 0.0f && phi[1] == 0.0f && phi[2] == 0.0f && phi[3] == 0.0f
       && fabsf(phi[MRAC_N_STRUCT] - sinf(5.0f * 0.017453293f)) < 1e-4f);
    MRAC_VariantParamSet(MRAC_AXIS_PITCH, MRAC_VF_BASIS, 8.0f);   /* the limits check below is RBF24D's */
    /* every slot finite and inside [0, 1] at the limits too */
    good = 1;
    tick(15.0f, -15.0f);
    ctrl.gyroyPID.FB = 200.0f;
    tick(15.0f, -15.0f);
    for (i = MRAC_N_STRUCT; i < MRAC_N_FEATURES; i++) good &= (phi[i] >= 0.0f && phi[i] <= 1.0f);
    ok("R RBF24D at the limits: slots finite in [0, 1]", good);
    MRAC_VariantParamSet(MRAC_AXIS_PITCH, MRAC_VF_BASIS, 0.0f);
}

static void test_multi_basis(void)
{
    const float *phi = mrac_state.pitch.Phi;
    fly();
    tick(5.0f, -3.0f);
    ok("M basis 0: ext slots stay 0", ext_nonzero(phi) == 0 && phi[1] != 0.0f);
    ok("M basis 3 accepted", MRAC_VariantParamSet(MRAC_AXIS_PITCH, MRAC_VF_BASIS, 3.0f) == 1U);
    tick(5.0f, -3.0f);
    ok("M RBF12: 12 ext slots live, the rest 0", ext_nonzero(phi) == 12 && phi[MRAC_N_STRUCT + 12] == 0.0f);
    ok("M RBF12: phi[0..3] zeroed", phi[0] == 0.0f && phi[1] == 0.0f && phi[2] == 0.0f && phi[3] == 0.0f);
    ok("M basis 4 (RBF24) fills all 24", MRAC_VariantParamSet(MRAC_AXIS_PITCH, MRAC_VF_BASIS, 4.0f) == 1U);
    tick(5.0f, -3.0f);
    ok("M RBF24: 24 ext slots live", ext_nonzero(phi) == 24);
    mrac_vp12.mask[0] = mrac_vp12.mask_act[0] = 16777215.0f;   /* the all-on default: must not round to bit 24 (exp15 bug) */
    tick(5.0f, -3.0f);
    ok("M mask 0xFFFFFF: all 24 ext slots live", ext_nonzero(phi) == 24);
    mrac_vp12.mask[0] = mrac_vp12.mask_act[0] = 8388609.0f;    /* bits 0 and 23 */
    tick(5.0f, -3.0f);
    ok("M mask bits 0+23: exactly slots 0 and 23", ext_nonzero(phi) == 2 && phi[MRAC_N_STRUCT] != 0.0f
       && phi[MRAC_N_STRUCT + 23] != 0.0f);
    mrac_vp12.mask[0] = mrac_vp12.mask_act[0] = 16777215.0f;
    mrac_in_acc[0] = 0.5f; mrac_in_vbat = 15.0f; mrac_vp12.v_arm = 16.0f;
    ok("M basis 6 (S10X) accepted", MRAC_VariantParamSet(MRAC_AXIS_PITCH, MRAC_VF_BASIS, 6.0f) == 1U);
    ok("M S10X lims: swing 2 % vs lag 10 %, bias = lag",
       fabsf(mrac_config_pitch.What_limit[MRAC_N_STRUCT + 4] / mrac_config_pitch.What_limit[MRAC_N_STRUCT + 7] - 0.2f) < 1e-4f
       && fabsf(mrac_config_pitch.What_limit[0] - mrac_config_pitch.What_limit[MRAC_N_STRUCT + 7]) < 1e-4f);
    tick(5.0f, -3.0f); tick(5.0f, -3.0f);
    ok("M S10X: swing/lag slots live, sag finite (0 at u_nom 0), ext 8+ zero",
       phi[MRAC_N_STRUCT + 4] != 0.0f && phi[MRAC_N_STRUCT + 7] != 0.0f && isfinite(phi[MRAC_N_STRUCT + 6]) && isfinite(phi[MRAC_N_STRUCT + 5])
       && isfinite(phi[MRAC_N_STRUCT + 7]) && phi[MRAC_N_STRUCT + 8] == 0.0f);
    mrac_in_acc[0] = 0.0f; mrac_in_vbat = 0.0f; mrac_vp12.v_arm = 0.0f;
    MRAC_VariantParamSet(MRAC_AXIS_PITCH, MRAC_VF_BASIS, 1.0f);
    ok("M leaving S10X restores the ext 4 row", fabsf(mrac_config_pitch.What_limit[MRAC_N_STRUCT + 4] - 0.05f) < 1e-6f);
    ok("M basis 9 (S10X+RBF16) accepted", MRAC_VariantParamSet(MRAC_AXIS_PITCH, MRAC_VF_BASIS, 9.0f) == 1U);
    ok("M S10X+RBF16 rows: ext 4 gamma 0.094 lim 2 %, ext 0 gamma 0.025, Gaussian ext 8 gamma 0.10",
       fabsf(mrac_config_pitch.gamma[MRAC_N_STRUCT + 4] - 0.094f) < 1e-6f
       && fabsf(mrac_config_pitch.What_limit[MRAC_N_STRUCT + 4] - 0.02f * mrac_config_pitch.u_max) < 1e-6f
       && fabsf(mrac_config_pitch.gamma[MRAC_N_STRUCT] - 0.025f) < 1e-6f
       && fabsf(mrac_config_pitch.gamma[MRAC_N_STRUCT + 8] - 0.10f) < 1e-6f);
    MRAC_VariantParamSet(MRAC_AXIS_PITCH, MRAC_VF_BASIS, 7.0f);
    ok("M leaving S10X+RBF16 restores the ext 0 and 4 rows",
       fabsf(mrac_config_pitch.gamma[MRAC_N_STRUCT] - 0.10f) < 1e-6f
       && fabsf(mrac_config_pitch.gamma[MRAC_N_STRUCT + 4] - 0.10f) < 1e-6f
       && fabsf(mrac_config_pitch.What_limit[MRAC_N_STRUCT + 4] - 0.05f) < 1e-6f);
    ok("M basis 10 refused", MRAC_VariantParamSet(MRAC_AXIS_PITCH, MRAC_VF_BASIS, 10.0f) == 0U);
    MRAC_VariantParamSet(MRAC_AXIS_PITCH, MRAC_VF_BASIS, 0.0f);
    tick(5.0f, -3.0f);
    ok("M back to basis 0: ext slots 0 again", ext_nonzero(phi) == 0);
}
#endif

int main(void)
{
    test_simplex_envelope_in_rad();
    test_nan_guard();
#if MRAC_VARIANT == MRAC_VARIANT_STRUCT6_RBF12
    test_rbf_angle_in_rad();
#endif
#if MRAC_VARIANT == MRAC_VARIANT_MULTI
    test_multi_basis();
    test_retuned_rbf();
#endif
    printf("mrac_inputs (variant %d): %d checks, %d failure(s)\n", MRAC_VARIANT, checks, fails);
    return fails != 0;
}
