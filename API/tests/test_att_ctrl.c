#include "att_ctrl.h"
#include <stdio.h>
#include <assert.h>
#include <math.h>
#include "att_ctrl_gains.h"

extern uint8_t flight_phase;
extern volatile uint8_t s_alt_valid_tick;
extern volatile uint8_t g_ctrl_pid_fallback_active;

uint8_t flight_phase = 0;
volatile uint8_t s_alt_valid_tick = 1;
volatile uint8_t g_ctrl_pid_fallback_active = 0;
float real_voltage = 16.8f;

int main() {
    AttCtrlIn in = {0};
    float u_out;

    AttCtrl_ResetAll();
    AttCtrl_TickBegin();

    in.r = 0.1f;
    in.y = 0.0f;
    in.w = 0.0f;

    // OFF mode
    g_att_mode[ATT_ROLL] = ATT_MODE_OFF;
    u_out = AttCtrl_Apply(ATT_ROLL, 150.0f, &in);
    assert(u_out == 150.0f);

    // SHADOW mode (bit exact pass-through)
    g_att_mode[ATT_ROLL] = ATT_MODE_SHADOW;
    u_out = AttCtrl_Apply(ATT_ROLL, 120.0f, &in);
    assert(u_out == 120.0f);

    // Latch logic testing - Rate out of bounds
    in.w = 400.0f * 3.14159f / 180.0f;
    u_out = AttCtrl_Apply(ATT_ROLL, 100.0f, &in);
    assert(g_att_would_latch[ATT_ROLL] == LATCH_RATE_BOUND);

    // Switch to active
    g_att_mode[ATT_ROLL] = ATT_MODE_ACTIVE;
    g_att_alpha[ATT_ROLL] = 1.0f;
    AttCtrl_ResetAll(); // clear latch
    in.w = 0.0f;
    u_out = AttCtrl_Apply(ATT_ROLL, 100.0f, &in);
    assert(u_out <= 100.0f);

    // Test LQR
    g_att_mode[ATT_ROLL] = ATT_CTRL_LQR;
    g_att_alpha[ATT_ROLL] = 1.0f;
    AttCtrl_ResetAll();
    u_out = AttCtrl_Apply(ATT_ROLL, 100.0f, &in);

    printf("att_ctrl tests pass\n");
    return 0;
}
