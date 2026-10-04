/* Differential trace harness for API/mrac.c (WP-37). Built and compared by tools/fw_trace.py (target mrac).
 *
 * API/tests/run_mrac_equiv.py proves the default law bit-exact; every WP-27/33 variant is compiled in but
 * inert at its default configuration, so EQUIV does not reach it. This harness does: the file under test is
 * #included (MRAC_SRC) next to the tree's mrac_math.c, and each tick it randomizes the PID loops MRAC_Control
 * reads, the feature flags, the gate inputs, the simplex knobs and, through MRAC_VariantParamSet, the variant
 * fields of every axis (values drawn inside and outside their bounds, non-finite now and then). It then runs
 * MRAC_Control() and mixes the whole MRAC state into one FNV-1a hash.
 */
#include MRAC_SRC
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned long long g_h = 1469598103934665603ULL;
static void mix(const void *p, size_t n)
{
    const unsigned char *b = (const unsigned char *)p;
    size_t i;
    for (i = 0; i < n; i++) {
        g_h ^= b[i];
        g_h *= 1099511628211ULL;
    }
}
#define MIXV(x) mix((const void *)&(x), sizeof(x))

static unsigned g_in = 1u;
static unsigned xs(unsigned *s) { *s ^= *s << 13; *s ^= *s >> 17; *s ^= *s << 5; return *s; }
static float fr(unsigned *s, float lo, float hi) { return lo + (hi - lo) * (float)(xs(s) >> 8) / 16777216.0f; }
static int pm(unsigned *s, unsigned per_mille) { return (xs(s) % 1000u) < per_mille; }

__typeof__(imu_data) imu_data;

static void flip(uint8_t *f, unsigned per_mille) { if (pm(&g_in, per_mille)) *f = (uint8_t)!*f; }

/* A value for variant field `field`: mostly inside its MRAC_VAR_FIELD bounds, sometimes on a bound, 0 (off),
 * above the bounds or non-finite (MRAC_VariantParamSet must refuse those). */
static float variant_value(uint8_t field)
{
    unsigned r = xs(&g_in) % 100u;
    float lo = field < MRAC_VF_COUNT ? mrac_var_field[field].lo : 0.0f;
    float hi = field < MRAC_VF_COUNT ? mrac_var_field[field].hi : 1.0f;
    if (r < 2u) return (r & 1u) ? INFINITY : NAN;
    if (r < 8u) return hi + fr(&g_in, 0.001f, 10.0f);
    if (r < 18u) return 0.0f;
    if (r < 28u) return (r & 1u) ? lo : hi;
    return fr(&g_in, lo, hi);
}

static void script(CtrlerTypeDef *c)
{
    PIDTypeDef *loops[4];
    int i;
    loops[0] = &c->gyroxPID; loops[1] = &c->gyroyPID; loops[2] = &c->gyrozPID; loops[3] = &c->Z_ratePID;
    for (i = 0; i < 4; i++) {
        float span = (i == 3) ? 2.0f : 200.0f;
        loops[i]->FB += fr(&g_in, -0.05f, 0.05f) * span;
        loops[i]->Des = pm(&g_in, 980) ? loops[i]->Des : fr(&g_in, -span, span);
        loops[i]->U = fr(&g_in, -300.0f, 300.0f);
        if (loops[i]->FB > span || loops[i]->FB < -span) loops[i]->FB *= 0.5f;
        if (pm(&g_in, 1)) loops[i]->FB = NAN;   /* poisons this tick only; the next += keeps it NaN, so reset */
        else if (loops[i]->FB != loops[i]->FB) loops[i]->FB = 0.0f;
    }
    flip(&mrac_flags.adaptation_on, 5); flip(&mrac_flags.projection_on, 5); flip(&mrac_flags.deadzone_on, 5);
    flip(&mrac_flags.hard_freeze_on, 5); flip(&mrac_flags.tanh_saturation_on, 5); flip(&mrac_flags.e_modification_on, 5);
    flip(&mrac_flags.l1_filtering_on, 5); flip(&mrac_flags.axis_enable_pitch, 2); flip(&mrac_flags.axis_enable_roll, 2);
    flip(&mrac_flags.axis_enable_yaw, 2); flip(&mrac_flags.output_injection_on, 5);
    if (pm(&g_in, 3)) mrac_flags.ref_model_type = (uint8_t)(xs(&g_in) % 3u);
    if (pm(&g_in, 5)) mrac_in_armed = (uint8_t)!mrac_in_armed;
    if (pm(&g_in, 5)) mrac_in_phase = (uint8_t)(xs(&g_in) % 4u);
    if (pm(&g_in, 2)) { mrac_simplex.mode = (uint8_t)(xs(&g_in) % 3u); mrac_simplex.variant = (uint8_t)(xs(&g_in) & 1u); }
    if (pm(&g_in, 2)) { mrac_simplex.roll_max = fr(&g_in, 0.1f, 1.0f); mrac_simplex.pitch_max = fr(&g_in, 0.1f, 1.0f); }
    if (pm(&g_in, 2)) mrac_simplex.w_norm_max = fr(&g_in, 0.5f, 50.0f);
    if (pm(&g_in, 60)) {
        uint8_t axis = (uint8_t)(xs(&g_in) % 4u), field = (uint8_t)(xs(&g_in) % (MRAC_VF_COUNT + 1u));
        mix(&axis, 1); mix(&field, 1);
        (void)MRAC_VariantParamSet(axis, field, variant_value(field));
    }
    mrac_state.pitch.u_def = fr(&g_in, -50.0f, 50.0f);
    mrac_state.roll.u_def = fr(&g_in, -50.0f, 50.0f);
    mrac_state.yaw.u_def = fr(&g_in, -50.0f, 50.0f);
    mrac_state.z_rate.u_def = fr(&g_in, -50.0f, 50.0f);
    imu_data.rol = fr(&g_in, -0.5f, 0.5f) * 57.3f;
    imu_data.pit = fr(&g_in, -0.5f, 0.5f) * 57.3f;
    if (pm(&g_in, 1)) MRAC_ResetWeights();
}

static void mix_state(void)
{
    MIXV(mrac_state); MIXV(mrac_flags); MIXV(mrac_config_pitch); MIXV(mrac_config_roll); MIXV(mrac_config_yaw);
    MIXV(mrac_config_z); MIXV(mrac_simplex); MIXV(mrac_inj); MIXV(mrac_bus); MIXV(mrac_g_gamma); MIXV(mrac_g_sigma);
    MIXV(mrac_g_phi); MIXV(mrac_u_ff); MIXV(mrac_var_id); MIXV(mrac_ref_type_eff);
}

int main(int argc, char **argv)
{
    unsigned long ticks = argc > 1 ? strtoul(argv[1], 0, 10) : 100000ul;
    unsigned seed = argc > 2 ? (unsigned)strtoul(argv[2], 0, 10) : 1u;
    unsigned long every = argc > 3 ? strtoul(argv[3], 0, 10) : 1000ul;
    unsigned long t;
    static CtrlerTypeDef c;
    g_in = 2463534242u ^ (seed * 2654435761u);
    MRAC_Init();
    for (t = 1; t <= ticks; t++) {
        script(&c);
        MRAC_Control(&c);
        mix_state();
        if (t % every == 0u || t == ticks) printf("tick %lu hash %016llx\n", t, g_h);
    }
    return 0;
}
