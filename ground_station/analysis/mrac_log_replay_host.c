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
 * would_trip_count rbf_phi_pitch[12] rbf_phi_roll[12] (zeros in the STRUCT6 build). Axis order pitch roll yaw z.
 *
 *   mrac_log_replay_host - - [args] [cfg:<axis>:<field>:<value> ...]   (closed loop, see main) */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <stddef.h>
#include <io.h>
#include <fcntl.h>

#include "mrac.h"

uint8_t MRAC_VariantParamSet(uint8_t axis, uint8_t field, float val);

_imu_st imu_data = {0.0f, 0.0f};

enum { IN_ARMED, IN_PHASE, IN_GY_DES, IN_GY_FB, IN_GY_U, IN_GX_DES, IN_GX_FB, IN_GX_U, IN_GZ_DES, IN_GZ_FB,
       IN_GZ_U, IN_Z_DES, IN_Z_FB, IN_Z_U, IN_PIT, IN_ROL, IN_UDEF_P, IN_UDEF_R, IN_UDEF_Y, IN_UDEF_Z, N_IN };
#define N_RBF 12
#define N_OUT (19 + 2 * N_RBF)

static MRAC_AxisState_t *const st[4] = {&mrac_state.pitch, &mrac_state.roll, &mrac_state.yaw, &mrac_state.z_rate};

/* One driver argument: inj:<0|1>, simplex:<mode> or axis:field:value (MRAC_VariantParamSet). Returns its ok. */
static int apply_arg(const char *s)
{
    char *end;
    long x, y;
    if (strncmp(s, "inj:", 4) == 0) {
        mrac_flags.output_injection_on = (uint8_t)strtol(s + 4, NULL, 10);
        mrac_inj.prev_injection_on = mrac_flags.output_injection_on;
        return 1;
    }
    if (strncmp(s, "simplex:", 8) == 0) { mrac_simplex.mode = (uint8_t)strtol(s + 8, NULL, 10); return 1; }
    x = strtol(s, &end, 10);
    y = strtol(end + 1, &end, 10);
    return (int)MRAC_VariantParamSet((uint8_t)x, (uint8_t)y, strtof(end + 1, NULL));
}

/* One 5 ms tick: in[N_IN] -> MRAC_Control -> out[N_OUT]. */
static void step(const float *in, float *out)
{
    CtrlerTypeDef c;
    float w2;
    int a, i;
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
}

/* Closed-loop use (sim/bench/fw_mrac.py): the mixer add-on exactly as API/controller.c mrac_correction() forms it,
 * and per-axis config writes the CMD 0x1D table does not reach (cfg:<axis>:<name>:<value>, "gamma" scales all). */
static MRAC_AxisConfig_t *const cfg[4] = {&mrac_config_pitch, &mrac_config_roll, &mrac_config_yaw, &mrac_config_z};
#define CFG_FIELD(n) {#n, offsetof(MRAC_AxisConfig_t, n)}
static const struct { const char *name; size_t off; } cfg_fields[] = {
    CFG_FIELD(omega_u), CFG_FIELD(sigma), CFG_FIELD(sigma_lf), CFG_FIELD(gam_f), CFG_FIELD(e_sat), CFG_FIELD(k_e),
    CFG_FIELD(u_max), CFG_FIELD(ref_model_bw), CFG_FIELD(e_deadzone)};

static float mix(int axis)
{
    if (!mrac_flags.output_injection_on) return 0.0f;
    return st[axis]->u_ad * cfg[axis]->mrac_to_mixer * mrac_simplex.fade * mrac_inj.inj_alpha;
}

static int apply_cfg(const char *s)
{
    char name[32];
    int axis;
    float val;
    size_t i;
    if (sscanf(s, "cfg:%d:%31[^:]:%f", &axis, name, &val) != 3 || axis < 0 || axis > 3) return 0;
    if (strcmp(name, "gamma") == 0) {
        for (i = 0; i < MAX_NUM_BASIS; i++) cfg[axis]->gamma[i] *= val;
        return 1;
    }
    for (i = 0; i < sizeof(cfg_fields) / sizeof(cfg_fields[0]); i++) {
        if (strcmp(name, cfg_fields[i].name) == 0) {
            *(float *)((char *)cfg[axis] + cfg_fields[i].off) = val;
            return 1;
        }
    }
    return 0;
}

/* in "-" out "-": stream mode for a closed loop, one tick per N_IN floats on stdin, N_OUT + 4 mixer add-ons
 * (pitch roll yaw z, mixer units) flushed to stdout per tick. */
int main(int argc, char **argv)
{
    FILE *fin, *fout;
    float in[N_IN], out[N_OUT + 4];
    int i, inj = 0, stream;

    if (argc < 2) return 2;
    stream = strcmp(argv[1], "-") == 0;    /* stream: "-" then args; file: in out then args */
    if (!stream && argc < 3) return 2;
    MRAC_Init();
    for (i = stream ? 2 : 3; i < argc; i++) {
        if (strncmp(argv[i], "inj:", 4) == 0) { inj = (int)strtol(argv[i] + 4, NULL, 10); continue; }
        if (strncmp(argv[i], "simplex:", 8) == 0) { apply_arg(argv[i]); continue; }
        if (strncmp(argv[i], "cfg:", 4) == 0) { fprintf(stderr, "set %d\n", apply_cfg(argv[i])); continue; }
        fprintf(stream ? stderr : stdout, "set %d\n", apply_arg(argv[i]));
    }
    mrac_flags.output_injection_on = (uint8_t)inj;
    mrac_inj.prev_injection_on = (uint8_t)inj;

    if (stream) {
        _setmode(_fileno(stdin), _O_BINARY);
        _setmode(_fileno(stdout), _O_BINARY);
        fin = stdin;
        fout = stdout;
    } else {
        fin = fopen(argv[1], "rb");
        if (!fin) return 3;
        fout = fopen(argv[2], "wb");
        if (!fout) {
            fclose(fin);
            return 3;
        }
    }
    while (fread(in, sizeof(float), N_IN, fin) == N_IN) {
        step(in, out);
        if (stream) {
            for (i = 0; i < 4; i++) out[N_OUT + i] = mix(i);
            fwrite(out, sizeof(float), N_OUT + 4, fout);
            fflush(fout);
        } else {
            fwrite(out, sizeof(float), N_OUT, fout);
        }
    }
    if (!stream) {
        fclose(fin);
        fclose(fout);
    }
    return 0;
}
