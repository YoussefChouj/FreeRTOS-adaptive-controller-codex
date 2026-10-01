import sys
import re

with open("tests/firmware_host/test_controller.c", "r") as f:
    content = f.read()

pattern = r"\*   gcc -std=c99 -Wall -Werror -Istubs -I../../API test_controller.c ../../API/controller.c -o t && \./t \*/"
replacement = r"*   cp ../../API/mrac.c . && gcc -std=c99 -Wall -Werror -I../../API/tests/stubs -I../../API test_controller.c ../../API/controller.c mrac.c -o t -lm && rm mrac.c && ./t */"

content = re.sub(pattern, replacement, content)

with open("tests/firmware_host/test_controller.c", "w") as f:
    f.write(content)
