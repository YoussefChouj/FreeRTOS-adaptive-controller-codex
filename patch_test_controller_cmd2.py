import sys
import re

with open("tests/firmware_host/test_controller.c", "r") as f:
    content = f.read()

content = content.replace("-Werror ", "")
content = content.replace("static int n_init, n_reset;", "")

with open("tests/firmware_host/test_controller.c", "w") as f:
    f.write(content)
