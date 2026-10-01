import sys
import re

with open("API/tests/test_mrac_equiv.c", "r") as f:
    content = f.read()

pattern = r"(MRAC_Init\(\);)"
replacement = r"""\1
#ifdef MRAC_EQUIV_NEW_TREE
    mrac_in_armed = 1;
    mrac_in_phase = 1; // FLYING
    mrac_inj.learn_gate = 1;
    mrac_inj.inj_alpha = 1.0f;
#endif
"""

content = re.sub(pattern, replacement, content)

with open("API/tests/test_mrac_equiv.c", "w") as f:
    f.write(content)
