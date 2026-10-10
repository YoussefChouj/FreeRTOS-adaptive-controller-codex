/* Host driver for the x/y MRAC log replay (built by mrac_xy_replay.py with gcc and API/mrac*.c).
 *
 *   mrac_xy_replay_host <in.f32> <out.f32> [ref_bw]
 *
 * Open loop, x/y only: the logged velocity loop (locx/ysPID Des, FB, U in cm/s and cm/s^2) drives MRAC_Control
 * tick by tick with every other input 0; u_ad goes nowhere. Learning and injection are on for x/y from power-on
 * (axis_enable_x/y 1, output_injection_on 1, simplex observe-only), as a flight with both gates opened would run.
 * ref_bw (rad/s), if given, switches x/y to a first-order reference model (V1 ref_type 1) at that bandwidth;
 * otherwise they follow the global type (boot default 0 = passthrough, xm = r).
 * in.f32: N_IN float32 per 5 ms tick: armed phase xdes xfb xu ydes yfb yu.
 * out.f32: N_OUT float32 per tick: u_ad[2] e[2] |Theta|[2] xm[2] learn_gate (axis order x y, SI units). */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "mrac.h"

_imu_st imu_data = {0.0f, 0.0f};

enum { IN_ARMED, IN_PHASE, IN_XDES, IN_XFB, IN_XU, IN_YDES, IN_YFB, IN_YU, N_IN };
#define N_OUT 9

int main(int argc, char **argv)
{
    MRAC_AxisState_t *const st[2] = {&mrac_state.x_vel, &mrac_state.y_vel};
    float in[N_IN], out[N_OUT], w2;
    CtrlerTypeDef c;
    FILE *fin, *fout;
    int a, i;

    if (argc < 3) { fprintf(stderr, "usage: %s in.f32 out.f32\n", argv[0]); return 2; }
    MRAC_Init();
    mrac_flags.axis_enable_x = 1;
    mrac_flags.axis_enable_y = 1;
    mrac_flags.output_injection_on = 1;
    mrac_inj.prev_injection_on = 1;
    mrac_simplex.mode = 2;
    if (argc > 3) {
        mrac_config_x.ref_type = mrac_config_y.ref_type = 1.0f;
        mrac_config_x.ref_model_bw = mrac_config_y.ref_model_bw = (float)atof(argv[3]);
    }
    fin = fopen(argv[1], "rb");
    fout = fopen(argv[2], "wb");
    if (!fin || !fout) { fprintf(stderr, "cannot open files\n"); return 2; }
    while (fread(in, sizeof(float), N_IN, fin) == N_IN) {
        memset(&c, 0, sizeof(c));
        c.locxsPID.Des = in[IN_XDES]; c.locxsPID.FB = in[IN_XFB];
        c.locysPID.Des = in[IN_YDES]; c.locysPID.FB = in[IN_YFB];
        mrac_xy_unom[0] = in[IN_XU];
        mrac_xy_unom[1] = in[IN_YU];
        mrac_in_armed = (uint8_t)(in[IN_ARMED] > 0.5f);
        mrac_in_phase = (uint8_t)lrintf(in[IN_PHASE]);
        MRAC_Control(&c);
        for (a = 0; a < 2; a++) {
            out[a] = st[a]->u_ad;
            out[2 + a] = st[a]->e;
            w2 = 0.0f;
            for (i = 0; i < MRAC_N_FEATURES; i++) w2 += st[a]->Theta[i] * st[a]->Theta[i];
            out[4 + a] = sqrtf(w2);
            out[6 + a] = st[a]->xm;
        }
        out[8] = (float)mrac_inj.learn_gate;
        fwrite(out, sizeof(float), N_OUT, fout);
    }
    fclose(fin);
    fclose(fout);
    return 0;
}
