import sys
import re

with open("API/tests/test_mrac_equiv.c", "r") as f:
    content = f.read()

pattern = r"(mrac_inj\.inj_alpha = 1\.0f;)"
replacement = r"mrac_inj.inj_alpha = mrac_flags.output_injection_on ? 1.0f : 0.0f;"

content = re.sub(pattern, replacement, content)

with open("API/tests/test_mrac_equiv.c", "w") as f:
    f.write(content)
