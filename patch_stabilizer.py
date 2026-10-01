import sys
import re

with open("TASK/StabilizerTask.c", "r") as f:
    content = f.read()

pattern = r"(Controller_CheckSwitch\(DroneStatus\.ARM_Status == Armed\);\s*)(MRAC_Control\(&Ctrler\);)"
replacement = r"\1mrac_in_armed = (DroneStatus.ARM_Status == Armed) ? 1U : 0U;\n\tmrac_in_phase = (uint8_t)flight_phase;\n\t\2"

content = re.sub(pattern, replacement, content)

with open("TASK/StabilizerTask.c", "w") as f:
    f.write(content)
