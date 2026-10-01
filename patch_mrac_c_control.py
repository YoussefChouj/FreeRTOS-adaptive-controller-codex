import sys
import re

with open("API/mrac.c", "r") as f:
    content = f.read()

# Replace MRAC_Control call to include MRAC_GateStep
pattern = re.compile(r"(void MRAC_Control\(const CtrlerTypeDef\* current_state\)\n\{\n.*?)(MRAC_SimplexStep\(\);)", re.DOTALL)
replacement = r"\1MRAC_GateStep();\n    \2"

content = pattern.sub(replacement, content)

with open("API/mrac.c", "w") as f:
    f.write(content)
