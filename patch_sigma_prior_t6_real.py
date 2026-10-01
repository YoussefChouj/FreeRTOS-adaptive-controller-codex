import sys

with open("API/tests/test_mrac_sigma_prior.c", "r") as f:
    content = f.read()

t6_code = """
    /* Test 6: gate case: with mrac_in_phase = GROUND_IDLE (armed=1) and e = 0.5, 
     * Theta stays bit-unchanged for 1000 MRAC_Control ticks. */
    {
        reset_state();
        mrac_in_armed = 1;
        mrac_in_phase = 0; // GROUND_IDLE
        mrac_inj.fly_ticks = 200;
        mrac_inj.learn_gate = 0; // Because it's on the ground, but let's let GateStep handle it.
        // Wait, reset_state() opens the gate now! 
        // I need to explicitly close it by doing the ground phase logic:
        mrac_in_phase = 0;
        
        float theta_before[MAX_NUM_BASIS];
        for (i = 0; i < MAX_NUM_BASIS; i++) {
            theta_before[i] = mrac_state.pitch.Theta[i];
        }
        
        drive_zero_steady_state(1000);
        
        for (i = 0; i < MAX_NUM_BASIS; i++) {
            char msg[80];
            sprintf(msg, "T6 gate closed Theta[%d]", i);
            okv_close(msg, mrac_state.pitch.Theta[i], theta_before[i], 0.0);
        }
    }
"""

content = content.replace('printf("\\n%d checks, %d failure(s)\\n", checks, fails);', t6_code + '\n    printf("\\n%d checks, %d failure(s)\\n", checks, fails);')

with open("API/tests/test_mrac_sigma_prior.c", "w") as f:
    f.write(content)
