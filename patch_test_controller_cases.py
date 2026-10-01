import sys
import re

with open("tests/firmware_host/test_controller.c", "r") as f:
    content = f.read()

cases_code = """
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
    for(int i=0; i<500; i++) MRAC_GateStep(); // fully on
    assert(mrac_inj.inj_alpha == 1.0f);
    mrac_flags.output_injection_on = 0;
    for(int i=0; i<100; i++) MRAC_GateStep();
    assert(mrac_inj.inj_alpha == 0.0f);
    
    /* (d) inj_alpha == 0 on the tick of disarm */
    mrac_flags.output_injection_on = 1;
    for(int i=0; i<500; i++) MRAC_GateStep(); // fully on
    assert(mrac_inj.inj_alpha == 1.0f);
    mrac_in_armed = 0; // disarm
    MRAC_GateStep();
    assert(mrac_inj.inj_alpha == 0.0f);

    printf("test_controller: all passed\\n");
"""

content = content.replace('printf("test_controller: all passed\\n");', cases_code)

with open("tests/firmware_host/test_controller.c", "w") as f:
    f.write(content)
