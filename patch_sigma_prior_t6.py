import sys

with open("API/tests/test_mrac_sigma_prior.c", "r") as f:
    content = f.read()

t6_code = """
    /* Test 6: gate case */
    {
        reset_state();
        mrac_in_phase = 0; // GROUND_IDLE
        mrac_in_armed = 1;
        mrac_flags.adaptation_on = 1;
        sigma_prior = 0.0f;
        
        // Let's set some error so it WOULD learn if it could.
        // Actually MRAC_Control uses CtrlerTypeDef *s.
        // I will just drive it via drive_zero_steady_state but pass an error.
        // Wait, drive_zero_steady_state sets error to 0.5f in its implementation!
        
        // Let's capture Theta before and after 1000 ticks
        float theta_before[MAX_NUM_BASIS];
        for (i = 0; i < MAX_NUM_BASIS; i++) {
            theta_before[i] = mrac_state.pitch.Theta[i];
        }
        
        for (int step = 0; step < 1000; step++) {
            CtrlerTypeDef ctrl;
            // The drive_zero_steady_state uses CtrlerTypeDef... Wait, let's just use it
            // I'll call drive_zero_steady_state(1000) directly.
        }
    }
"""

# Let's see how drive_zero_steady_state is implemented first!
