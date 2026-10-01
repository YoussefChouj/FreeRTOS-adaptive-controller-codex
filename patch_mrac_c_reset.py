import sys

with open("API/mrac.c", "r") as f:
    content = f.read()

replacement = """void MRAC_ResetWeights(void)
{
    int i;
    // Force reference models to snap to current plant states, reset weights.
    // Useful during mid-flight mode switches or disarms safely.
    for (i = 0; i < MAX_NUM_BASIS; i++) {
        mrac_state.pitch.Theta[i] = 0.0f;
        mrac_state.roll.Theta[i]  = 0.0f;
        mrac_state.yaw.Theta[i]   = 0.0f;
        mrac_state.z_rate.Theta[i]= 0.0f;
        mrac_state.pitch.Whatf[i] = 0.0f;
        mrac_state.roll.Whatf[i]  = 0.0f;
        mrac_state.yaw.Whatf[i]   = 0.0f;
        mrac_state.z_rate.Whatf[i]= 0.0f;
    }
    
    // Snap references to plant state (bumpless) and zero 2nd-order velocity states
    mrac_state.pitch.xm = mrac_state.pitch.x;   mrac_state.pitch.xm_dot  = 0.0f;
    mrac_state.roll.xm  = mrac_state.roll.x;    mrac_state.roll.xm_dot   = 0.0f;
    mrac_state.yaw.xm   = mrac_state.yaw.x;     mrac_state.yaw.xm_dot    = 0.0f;
    mrac_state.z_rate.xm= mrac_state.z_rate.x;  mrac_state.z_rate.xm_dot = 0.0f;

    // Zero the rate-derivative estimator (ADR-0007) so e_dot starts clean post-reset.
    mrac_state.pitch.x_prev = mrac_state.pitch.x;   mrac_state.pitch.xdot_f  = 0.0f; mrac_state.pitch.e_dot  = 0.0f;
    mrac_state.roll.x_prev  = mrac_state.roll.x;    mrac_state.roll.xdot_f   = 0.0f; mrac_state.roll.e_dot   = 0.0f;
    mrac_state.yaw.x_prev   = mrac_state.yaw.x;     mrac_state.yaw.xdot_f    = 0.0f; mrac_state.yaw.e_dot    = 0.0f;
    mrac_state.z_rate.x_prev= mrac_state.z_rate.x;  mrac_state.z_rate.xdot_f = 0.0f; mrac_state.z_rate.e_dot = 0.0f;
}

void MRAC_Reset(void)
{
    MRAC_ResetWeights();
}"""

# Find the start of void MRAC_Reset(void) and its body and replace it.
import re
pattern = re.compile(r"void MRAC_Reset\(void\)\n\{.*?\n\}", re.DOTALL)
content = pattern.sub(replacement, content)

with open("API/mrac.c", "w") as f:
    f.write(content)
