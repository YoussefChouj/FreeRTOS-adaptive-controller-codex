/* API/tests/test_mrac_inputs.c - host test of the inputs MRAC_Control reads (WP-38).
 *
 * A. Units. imu_data.pit/rol are degrees (API/imu_update.c:196-197). MRAC converts them with MRAC_DEG2RAD where
 *    it reads them: the simplex envelope (roll_max/pitch_max, rad) and, in the V3 build, the RBF angle input
 *    (rad / rbf_ang_scale). Before WP-38 the degrees were used as rad: a 3.2 deg tilt passed the 3.14 "rad" limit.
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

int main(void)
{
    test_simplex_envelope_in_rad();
#if MRAC_VARIANT == MRAC_VARIANT_STRUCT6_RBF12
    test_rbf_angle_in_rad();
#endif
    printf("mrac_inputs (variant %d): %d checks, %d failure(s)\n", MRAC_VARIANT, checks, fails);
    return fails != 0;
}
