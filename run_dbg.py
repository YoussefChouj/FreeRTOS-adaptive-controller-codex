import subprocess

code = """
#include <stdio.h>
#include "controller.h"
#include "mrac.h"
int main() {
    printf("Test ran\n");
    return 0;
}
"""
with open("tests/firmware_host/test_dbg.c", "w") as f:
    f.write(code)

