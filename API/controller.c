#include "controller.h"
#include "mrac.h"

volatile uint8_t g_ctrl_select = CTRL_MRAC;     /* == pre-interface behaviour: PID + gated MRAC injection */
volatile uint8_t g_ctrl_select_req = CTRL_MRAC;
volatile uint8_t g_ctrl_axis_mask = 0x0F;

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
 * rebuilt from the mixer inputs of the previous tick (TASK/StabilizerTask.c:1374-1409, same signs) and the
 * part cut by the [2000, 4000] clamp (BSP/pwm.h Motor_PWM_ZERO/MAX) is projected back per axis, in the MRAC
 * units of u_nom (mixer units / mrac_to_mixer). The law uses |u_def| only (API/mrac.c), so signs per axis
 * do not matter, only which motors feed which axis. Firmware only: the host tests have no mixer. */
extern float Throttle_out, u_gyrox, u_gyroy, u_gyroz;
extern volatile float g_yaw_mix_dir;

#define MIX_PWM_MIN   2000.0f   /* BSP/pwm.h Motor_PWM_ZERO */
#define MIX_PWM_MAX   4000.0f   /* BSP/pwm.h Motor_PWM_MAX */
#define MIX_PER_MOTOR 0.25f     /* deficit per axis = mean over the 4 motors */

static float motor_cut(float m)
{
    if (m > MIX_PWM_MAX) return m - MIX_PWM_MAX;
    if (m < MIX_PWM_MIN) return m - MIX_PWM_MIN;
    return 0.0f;
}

static void mrac_mixer_deficit(void)
{
    float yz = g_yaw_mix_dir * u_gyroz;
    float d1 = motor_cut(Throttle_out - u_gyroy - u_gyrox - yz);
    float d2 = motor_cut(Throttle_out + u_gyroy + u_gyrox - yz);
    float d3 = motor_cut(Throttle_out - u_gyroy + u_gyrox + yz);
    float d4 = motor_cut(Throttle_out + u_gyroy - u_gyrox + yz);
    float u;

    u = MIX_PER_MOTOR * (-d1 + d2 + d3 - d4) / mrac_config_roll.mrac_to_mixer;
    mrac_state.roll.u_def = (u - u == 0.0f) ? u : 0.0f;
    u = -MIX_PER_MOTOR * (-d1 + d2 - d3 + d4) / mrac_config_pitch.mrac_to_mixer;   /* u_gyroy = -pitch */
    mrac_state.pitch.u_def = (u - u == 0.0f) ? u : 0.0f;
    u = MIX_PER_MOTOR * g_yaw_mix_dir * (-d1 - d2 + d3 + d4) / mrac_config_yaw.mrac_to_mixer;
    mrac_state.yaw.u_def = (u - u == 0.0f) ? u : 0.0f;
    u = MIX_PER_MOTOR * (d1 + d2 + d3 + d4) / mrac_config_z.mrac_to_mixer;
    mrac_state.z_rate.u_def = (u - u == 0.0f) ? u : 0.0f;
}
#endif

/* Runs once per tick before MRAC_Control (TASK/StabilizerTask.c:1358). */
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
