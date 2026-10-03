/* Host driver for the WP-34 MRAC log replay (built by mrac_log_replay.py with gcc and API/mrac*.c).
 *
 *   mrac_log_replay_host <in.f32> <out.f32> [axis:field:value ...] [inj:<0|1>] [simplex:<mode>]
 *
 * Open loop: the logged states and commands drive MRAC_Control tick by tick; u_ad goes nowhere, so the
 * plant in the log never responds to it. Each axis:field:value goes through MRAC_VariantParamSet (CMD 0x1D,
 * printed "set <ok>"). inj:1 runs the law as if output_injection_on were 1 from power-on (learn gate and
 * Theta ramp as injected, no rising-edge reset); simplex:2 counts would-be trips without acting on them.
 *
 * in.f32: N_IN float32 per 5 ms tick (layout IN_* below). imu_data.pit / .rol are passed as given: the
 * firmware feeds degrees (API/imu_update.c:196-197).
 * out.f32: N_OUT float32 per tick: u_ad[4] u_nom[4] |Theta|[4] e[4] learn_gate simplex_reason
 * would_trip_count rbf_phi_pitch[12] rbf_phi_roll[12] (zeros in the STRUCT6 build). Axis order pitch roll yaw z. */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "mrac.h"

extern uint8_t mrac_var_id[AXES];
uint8_t MRAC_VariantParamSet(uint8_t axis, uint8_t field, float val);

_imu_st imu_data = {0.0f, 0.0f};

enum { IN_ARMED, IN_PHASE, IN_GY_DES, IN_GY_FB, IN_GY_U, IN_GX_DES, IN_GX_FB, IN_GX_U, IN_GZ_DES, IN_GZ_FB,
       IN_GZ_U, IN_Z_DES, IN_Z_FB, IN_Z_U, IN_PIT, IN_ROL, IN_UDEF_P, IN_UDEF_R, IN_UDEF_Y, IN_UDEF_Z, N_IN };
#define N_RBF 12
#define N_OUT (19 + 2 * N_RBF)

int main(int argc, char **argv)
{
    MRAC_AxisState_t *st[4];
    FILE *fin, *fout;
    float in[N_IN], out[N_OUT];
    int a, i, inj = 0;

    if (argc < 3) return 2;
    MRAC_Init();
    for (i = 3; i < argc; i++) {
        const char *s = argv[i];
        char *end;
        long x, y;
        if (strncmp(s, "inj:", 4) == 0) { inj = (int)strtol(s + 4, NULL, 10); continue; }
        if (strncmp(s, "simplex:", 8) == 0) { mrac_simplex.mode = (uint8_t)strtol(s + 8, NULL, 10); continue; }
        x = strtol(s, &end, 10);
        y = strtol(end + 1, &end, 10);
        printf("set %d\n", (int)MRAC_VariantParamSet((uint8_t)x, (uint8_t)y, strtof(end + 1, NULL)));
    }
    mrac_flags.output_injection_on = (uint8_t)inj;
    mrac_inj.prev_injection_on = (uint8_t)inj;

    st[0] = &mrac_state.pitch; st[1] = &mrac_state.roll; st[2] = &mrac_state.yaw; st[3] = &mrac_state.z_rate;
    fin = fopen(argv[1], "rb");
    fout = fopen(argv[2], "wb");
    if (!fin || !fout) return 3;

    while (fread(in, sizeof(float), N_IN, fin) == N_IN) {
        CtrlerTypeDef c;
        float w2;
        memset(&c, 0, sizeof(c));
        c.gyroyPID.Des = in[IN_GY_DES]; c.gyroyPID.FB = in[IN_GY_FB]; c.gyroyPID.U = in[IN_GY_U];
        c.gyroxPID.Des = in[IN_GX_DES]; c.gyroxPID.FB = in[IN_GX_FB]; c.gyroxPID.U = in[IN_GX_U];
        c.gyrozPID.Des = in[IN_GZ_DES]; c.gyrozPID.FB = in[IN_GZ_FB]; c.gyrozPID.U = in[IN_GZ_U];
        c.Z_ratePID.Des = in[IN_Z_DES]; c.Z_ratePID.FB = in[IN_Z_FB]; c.Z_ratePID.U = in[IN_Z_U];
        imu_data.pit = in[IN_PIT];
        imu_data.rol = in[IN_ROL];
        for (a = 0; a < 4; a++) st[a]->u_def = in[IN_UDEF_P + a];
        mrac_in_armed = (uint8_t)(in[IN_ARMED] > 0.5f);
        mrac_in_phase = (uint8_t)lrintf(in[IN_PHASE]);

        MRAC_Control(&c);

        for (a = 0; a < 4; a++) {
            out[a] = st[a]->u_ad;
            out[4 + a] = st[a]->u_nom;
            w2 = 0.0f;
            for (i = 0; i < MRAC_N_FEATURES; i++) w2 += st[a]->Theta[i] * st[a]->Theta[i];
            out[8 + a] = sqrtf(w2);
            out[12 + a] = st[a]->e;
        }
        out[16] = (float)mrac_inj.learn_gate;
        out[17] = (float)mrac_simplex.reason;
        out[18] = (float)mrac_simplex.would_trip_count;
        for (i = 0; i < N_RBF; i++) {
#if MRAC_N_FEATURES > 6
            out[19 + i] = st[0]->Phi[6 + i];
            out[19 + N_RBF + i] = st[1]->Phi[6 + i];
#else
            out[19 + i] = 0.0f;
            out[19 + N_RBF + i] = 0.0f;
#endif
        }
        fwrite(out, sizeof(float), N_OUT, fout);
    }
    fclose(fin);
    fclose(fout);
    return 0;
}
