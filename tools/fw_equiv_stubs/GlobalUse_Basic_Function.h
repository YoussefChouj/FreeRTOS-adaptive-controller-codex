/* Case alias for host gcc (tools/fw_equiv.py, tools/fw_trace.py; never built for the target). API/pid.h, BSP/pwm.h,
   TASK/RemoterTask.h, TASK/send_data.h and Global_file/globaluse_basic_function.c include "GlobalUse_Basic_Function.h"
   but the file is Global_file/globaluse_basic_function.h. Keil and MinGW on Windows ignore case; a case-sensitive
   filesystem (Linux) does not. This directory is first on the include path, so #include_next picks up the real header
   from the tree being built (base or new), on either filesystem. */
#include_next "globaluse_basic_function.h"
