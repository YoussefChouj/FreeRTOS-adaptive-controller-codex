import sys
import re

with open("API/mrac.c", "r") as f:
    content = f.read()

pattern = r"if \(mrac_flags\.adaptation_on && do_adaptation\) \{"
replacement = r"""if (mrac_flags.adaptation_on && do_adaptation && mrac_inj.learn_gate) {
        float theta_scale = mrac_flags.output_injection_on ? mrac_inj.inj_alpha : 1.0f;"""

content = re.sub(pattern, replacement, content)

pattern2 = r"state->Theta\[i\] \+= MRAC_DT \* y;"
replacement2 = r"state->Theta[i] += MRAC_DT * y * theta_scale;"

content = re.sub(pattern2, replacement2, content)

with open("API/mrac.c", "w") as f:
    f.write(content)
