import sys

with open("tests/firmware_host/test_controller.c", "r") as f:
    content = f.read()

content = content.replace("    setup();\n    Controller_Init();", "    Controller_Init();\n    setup();")
content = content.replace('printf("Update returned %f\\n", Controller_Update(CTRL_AXIS_ROLL, 10.0f)); ', "")

with open("tests/firmware_host/test_controller.c", "w") as f:
    f.write(content)
