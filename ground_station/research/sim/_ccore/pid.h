/* Host stand-in for API/pid.h: the same declarations, with the stub headers in this directory instead of
 * the firmware chain (API/SINS.h pulls stm32f4xx.h, bmi088_driver.h, ...). build.py compiles a byte copy
 * of API/pid.c against it. */
#ifndef __PID_H
#define __PID_H

#include "robot_types.h"
#include "global_declare.h"
#include "GlobalUse_Basic_Function.h"
#include "SINS.h"

void ComputePID(PIDTypeDef *pPID);
void ComputeYawPID(PIDTypeDef *pPID);
void Clear_Structure(void);

void ComputePID_locx(PIDTypeDef *pPID);
void ComputePID_locy(PIDTypeDef *pPID);

extern CtrlerTypeDef Ctrler;

#endif
