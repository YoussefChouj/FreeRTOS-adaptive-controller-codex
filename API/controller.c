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
    return u * mrac_simplex.fade;
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

void Controller_CheckSwitch(uint8_t armed)
{
    uint8_t req = g_ctrl_select_req;
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
