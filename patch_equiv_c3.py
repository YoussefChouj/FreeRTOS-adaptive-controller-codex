import sys
import re

with open("API/tests/test_mrac_equiv.c", "r") as f:
    content = f.read()

# I will move the MRAC_EQUIV_NEW_TREE block to AFTER the mrac_flags modifications.
# mrac_flags are modified inside "if (scn == 1 || scn == 8 || scn == 10) { ... }"

content = content.replace("#ifdef MRAC_EQUIV_NEW_TREE\n    mrac_in_armed = 1;\n    mrac_in_phase = 1; // FLYING\n    mrac_inj.learn_gate = 1;\n    mrac_inj.inj_alpha = 1.0f;\n    mrac_inj.fly_ticks = 200;\n#endif\n", "")

pattern = r"(g_lcg = 123456789U \+ \(uint32_t\)scn \* 10007U;)"
replacement = r"""\1
#ifdef MRAC_EQUIV_NEW_TREE
    mrac_in_armed = 1;
    mrac_in_phase = 1; // FLYING
    mrac_inj.learn_gate = 1;
    mrac_inj.inj_alpha = 1.0f;
    mrac_inj.ramp_p = 1.0f;
    mrac_inj.fly_ticks = 200;
    mrac_inj.prev_injection_on = mrac_flags.output_injection_on;
#endif
"""

content = re.sub(pattern, replacement, content)

with open("API/tests/test_mrac_equiv.c", "w") as f:
    f.write(content)
