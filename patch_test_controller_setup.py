import sys
import re

with open("tests/firmware_host/test_controller.c", "r") as f:
    content = f.read()

content = content.replace("mrac_simplex.fade = 0.5f;", "mrac_simplex.fade = 0.5f;\n    mrac_inj.inj_alpha = 1.0f;")

with open("tests/firmware_host/test_controller.c", "w") as f:
    f.write(content)
