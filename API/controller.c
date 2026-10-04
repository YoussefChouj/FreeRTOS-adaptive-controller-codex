/**
 * @module     controller.c
 * @subsystem  control
 * @owner      Stabilizer_Task, 200 Hz: Controller_CheckSwitch then Controller_Update per axis
 *             (TASK/StabilizerTask.c Compute_Motor and Mix_Compute); Controller_Init once at boot.
 * @purpose    Runtime-selectable correction on top of the PID nominal output (controller.h), and the
 *             quad-X mixer table that maps throttle and the three axis commands to the four motors.
 * @inputs     u_nom per axis (Ctrler.*.U), mrac_state/mrac_config_* (API/mrac.c), g_ctrl_* (GS), and for the
 *             V2 saturation deficit the previous tick's mixer inputs (Throttle_out, u_gyrox/y/z, g_yaw_mix_dir).
 * @outputs    the corrected axis commands; mrac_state.*.u_def (V2 builds only).
 */
#include "controller.h"
#include "mrac.h"

volatile uint8_t g_ctrl_select = CTRL_MRAC;     /* == pre-interface behaviour: PID + gated MRAC injection */
volatile uint8_t g_ctrl_select_req = CTRL_MRAC;
volatile uint8_t g_ctrl_axis_mask = CTRL_AXIS_MASK_ALL;

/* Quad-X mixer, one row per motor: motor = thr*thr_in + pitch*u_pitch + roll*u_roll + yaw*u_yaw, with
 * u_pitch = u_gyroy (already -pitch U), u_roll = u_gyrox, u_yaw = g_yaw_mix_dir*u_gyroz (StabilizerTask.c
 * Mix_Compute). Every cell is +-1, so each product is exact and the sum rounds exactly as the written-out
 * expressions it replaced (WP-37). Keep the signs in sync with the physical motor map and BSP/pwm.h.
 *   @thr    -  [1, 1]    throttle (collective)
 *   @pitch  -  [-1, 1]   sign of u_gyroy
 *   @roll   -  [-1, 1]   sign of u_gyrox
 *   @yaw    -  [-1, 1]   sign of g_yaw_mix_dir*u_gyroz; prop directions measured on the bench 2026-09-27 */
#define MIX_ROW(thr, pitch, roll, yaw) { thr, pitch, roll, yaw }

const float g_mix[MIX_MOTORS][MIX_INPUTS] = {
/*           thr    pitch   roll    yaw        motor  prop */
    MIX_ROW( 1.0f,  -1.0f,  -1.0f,  -1.0f ),  /* M1     CW   */
    MIX_ROW( 1.0f,  +1.0f,  +1.0f,  -1.0f ),  /* M2     CW   */
    MIX_ROW( 1.0f,  -1.0f,  +1.0f,  +1.0f ),  /* M3     CCW  */
    MIX_ROW( 1.0f,  +1.0f,  -1.0f,  +1.0f )   /* M4     CCW  */
};

/* Change history (newest first)
 * yaw
 *   2026-09-27 flight1/3 with the old signs (M3/M4 +u) pinned gyrozU +350 and spun CW; flight4 with the
 *              signs flipped spun faster (gz -150 -> -770 deg/s in 3 s). g_yaw_mix_dir = -1 restores the
 *              09-10 behaviour without a reflash and is the default (StabilizerTask.c).
 */

float Mix_Motor(uint8_t motor, float thr, float u_pitch, float u_roll, float u_yaw)
{
    const float *r = g_mix[motor];
    return r[MIX_THR] * thr + r[MIX_PITCH] * u_pitch + r[MIX_ROLL] * u_roll + r[MIX_YAW] * u_yaw;
}

float Mix_Column(const float v[MIX_MOTORS], uint8_t input)
{
    return g_mix[0][input] * v[0] + g_mix[1][input] * v[1] + g_mix[2][input] * v[2] + g_mix[3][input] * v[3];
}

#define MIX_PWM_MIN   2000.0f   /* BSP/pwm.h Motor_PWM_ZERO */
#define MIX_PWM_MAX   4000.0f   /* BSP/pwm.h Motor_PWM_MAX */
#define MIX_PER_MOTOR 0.25f     /* deficit per axis = mean over the 4 motors */

static float motor_cut(float m)
{
    if (m > MIX_PWM_MAX) return m - MIX_PWM_MAX;
    if (m < MIX_PWM_MIN) return m - MIX_PWM_MIN;
    return 0.0f;
}

void Mix_SatDeficit(float thr, float u_pitch, float u_roll, float u_yaw, float yaw_dir, float def[4])
{
    float d[MIX_MOTORS];
    uint8_t i;

    for (i = 0U; i < MIX_MOTORS; i++) {
        d[i] = motor_cut(Mix_Motor(i, thr, u_pitch, u_roll, u_yaw));
    }
    def[CTRL_AXIS_ROLL]  = MIX_PER_MOTOR * Mix_Column(d, MIX_ROLL);
    def[CTRL_AXIS_PITCH] = -MIX_PER_MOTOR * Mix_Column(d, MIX_PITCH);   /* u_gyroy = -pitch */
    def[CTRL_AXIS_YAW]   = MIX_PER_MOTOR * yaw_dir * Mix_Column(d, MIX_YAW);
    def[CTRL_AXIS_Z]     = MIX_PER_MOTOR * Mix_Column(d, MIX_THR);
}

static void none_reset(void) { }
static float none_correction(uint8_t axis) { (void)axis; return 0.0f; }

/* MRAC_Control() still runs every cycle in Compute_Motor (it learns in shadow mode too);
 * this entry only decides whether its u_ad reaches the mixer. */
static float mrac_correction(uint8_t axis)
{
#if ENABLE_MRAC_OUTPUT_INJECTION == 1
    float u;
    if (!mrac_flags.output_injection_on) return 0.0f;
    switch (axis) {
    case CTRL_AXIS_PITCH: u = mrac_state.pitch.u_ad  * mrac_config_pitch.mrac_to_mixer; break;
    case CTRL_AXIS_ROLL:  u = mrac_state.roll.u_ad   * mrac_config_roll.mrac_to_mixer;  break;
    case CTRL_AXIS_YAW:   u = mrac_state.yaw.u_ad    * mrac_config_yaw.mrac_to_mixer;   break;
    case CTRL_AXIS_Z:     u = mrac_state.z_rate.u_ad * mrac_config_z.mrac_to_mixer;     break;
    default:              return 0.0f;
    }
    return u * mrac_simplex.fade * mrac_inj.inj_alpha;
#else
    (void)axis;
    return 0.0f;
#endif
}

static const ctrl_ops_t controllers[CTRL_MAX] = {
    { none_reset, none_correction, 1 },   /* CTRL_PID */
    { MRAC_Reset, mrac_correction, 1 },  /* CTRL_MRAC */
    { none_reset, none_correction, 0 },   /* CTRL_MRAC_STRUCT (reserved) */
    { none_reset, none_correction, 0 },   /* CTRL_MRAC_RBF (reserved) */
    { none_reset, none_correction, 0 }    /* CTRL_3LAYER (reserved) */
};

void Controller_Init(void)
{
    /* MRAC_Init populates mrac_config_* and must run even when PID is selected: MRAC_Control runs regardless. */
    MRAC_Init();
}

#if defined(__CC_ARM) && MRAC_ENABLE_SATAWARE == 1
/* MRAC V2 saturation deficit (WP-27). Set_PWM_Motors clamps mymotor in place, so the commanded motors are
 * rebuilt from the mixer inputs of the previous tick (TASK/StabilizerTask.c Mix_Compute, the same g_mix rows)
 * and the part cut by the [2000, 4000] clamp (BSP/pwm.h Motor_PWM_ZERO/MAX) is projected back per axis, in the
 * MRAC units of u_nom (mixer units / mrac_to_mixer). The law uses |u_def| only (API/mrac.c), so signs per axis
 * do not matter, only which motors feed which axis. The math is Mix_SatDeficit (host-tested in
 * API/tests/test_mixer.c); this wrapper reads the firmware globals and writes u_def. */
extern float Throttle_out, u_gyrox, u_gyroy, u_gyroz;
extern volatile float g_yaw_mix_dir;

static void mrac_mixer_deficit(void)
{
    float def[4];
    float u;

    Mix_SatDeficit(Throttle_out, u_gyroy, u_gyrox, g_yaw_mix_dir * u_gyroz, g_yaw_mix_dir, def);
    u = def[CTRL_AXIS_ROLL] / mrac_config_roll.mrac_to_mixer;
    mrac_state.roll.u_def = (u - u == 0.0f) ? u : 0.0f;
    u = def[CTRL_AXIS_PITCH] / mrac_config_pitch.mrac_to_mixer;
    mrac_state.pitch.u_def = (u - u == 0.0f) ? u : 0.0f;
    u = def[CTRL_AXIS_YAW] / mrac_config_yaw.mrac_to_mixer;
    mrac_state.yaw.u_def = (u - u == 0.0f) ? u : 0.0f;
    u = def[CTRL_AXIS_Z] / mrac_config_z.mrac_to_mixer;
    mrac_state.z_rate.u_def = (u - u == 0.0f) ? u : 0.0f;
}
#endif

/* Runs once per tick before MRAC_Control (TASK/StabilizerTask.c Compute_Motor). */
void Controller_CheckSwitch(uint8_t armed)
{
    uint8_t req = g_ctrl_select_req;
#if defined(__CC_ARM) && MRAC_ENABLE_SATAWARE == 1
    mrac_mixer_deficit();   /* V2 input; read by the law only while mu_sat > 0 */
#endif
    if (req == g_ctrl_select || armed) return;      /* armed: request stays pending until disarm */
    if (req >= CTRL_MAX || !controllers[req].available) {
        g_ctrl_select_req = g_ctrl_select;          /* refuse, visibly */
        return;
    }
    controllers[req].reset();
    g_ctrl_select = req;
}

float Controller_Update(uint8_t axis, float u_nom)
{
    uint8_t sel = g_ctrl_select;
    float c;
    if (axis > CTRL_AXIS_Z || sel >= CTRL_MAX || !(g_ctrl_axis_mask & (1U << axis))) return u_nom;
    c = controllers[sel].correction(axis);
    if (!(c - c == 0.0f)) return u_nom;             /* NaN/Inf correction: PID baseline only */
    return u_nom + c;
}
