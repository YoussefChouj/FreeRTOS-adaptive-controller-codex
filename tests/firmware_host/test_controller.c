/* Host test for API/controller.c: default == legacy MRAC injection, disarmed-only switching,
 * reserved slots refused, NaN/axis-mask fallback to u_nom.
 *   gcc -std=c99 -Wall -Werror -Istubs -I../../API test_controller.c ../../API/controller.c -o t && ./t */
#include <stdio.h>
#include <assert.h>
#include <math.h>
#include "controller.h"
#include "mrac.h"

MRAC_State_t mrac_state;
MRAC_FeatureFlags_t mrac_flags;
MRAC_Simplex_t mrac_simplex;
MRAC_AxisConfig_t mrac_config_pitch, mrac_config_roll, mrac_config_yaw, mrac_config_z;
static int n_init, n_reset;
void MRAC_Init(void) { n_init++; }
void MRAC_Reset(void) { n_reset++; }
void MRAC_Control(const CtrlerTypeDef *s) { (void)s; }

static void setup(void)
{
    mrac_flags.output_injection_on = 1;
    mrac_simplex.fade = 0.5f;
    mrac_state.roll.u_ad = 2.0f;   mrac_config_roll.mrac_to_mixer = 3.0f;
    mrac_state.pitch.u_ad = 1.0f;  mrac_config_pitch.mrac_to_mixer = 1.0f;
    mrac_state.yaw.u_ad = 4.0f;    mrac_config_yaw.mrac_to_mixer = 1.0f;
    mrac_state.z_rate.u_ad = 8.0f; mrac_config_z.mrac_to_mixer = 1.0f;
}

int main(void)
{
    setup();
    Controller_Init();
    assert(n_init == 1);
    /* default: MRAC, u = u_nom + u_ad * to_mixer * fade */
    assert(g_ctrl_select == CTRL_MRAC);
    assert(Controller_Update(CTRL_AXIS_ROLL, 10.0f) == 13.0f);
    assert(Controller_Update(CTRL_AXIS_PITCH, 10.0f) == 10.5f);
    assert(Controller_Update(CTRL_AXIS_YAW, 10.0f) == 12.0f);
    assert(Controller_Update(CTRL_AXIS_Z, 10.0f) == 14.0f);
    assert(Controller_Update(7, 10.0f) == 10.0f);
    /* runtime shadow mode */
    mrac_flags.output_injection_on = 0;
    assert(Controller_Update(CTRL_AXIS_ROLL, 10.0f) == 10.0f);
    mrac_flags.output_injection_on = 1;
    /* non-finite correction dropped */
    mrac_state.roll.u_ad = NAN;
    assert(Controller_Update(CTRL_AXIS_ROLL, 10.0f) == 10.0f);
    mrac_state.roll.u_ad = INFINITY;
    assert(Controller_Update(CTRL_AXIS_ROLL, 10.0f) == 10.0f);
    mrac_state.roll.u_ad = 2.0f;
    /* axis mask */
    g_ctrl_axis_mask = 0x0F & ~(1U << CTRL_AXIS_ROLL);
    assert(Controller_Update(CTRL_AXIS_ROLL, 10.0f) == 10.0f);
    assert(Controller_Update(CTRL_AXIS_YAW, 10.0f) == 12.0f);
    g_ctrl_axis_mask = 0x0F;
    /* armed: request stays pending */
    g_ctrl_select_req = CTRL_PID;
    Controller_CheckSwitch(1);
    assert(g_ctrl_select == CTRL_MRAC && g_ctrl_select_req == CTRL_PID);
    /* disarmed: applied, PID adds nothing */
    Controller_CheckSwitch(0);
    assert(g_ctrl_select == CTRL_PID);
    assert(Controller_Update(CTRL_AXIS_ROLL, 10.0f) == 10.0f);
    /* back to MRAC resets weights exactly once */
    n_reset = 0;
    g_ctrl_select_req = CTRL_MRAC;
    Controller_CheckSwitch(0);
    Controller_CheckSwitch(0);
    assert(g_ctrl_select == CTRL_MRAC && n_reset == 1);
    /* reserved and out-of-range slots refused, request rolled back */
    g_ctrl_select_req = CTRL_MRAC_RBF;
    Controller_CheckSwitch(0);
    assert(g_ctrl_select == CTRL_MRAC && g_ctrl_select_req == CTRL_MRAC);
    g_ctrl_select_req = 200;
    Controller_CheckSwitch(0);
    assert(g_ctrl_select == CTRL_MRAC && g_ctrl_select_req == CTRL_MRAC);
    assert(n_init == 1);
    printf("test_controller: all passed\n");
    return 0;
}
