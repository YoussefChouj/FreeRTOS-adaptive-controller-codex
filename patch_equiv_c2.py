import sys
import re

with open("API/tests/test_mrac_equiv.c", "r") as f:
    content = f.read()

pattern = r"(mrac_inj\.inj_alpha = 1\.0f;)"
replacement = r"\1\n    mrac_inj.fly_ticks = 200;"

content = re.sub(pattern, replacement, content)

with open("API/tests/test_mrac_equiv.c", "w") as f:
    f.write(content)
