/* Host driver for the WP-27 MRAC variants (built by test_mrac_variants_host.py with gcc).
 *
 *   mrac_variants_host <steps> [axis:field:value ...] [nan_rate:<from>:<to>] [nan_ang:<from>:<to>] [udef:<value>]
 *
 * Writes each axis:field:value through MRAC_VariantParamSet (prints "set <ok>"), then runs <steps> ticks of
 * a synthetic rate-tracking signal with the learn gate open and injection on (as API/tests/test_mrac_equiv.c).
 * Per tick it prints "t <u_ad x4> <pitch Theta[0..5]>" as float hex; at the end "max <ax> <|u_ad| max>",
 * "umax <ax> <u_max>", "vid <ax> <id>", "nonfinite <count>". */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "mrac.h"

_imu_st imu_data = {0.0f, 0.0f};

static uint32_t g_lcg = 123456789U;
static float lcg(void) { g_lcg = g_lcg * 1664525U + 1013904223U; return ((float)(int32_t)g_lcg) / 2147483648.0f; }

static void hex(float v) { uint32_t u; memcpy(&u, &v, sizeof(u)); printf(" %08x", u); }

int main(int argc, char **argv)
{
    MRAC_AxisState_t *st[4];
    MRAC_AxisConfig_t *cf[4];
    float maxabs[4] = {0.0f, 0.0f, 0.0f, 0.0f};
    int steps, step, a, i, nonfinite = 0;
    int nan_rate_from = -1, nan_rate_to = -1, nan_ang_from = -1, nan_ang_to = -1;
    float udef = 0.0f;

    if (argc < 2) return 2;
    steps = atoi(argv[1]);
    MRAC_Init();
    mrac_flags.output_injection_on = 1;
    mrac_in_armed = 1;
    mrac_in_phase = 1;
    mrac_inj.fly_ticks = 65535;
    mrac_inj.prev_armed = 1;
    mrac_inj.prev_injection_on = 1;
    mrac_inj.ramp_p = 1.0f;
    mrac_inj.inj_alpha = 1.0f;
    mrac_inj.learn_gate = 1;

    for (i = 2; i < argc; i++) {
        int x, y;
        float v;
        char s[32];
        if (sscanf(argv[i], "nan_rate:%d:%d", &x, &y) == 2) { nan_rate_from = x; nan_rate_to = y; continue; }
        if (sscanf(argv[i], "nan_ang:%d:%d", &x, &y) == 2) { nan_ang_from = x; nan_ang_to = y; continue; }
        if (sscanf(argv[i], "udef:%f", &v) == 1) { udef = v; continue; }
        if (sscanf(argv[i], "%d:%d:%31s", &x, &y, s) == 3) {
            v = strstr(s, "nan") ? NAN : strtof(s, NULL);
            printf("set %d\n", (int)MRAC_VariantParamSet((uint8_t)x, (uint8_t)y, v));
        }
    }

    st[0] = &mrac_state.pitch; st[1] = &mrac_state.roll; st[2] = &mrac_state.yaw; st[3] = &mrac_state.z_rate;
    cf[0] = &mrac_config_pitch; cf[1] = &mrac_config_roll; cf[2] = &mrac_config_yaw; cf[3] = &mrac_config_z;

    for (step = 0; step < steps; step++) {
        float t = (float)step * MRAC_DT;
        CtrlerTypeDef c;
        memset(&c, 0, sizeof(c));
        c.gyroyPID.Des = 60.0f * sinf(2.0f * 3.14159265f * 0.7f * t) + 20.0f;
        c.gyroxPID.Des = 50.0f * sinf(2.0f * 3.14159265f * 0.9f * t) - 15.0f;
        c.gyrozPID.Des = 30.0f * sinf(2.0f * 3.14159265f * 0.4f * t);
        c.Z_ratePID.Des = 0.3f * sinf(2.0f * 3.14159265f * 0.5f * t);
        c.gyroyPID.FB = c.gyroyPID.Des - 15.0f + 5.0f * lcg();
        c.gyroxPID.FB = c.gyroxPID.Des + 12.0f + 5.0f * lcg();
        c.gyrozPID.FB = c.gyrozPID.Des - 10.0f + 3.0f * lcg();
        c.Z_ratePID.FB = c.Z_ratePID.Des - 0.1f + 0.02f * lcg();
        if ((step % 800) >= 760) {           /* saturating burst, as in test_mrac_equiv.c */
            c.gyroyPID.FB = 900.0f; c.gyroxPID.FB = -900.0f; c.gyrozPID.FB = 600.0f;
        }
        if (step >= nan_rate_from && step < nan_rate_to) c.gyroyPID.FB = NAN;
        c.gyroyPID.U = 400.0f * sinf(2.0f * 3.14159265f * 1.1f * t) + 20.0f * lcg();
        c.gyroxPID.U = 400.0f * sinf(2.0f * 3.14159265f * 1.3f * t) + 20.0f * lcg();
        c.gyrozPID.U = 300.0f * sinf(2.0f * 3.14159265f * 0.8f * t) + 20.0f * lcg();
        c.Z_ratePID.U = 200.0f * sinf(2.0f * 3.14159265f * 0.6f * t) + 10.0f * lcg();
        imu_data.pit = 0.2f * sinf(2.0f * 3.14159265f * 0.3f * t);
        imu_data.rol = -0.15f * sinf(2.0f * 3.14159265f * 0.25f * t);
        if (step >= nan_ang_from && step < nan_ang_to) imu_data.pit = NAN;
        for (a = 0; a < 4; a++) st[a]->u_def = udef;

        MRAC_Control(&c);

        printf("t");
        for (a = 0; a < 4; a++) {
            float u = st[a]->u_ad;
            hex(u);
            if (!(u - u == 0.0f)) nonfinite++;
            else if (fabsf(u) > maxabs[a]) maxabs[a] = fabsf(u);
        }
        for (i = 0; i < 6; i++) hex(st[0]->Theta[i]);
        printf("\n");
    }
    for (a = 0; a < 4; a++) {
        printf("max %d %.9g\n", a, (double)maxabs[a]);
        printf("umax %d %.9g\n", a, (double)cf[a]->u_max);
        printf("vid %d %d\n", a, (int)mrac_var_id[a]);
    }
    printf("nonfinite %d\n", nonfinite);
    return 0;
}
