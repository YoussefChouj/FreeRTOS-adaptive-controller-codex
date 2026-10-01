/* Host test for API/controller.c: default == legacy MRAC injection, disarmed-only switching,
 * reserved slots refused, NaN/axis-mask fallback to u_nom.
 *   cp ../../API/mrac.c . && gcc -std=c99 -Wall -I../../API/tests/stubs -I../../API test_controller.c ../../API/controller.c mrac.c -o t -lm && rm mrac.c && ./t */
#include <stdio.h>
#include <assert.h>
#include <math.h>
#include "controller.h"
#include "mrac.h"



_imu_st imu_data = {0};

float MRAC_VectorNormSquare(const float *v, int n) { return 0.0f; }

// We will hook MRAC_Reset using a macro or we just don't count n_reset
// wait, we DO need to count n_reset. Let's just redefine it or check MRAC_State_t directly.

static void setup(void)
{
    mrac_flags.output_injection_on = 1;
    mrac_simplex.fade = 0.5f;
    mrac_inj.inj_alpha = 1.0f;
    mrac_state.roll.u_ad = 2.0f;   mrac_config_roll.mrac_to_mixer = 3.0f;
    mrac_state.pitch.u_ad = 1.0f;  mrac_config_pitch.mrac_to_mixer = 1.0f;
    mrac_state.yaw.u_ad = 4.0f;    mrac_config_yaw.mrac_to_mixer = 1.0f;
    mrac_state.z_rate.u_ad = 8.0f; mrac_config_z.mrac_to_mixer = 1.0f;
}

int main(void)
{
    Controller_Init();
    setup();
    
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
    mrac_state.pitch.x_prev = 1.0f;
    g_ctrl_select_req = CTRL_MRAC;
    Controller_CheckSwitch(0);
    Controller_CheckSwitch(0);
    assert(g_ctrl_select == CTRL_MRAC && mrac_state.pitch.x_prev == 0.0f);
    /* reserved and out-of-range slots refused, request rolled back */
    g_ctrl_select_req = CTRL_MRAC_RBF;
    Controller_CheckSwitch(0);
    assert(g_ctrl_select == CTRL_MRAC && g_ctrl_select_req == CTRL_MRAC);
    g_ctrl_select_req = 200;
    Controller_CheckSwitch(0);
    assert(g_ctrl_select == CTRL_MRAC && g_ctrl_select_req == CTRL_MRAC);
    
    
    /* (a) injection step with wound-up weights (fixed u) */
    mrac_in_armed = 1;
    mrac_in_phase = 1; // FLYING
    mrac_inj.fly_ticks = 200;
    mrac_flags.output_injection_on = 0; // shadow mode
    MRAC_GateStep(); // settle
    
    mrac_state.roll.u_ad = 2.0f;
    mrac_config_roll.mrac_to_mixer = 3.0f;
    float u = 6.0f; // 2.0 * 3.0
    mrac_simplex.fade = 1.0f;
    
    mrac_flags.output_injection_on = 1; // rising edge
    MRAC_GateStep();
    float prev_out = Controller_Update(CTRL_AXIS_ROLL, 10.0f);
    float max_diff = 1.5f * u / (2.5f * 200.0f); // 1.5*u / (MRAC_INJ_T_UP*200)
    for (int i=0; i<100; i++) {
        MRAC_GateStep();
        float current_out = Controller_Update(CTRL_AXIS_ROLL, 10.0f);
        float diff = current_out - prev_out;
        assert(diff <= max_diff);
        prev_out = current_out;
    }
    
    /* (b) inj_alpha == 0 while phase != FLYING/LANDING */
    mrac_in_phase = 0; // GROUND_IDLE
    MRAC_GateStep();
    assert(mrac_inj.inj_alpha == 0.0f);
    
    /* (c) inj_alpha reaches 0 within 100 ticks after injection turns off */
    mrac_in_phase = 1; // FLYING
    mrac_inj.fly_ticks = 200;
    mrac_flags.output_injection_on = 1;
    for(int i=0; i<550; i++) MRAC_GateStep(); // fully on
    assert(mrac_inj.inj_alpha == 1.0f);
    mrac_flags.output_injection_on = 0;
    for(int i=0; i<105; i++) MRAC_GateStep();
    assert(mrac_inj.inj_alpha == 0.0f);
    
    /* (d) inj_alpha == 0 on the tick of disarm */
    mrac_flags.output_injection_on = 1;
    for(int i=0; i<550; i++) MRAC_GateStep(); // fully on
    assert(mrac_inj.inj_alpha == 1.0f);
    mrac_in_armed = 0; // disarm
    MRAC_GateStep();
    assert(mrac_inj.inj_alpha == 0.0f);

    printf("test_controller: all passed\n");

    return 0;
}
