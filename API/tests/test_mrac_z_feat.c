/* API/tests/test_mrac_z_feat.c - host test of the Z-axis MRAC features (mrac_z_feat_mask, API/mrac.c MRAC_ZFeat).
 *
 * The features live in Z ext 0..3 under Z basis S10X (MULTI build) and only when their mask bit is set:
 *   bit0 bias 1, bit1 ground effect (h0/max(h,h_min))^2 in [0,1], bit2 drag vz|vz|/vz_ref^2 in [-1,1],
 *   bit3 tilt loss 1/cos(tilt)-1 in [0,1] (tilt clamped to tilt_max). A NaN or out-of-range input gives 0.
 * A. Mask 0 is bit-exact: the same run with the ext 0..3 rows put back to their MRAC_Init values (= HEAD before
 *    the features, where S10X set only the ext 6 row) gives the same u_ad of every axis and the same Theta.
 * B. Each bit fills only its own slot. C. Bounds and the h_min clamp. D. NaN inputs give 0, Theta stays finite.
 * E. Pitch/roll/yaw/x/y are the same with mask 0 and mask 15. F. Basis 0 ignores the mask.
 *
 * Built by tools/host_tests.py, row mrac_z_feat (-DMRAC_VARIANT=2), on a copy of API/mrac*.[ch] so the stubs win:
 *   gcc -std=c99 -Wall -Wextra -DMRAC_VARIANT=2 -I<copy> -IAPI/tests/stubs API/tests/test_mrac_z_feat.c
 *       <copy>/mrac.c <copy>/mrac_math.c -lm
 */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "mrac.h"
#include "mrac_math.h"

_imu_st imu_data;

#define N_TICKS 600
#define EXT(k)  (MRAC_N_STRUCT + (k))

static int checks, fails;
static void ok(const char *what, int cond)
{
    checks++;
    if (!cond) {
        fails++;
        printf("FAIL  %s\n", what);
    }
}
static int near(float a, float b) { return fabsf(a - b) <= 1e-5f; }

static CtrlerTypeDef ctrl;

/* Flying, learn-gated, injected (as API/tests/test_mrac_inputs.c), Z basis set, then the mask. */
static void fly(float basis, uint8_t mask, int restore_init_rows)
{
    float g[MRAC_ZFEAT_N], lim[MRAC_ZFEAT_N], tol[MRAC_ZFEAT_N], low[MRAC_ZFEAT_N];
    int k;
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
    for (k = 0; k < MRAC_ZFEAT_N; k++) {
        g[k] = mrac_config_z.gamma[EXT(k)];
        lim[k] = mrac_config_z.What_limit[EXT(k)];
        tol[k] = mrac_config_z.What_tol[EXT(k)];
        low[k] = mrac_config_z.What_lower_limit[EXT(k)];
    }
    MRAC_VariantParamSet(MRAC_AXIS_Z, MRAC_VF_BASIS, basis);
    if (restore_init_rows) {
        for (k = 0; k < MRAC_ZFEAT_N; k++) {
            mrac_config_z.gamma[EXT(k)] = g[k];
            mrac_config_z.What_limit[EXT(k)] = lim[k];
            mrac_config_z.What_tol[EXT(k)] = tol[k];
            mrac_config_z.What_lower_limit[EXT(k)] = low[k];
        }
    }
    mrac_z_feat_mask = mask;
}

/* One tick with explicit Z inputs (h m, vz m/s, rol/pit deg). */
static void tick_z(float h, float vz, float rol, float pit)
{
    ctrl.Z_posPID.FB = h;
    ctrl.Z_ratePID.FB = vz;
    ctrl.Z_ratePID.Des = vz + 0.1f;
    ctrl.Z_ratePID.U = 120.0f;
    imu_data.rol = rol;
    imu_data.pit = pit;
    MRAC_Control(&ctrl);
}

/* A run with every axis excited; records u_ad of the six axes per tick. */
static float traj[N_TICKS][6];
static void run(void)
{
    int i;
    for (i = 0; i < N_TICKS; i++) {
        float t = (float)i;
        ctrl.gyroyPID.FB = 30.0f * sinf(0.03f * t);
        ctrl.gyroyPID.Des = 30.0f * sinf(0.03f * t + 0.4f);
        ctrl.gyroxPID.FB = -20.0f * cosf(0.04f * t);
        ctrl.gyroxPID.Des = -20.0f * cosf(0.04f * t + 0.3f);
        ctrl.gyrozPID.FB = 10.0f * sinf(0.02f * t);
        ctrl.gyrozPID.Des = 10.0f * sinf(0.02f * t + 0.5f);
        ctrl.Z_posPID.FB = 0.30f + 0.25f * sinf(0.02f * t);
        ctrl.Z_ratePID.FB = 0.6f * sinf(0.05f * t);
        ctrl.Z_ratePID.Des = 0.6f * sinf(0.05f * t + 0.3f);
        ctrl.Z_ratePID.U = 150.0f + 50.0f * sinf(0.01f * t);
        imu_data.pit = 8.0f * sinf(0.03f * t);
        imu_data.rol = 6.0f * cosf(0.025f * t);
        MRAC_Control(&ctrl);
        traj[i][0] = mrac_state.pitch.u_ad;
        traj[i][1] = mrac_state.roll.u_ad;
        traj[i][2] = mrac_state.yaw.u_ad;
        traj[i][3] = mrac_state.z_rate.u_ad;
        traj[i][4] = mrac_state.x_vel.u_ad;
        traj[i][5] = mrac_state.y_vel.u_ad;
    }
}

static float ref[N_TICKS][6];
static float ref_theta[MRAC_N_FEATURES];

static int axis_same(int a)
{
    int i;
    for (i = 0; i < N_TICKS; i++)
        if (memcmp(&traj[i][a], &ref[i][a], sizeof(float)) != 0) return 0;
    return 1;
}

static void test_mask0_bit_exact(void)
{
    int a, moved = 0, i;
    fly(6.0f, 0, 1);
    run();
    memcpy(ref, traj, sizeof(traj));
    memcpy(ref_theta, (const void *)mrac_state.z_rate.Theta, sizeof(ref_theta));
    fly(6.0f, 0, 0);
    run();
    for (a = 0; a < 6; a++) ok("A mask 0 S10X: u_ad bit-exact vs Init rows", axis_same(a));
    ok("A mask 0 S10X: Z Theta bit-exact", memcmp(ref_theta, (const void *)mrac_state.z_rate.Theta, sizeof(ref_theta)) == 0);
    ok("A mask 0: Z ext 0..3 Phi and Theta stay 0",
       mrac_state.z_rate.Phi[EXT(0)] == 0.0f && mrac_state.z_rate.Phi[EXT(3)] == 0.0f &&
       mrac_state.z_rate.Theta[EXT(0)] == 0.0f && mrac_state.z_rate.Theta[EXT(1)] == 0.0f &&
       mrac_state.z_rate.Theta[EXT(2)] == 0.0f && mrac_state.z_rate.Theta[EXT(3)] == 0.0f);
    for (i = 0; i < N_TICKS; i++) if (traj[i][3] != 0.0f) moved = 1;
    ok("A the Z axis adapts in this run (u_ad not all 0)", moved);
    printf("A  mask 0 S10X: z u_ad[%d] = %.9g (Init rows %.9g), sag Theta = %.9g\n", N_TICKS - 1,
           (double)traj[N_TICKS - 1][3], (double)ref[N_TICKS - 1][3], (double)mrac_state.z_rate.Theta[EXT(6)]);
}

static void test_each_bit_own_slot(void)
{
    static const float expect[MRAC_ZFEAT_N] = { 1.0f, 0.25f, 0.09f, 0.0f };
    float base[MRAC_N_EXT];
    float ct;
    int b, k, other_same;
    fly(6.0f, 0, 0);
    tick_z(0.2f, 0.3f, 10.0f, 5.0f);
    memcpy(base, (const void *)&mrac_state.z_rate.Phi[MRAC_N_STRUCT], sizeof(base));
    ct = cosf(10.0f * 0.0174533f) * cosf(5.0f * 0.0174533f);
    for (b = 0; b < MRAC_ZFEAT_N; b++) {
        float want = (b == 3) ? 1.0f / ct - 1.0f : expect[b];
        const volatile float *phi = &mrac_state.z_rate.Phi[MRAC_N_STRUCT];
        fly(6.0f, (uint8_t)(1U << b), 0);
        tick_z(0.2f, 0.3f, 10.0f, 5.0f);
        other_same = 1;
        for (k = 0; k < MRAC_N_EXT; k++)
            if (k != b && memcmp((const void *)&phi[k], &base[k], sizeof(float)) != 0) other_same = 0;
        ok("B bit fills its own slot with the right value", near(phi[b], want));
        ok("B bit leaves every other ext slot as mask 0", other_same);
        printf("B  bit %d: ext %d = %.6f (want %.6f)\n", b, b, (double)phi[b], (double)want);
    }
}

static float feat(uint8_t bit, float h, float vz, float rol, float pit)
{
    fly(6.0f, bit, 0);
    tick_z(h, vz, rol, pit);
    return mrac_state.z_rate.Phi[EXT(bit == MRAC_ZFEAT_GROUND ? 1 : bit == MRAC_ZFEAT_DRAG ? 2 : bit == MRAC_ZFEAT_TILT ? 3 : 0)];
}

static void test_bounds(void)
{
    float lo_t;
    ok("C ground h=0.01 clamps to h_min -> 1", feat(MRAC_ZFEAT_GROUND, 0.01f, 0.0f, 0, 0) == 1.0f);
    ok("C ground h=0 -> 1", feat(MRAC_ZFEAT_GROUND, 0.0f, 0.0f, 0, 0) == 1.0f);
    ok("C ground h=0.1 (= h0) -> 1", near(feat(MRAC_ZFEAT_GROUND, 0.1f, 0.0f, 0, 0), 1.0f));
    ok("C ground h=1 -> 0.01", near(feat(MRAC_ZFEAT_GROUND, 1.0f, 0.0f, 0, 0), 0.01f));
    ok("C ground h=5 (> h_max) -> 0", feat(MRAC_ZFEAT_GROUND, 5.0f, 0.0f, 0, 0) == 0.0f);
    ok("C ground h<0 -> 0", feat(MRAC_ZFEAT_GROUND, -0.1f, 0.0f, 0, 0) == 0.0f);
    ok("C drag vz=2 -> +1", feat(MRAC_ZFEAT_DRAG, 0.3f, 2.0f, 0, 0) == 1.0f);
    ok("C drag vz=-2 -> -1", feat(MRAC_ZFEAT_DRAG, 0.3f, -2.0f, 0, 0) == -1.0f);
    ok("C drag vz=-0.5 -> -0.25", near(feat(MRAC_ZFEAT_DRAG, 0.3f, -0.5f, 0, 0), -0.25f));
    ok("C drag vz=6 (> vz_max) -> 0", feat(MRAC_ZFEAT_DRAG, 0.3f, 6.0f, 0, 0) == 0.0f);
    lo_t = 1.0f / cosf(30.0f * 0.0174533f) - 1.0f;
    ok("C tilt level -> 0", feat(MRAC_ZFEAT_TILT, 0.3f, 0.0f, 0.0f, 0.0f) == 0.0f);
    ok("C tilt rol 60 clamps to tilt_max 30", near(feat(MRAC_ZFEAT_TILT, 0.3f, 0.0f, 60.0f, 0.0f), lo_t));
    ok("C tilt rol 25 pit 25 clamps to tilt_max", near(feat(MRAC_ZFEAT_TILT, 0.3f, 0.0f, 25.0f, 25.0f), lo_t));
    ok("C tilt rol 200 (out of range) -> 0", feat(MRAC_ZFEAT_TILT, 0.3f, 0.0f, 200.0f, 0.0f) == 0.0f);
    printf("C  tilt at tilt_max = %.6f, ground h=1 = %.6f\n", (double)lo_t,
           (double)feat(MRAC_ZFEAT_GROUND, 1.0f, 0.0f, 0, 0));
}

static int z_finite(void)
{
    int k;
    for (k = 0; k < MRAC_N_FEATURES; k++)
        if (!(mrac_state.z_rate.Theta[k] - mrac_state.z_rate.Theta[k] == 0.0f)) return 0;
    return mrac_state.z_rate.u_ad - mrac_state.z_rate.u_ad == 0.0f;
}

static void test_nan(void)
{
    int i;
    float out_f[MRAC_ZFEAT_N], out_w[MRAC_ZFEAT_N];
    ok("D ground h=NaN -> 0", feat(MRAC_ZFEAT_GROUND, NAN, 0.2f, 0, 0) == 0.0f && z_finite());
    ok("D ground h=inf -> 0", feat(MRAC_ZFEAT_GROUND, INFINITY, 0.2f, 0, 0) == 0.0f && z_finite());
    ok("D tilt rol=NaN -> 0", feat(MRAC_ZFEAT_TILT, 0.3f, 0.2f, NAN, 0) == 0.0f && z_finite());
    ok("D tilt pit=NaN -> 0", feat(MRAC_ZFEAT_TILT, 0.3f, 0.2f, 0, NAN) == 0.0f && z_finite());
    fly(6.0f, 0x0F, 0);
    for (i = 0; i < 50; i++) tick_z(0.3f, 0.2f, 3.0f, 2.0f);
    tick_z(0.3f, NAN, 3.0f, 2.0f);          /* vz NaN: the input guard skips the Z update */
    ok("D vz=NaN: Z Theta and u_ad stay finite", z_finite());
    for (i = 0; i < 50; i++) tick_z(NAN, 0.2f, NAN, NAN);
    ok("D h, rol, pit NaN for 50 ticks: finite, ground/tilt slots 0",
       z_finite() && mrac_state.z_rate.Phi[EXT(1)] == 0.0f && mrac_state.z_rate.Phi[EXT(3)] == 0.0f);
    MRAC_ZFeatOut(out_f, out_w);
    ok("D MRAC_ZFeatOut gives Phi/Theta of ext 0..3",
       out_f[0] == mrac_state.z_rate.Phi[EXT(0)] && out_f[2] == mrac_state.z_rate.Phi[EXT(2)] &&
       out_w[0] == mrac_state.z_rate.Theta[EXT(0)] && out_w[3] == mrac_state.z_rate.Theta[EXT(3)]);
}

static void test_other_axes_and_basis0(void)
{
    int a, z_moved = 0, i;
    float w[MRAC_ZFEAT_N], f[MRAC_ZFEAT_N];
    fly(6.0f, 0, 0);
    run();
    memcpy(ref, traj, sizeof(traj));
    fly(6.0f, 0x0F, 0);
    run();
    for (a = 0; a < 6; a++) if (a != 3) ok("E mask 15: other axis u_ad bit-exact vs mask 0", axis_same(a));
    for (i = 0; i < N_TICKS; i++) if (memcmp(&traj[i][3], &ref[i][3], sizeof(float)) != 0) z_moved = 1;
    MRAC_ZFeatOut(f, w);
    ok("E mask 15: Z differs and the feature weights learn", z_moved && w[0] != 0.0f && w[2] != 0.0f);
    printf("E  mask 15: w = %.6g %.6g %.6g %.6g\n", (double)w[0], (double)w[1], (double)w[2], (double)w[3]);
    fly(0.0f, 0, 0);
    run();
    memcpy(ref, traj, sizeof(traj));
    fly(0.0f, 0x0F, 0);
    run();
    for (a = 0; a < 6; a++) ok("F basis 0: mask 15 bit-exact vs mask 0", axis_same(a));
    ok("F basis 0: Z ext 0..3 Phi stay 0", mrac_state.z_rate.Phi[EXT(0)] == 0.0f && mrac_state.z_rate.Phi[EXT(1)] == 0.0f);
}

int main(void)
{
#if MRAC_VARIANT == MRAC_VARIANT_MULTI
    test_mask0_bit_exact();
    test_each_bit_own_slot();
    test_bounds();
    test_nan();
    test_other_axes_and_basis0();
#else
    printf("Z features are MULTI only (MRAC_VARIANT=2)\n");
#endif
    printf("%d checks, %d failure(s)\n", checks, fails);
    return fails ? 1 : 0;
}
