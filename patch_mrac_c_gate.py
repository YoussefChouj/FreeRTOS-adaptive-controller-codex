import sys

with open("API/mrac.c", "r") as f:
    content = f.read()

injection_code = """
MRAC_Inj_t mrac_inj = {0};
volatile uint8_t mrac_in_armed = 0;
volatile uint8_t mrac_in_phase = 0;

void MRAC_GateStep(void)
{
    uint8_t is_flying = (mrac_in_phase == 1 /* FLYING */ || mrac_in_phase == 2 /* LANDING */);
    
    // 1. fly_ticks
    if (mrac_in_armed && is_flying) {
        if (mrac_inj.fly_ticks < 65535) {
            mrac_inj.fly_ticks++;
        }
        mrac_inj.not_flying_ticks = 0;
    } else {
        if (mrac_inj.not_flying_ticks < MRAC_FLY_HYST_TICKS) {
            mrac_inj.not_flying_ticks++;
        } else {
            mrac_inj.fly_ticks = 0;
        }
    }
    
    // 2. basic learn_gate
    uint8_t basic_gate = (mrac_in_armed && is_flying && 
                          mrac_inj.fly_ticks >= (uint16_t)(MRAC_LEARN_HOLD_S / 0.005f));
                          
    // 3. Disarm edge, or phase LANDED / GROUND_IDLE
    uint8_t disarm_edge = (mrac_inj.prev_armed && !mrac_in_armed);
    if (disarm_edge || !is_flying) {
        mrac_inj.inj_alpha = 0.0f;
        mrac_inj.ramp_p = 0.0f;
        mrac_inj.learn_gate = 0;
        if (disarm_edge) {
            MRAC_ResetWeights();
            mrac_inj.freeze_shadow = 0; // reset freeze on disarm
        }
    }
    mrac_inj.prev_armed = mrac_in_armed;
    
    uint8_t inj_on = mrac_flags.output_injection_on;
    
    // 4. Injection rising edge
    if (inj_on && !mrac_inj.prev_injection_on) {
        MRAC_ResetWeights();
        mrac_inj.ramp_p = 0.0f;
        mrac_inj.inj_alpha = 0.0f;
        mrac_inj.freeze_shadow = 0;
    }
    
    // 5 & 6. Ramp and freeze logic
    if (inj_on) {
        if (basic_gate) {
            mrac_inj.ramp_p += 0.005f / MRAC_INJ_T_UP;
            if (mrac_inj.ramp_p > 1.0f) mrac_inj.ramp_p = 1.0f;
            float p = mrac_inj.ramp_p;
            mrac_inj.inj_alpha = 3.0f * p * p - 2.0f * p * p * p;
        } else {
            // "If injection is on but the gate is closed, alpha stays 0 and the ramp starts when the gate opens."
            mrac_inj.ramp_p = 0.0f;
            mrac_inj.inj_alpha = 0.0f;
        }
        mrac_inj.learn_gate = basic_gate;
    } else {
        if (mrac_inj.prev_injection_on) {
            // falling edge just happened, we're ramping down
        }
        
        if (mrac_inj.inj_alpha > 0.0f) {
            mrac_inj.inj_alpha -= 0.005f / MRAC_INJ_T_DN;
            if (mrac_inj.inj_alpha <= 0.0f) {
                mrac_inj.inj_alpha = 0.0f;
                // "when it reaches 0 learning freezes"
                mrac_inj.freeze_shadow = 1;
            }
        } else {
            // If it was already 0 and we toggle off, freeze immediately
            if (mrac_inj.prev_injection_on) {
                mrac_inj.freeze_shadow = 1;
            }
        }
        
        mrac_inj.ramp_p = 0.0f; // reset p for future
        
        // Reconcile: shadow mode keeps learning with the gate, UNLESS we explicitly froze it by toggling injection off.
        mrac_inj.learn_gate = basic_gate && !mrac_inj.freeze_shadow;
    }
    
    // Safety clamp, though logic above should handle it
    if (disarm_edge || !is_flying) {
        mrac_inj.learn_gate = 0;
        mrac_inj.inj_alpha = 0.0f;
    }
    
    mrac_inj.prev_injection_on = inj_on;
}

"""

import re
content = content.replace("MRAC_Simplex_t mrac_simplex = {0, 0, 0, 0, 0, 0, {0, 0, 0, 0}, 0, 200, 40,\n                               3.14f, 3.14f, 1.0e6f, 1.0f};\n", "MRAC_Simplex_t mrac_simplex = {0, 0, 0, 0, 0, 0, {0, 0, 0, 0}, 0, 200, 40,\n                               3.14f, 3.14f, 1.0e6f, 1.0f};\n\n" + injection_code)

with open("API/mrac.c", "w") as f:
    f.write(content)
